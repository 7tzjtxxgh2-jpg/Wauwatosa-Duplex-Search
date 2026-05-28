"""
Flask dashboard for the Wauwatosa rental search.

Local dev: no auth, no CSRF nag (debug mode skips them).
Hosted:    set FLASK_ENV=production AND SECRET_KEY AND optionally
           BASIC_AUTH_USERNAME / BASIC_AUTH_PASSWORD to lock it down.
"""
from __future__ import annotations

import os
import json
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, request, jsonify, Response
from flask_wtf.csrf import CSRFProtect, generate_csrf
from dotenv import load_dotenv

from db import (
    init_db,
    get_listings,
    get_listing,
    get_sources,
    get_status_counts,
    get_type_counts,
    update_status,
    update_notes,
)

load_dotenv()

VALID_STATUSES = {"new", "interested", "touring_applying", "passed"}

# A listing from an auto-scraped source that hasn't been re-seen in this many
# days is probably gone (rented/removed). Manually-added sources (Facebook,
# Nextdoor) are never re-scraped, so staleness there is informational only.
STALE_DAYS = 21


def _json_list(raw) -> list:
    """Decode a JSON-array string column into a Python list; tolerate null/bad data."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _days_since(dt_str: str | None) -> int | None:
    """Whole days since a SQLite CURRENT_TIMESTAMP value (UTC), or None."""
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except ValueError:
            continue
    return None


def _is_auto_scraped(source: str) -> bool:
    """Auto-scraped sources get re-confirmed each run; manual ones don't."""
    return not (source or "").startswith(("facebook", "nextdoor"))

app = Flask(__name__)

# ---- SECRET_KEY enforcement ----------------------------------------------
# In production, require an explicit SECRET_KEY. In dev/debug, autogenerate.
_is_production = os.getenv("FLASK_ENV") == "production"
_secret = os.getenv("SECRET_KEY")
if _is_production and not _secret:
    raise RuntimeError(
        "SECRET_KEY env var is required when FLASK_ENV=production. "
        "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
    )
app.secret_key = _secret or secrets.token_hex(32)

# ---- CSRF protection -----------------------------------------------------
csrf = CSRFProtect(app)


@app.context_processor
def inject_csrf_token():
    """Make csrf_token() available in templates."""
    return {"csrf_token": generate_csrf}


# ---- Optional HTTP Basic auth --------------------------------------------
# Active only if both env vars are set. Local dev leaves them blank.
_basic_user = os.getenv("BASIC_AUTH_USERNAME")
_basic_pass = os.getenv("BASIC_AUTH_PASSWORD")


def _auth_required():
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Wauwatosa Rentals"'},
    )


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _basic_user or not _basic_pass:
            return view(*args, **kwargs)  # disabled when env unset
        auth = request.authorization
        if (
            auth is None
            or not secrets.compare_digest(auth.username or "", _basic_user)
            or not secrets.compare_digest(auth.password or "", _basic_pass)
        ):
            return _auth_required()
        return view(*args, **kwargs)
    return wrapped


# ---- DB lifecycle --------------------------------------------------------
@app.before_request
def setup():
    init_db()


# ---- Routes --------------------------------------------------------------
@app.route("/")
@require_auth
def index():
    status_filter = request.args.get("status", "")
    source_filter = request.args.get("source", "")
    type_filter = request.args.get("type", "")
    max_rent = request.args.get("max_rent", type=int)
    min_score = request.args.get("min_score", type=int)
    duplex_only = request.args.get("duplex_only") == "1"
    hide_stale = request.args.get("hide_stale") == "1"
    sort = request.args.get("sort", "score")  # "score" (default) or "newest"
    search = request.args.get("q", "").strip().lower()

    listings = get_listings(status=status_filter or None)

    # Annotate each listing with staleness (derived from last_seen)
    for l in listings:
        days = _days_since(l.get("last_seen"))
        l["days_since_seen"] = days
        l["stale"] = (
            _is_auto_scraped(l.get("source", ""))
            and days is not None
            and days >= STALE_DAYS
        )

    if source_filter:
        listings = [l for l in listings if l["source"] == source_filter]
    if type_filter in ("rental", "roommate"):
        listings = [l for l in listings if l.get("listing_type") == type_filter]
    if max_rent:
        listings = [l for l in listings if l["rent"] and l["rent"] <= max_rent]
    if min_score is not None:
        listings = [l for l in listings if (l.get("fit_score") or 0) >= min_score]
    if duplex_only:
        listings = [l for l in listings if l["duplex_flag"]]
    if hide_stale:
        listings = [l for l in listings if not l["stale"]]
    if search:
        listings = [
            l for l in listings
            if search in (l["title"] or "").lower()
            or search in (l["neighborhood"] or "").lower()
            or search in (l["description"] or "").lower()
            or search in (l.get("ai_summary") or "").lower()
        ]

    # Sort: by fit score (desc, unscored last) or by newest first
    if sort == "score":
        listings.sort(
            key=lambda l: (l.get("fit_score") if l.get("fit_score") is not None else -1),
            reverse=True,
        )
    # "newest" is already the DB default order (first_seen DESC)

    # Decode JSON concerns/highlights for template rendering
    for l in listings:
        l["concerns_list"] = _json_list(l.get("concerns"))
        l["highlights_list"] = _json_list(l.get("highlights"))

    return render_template(
        "index.html",
        listings=listings,
        sources=get_sources(),
        counts=get_status_counts(),
        type_counts=get_type_counts(),
        filters={
            "status": status_filter,
            "source": source_filter,
            "type": type_filter,
            "max_rent": max_rent,
            "min_score": min_score,
            "duplex_only": duplex_only,
            "hide_stale": hide_stale,
            "sort": sort,
            "q": search,
        },
        stale_days=STALE_DAYS,
    )


@app.route("/listings/<int:listing_id>/status", methods=["POST"])
@require_auth
def set_status(listing_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUSES:
        return jsonify({"error": "invalid status"}), 400
    try:
        get_listing(listing_id)  # raises KeyError if missing
    except KeyError:
        return jsonify({"error": "listing not found"}), 404
    update_status(listing_id, new_status)
    return jsonify({"ok": True})


@app.route("/listings/<int:listing_id>/notes", methods=["POST"])
@require_auth
def set_notes(listing_id):
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    try:
        get_listing(listing_id)
    except KeyError:
        return jsonify({"error": "listing not found"}), 404
    update_notes(listing_id, notes)
    return jsonify({"ok": True})


@app.route("/quick-add", methods=["POST"])
@require_auth
def quick_add_route():
    """Add a single pasted listing (e.g. an FB group post) and score it immediately."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    url = (data.get("url") or "").strip()
    source = (data.get("source") or "facebook_group").strip() or "facebook_group"

    if not text:
        return jsonify({"ok": False, "error": "Paste the listing text first."}), 400

    from import_listings import quick_add
    result = quick_add(text, url=url, source=source)

    if result["status"] == "skip_nonrental":
        return jsonify({"ok": False, "skipped": "non-rental",
                        "message": "Looks like a non-rental post (job, commercial, or ISO). Not added."})
    if result["status"] == "skip_geo":
        return jsonify({"ok": False, "skipped": "out-of-scope",
                        "message": "Looks out of the Wauwatosa area. Not added."})
    if result["status"] == "empty":
        return jsonify({"ok": False, "error": "Couldn't read a listing from that text."}), 400
    if result["status"] == "duplicate":
        return jsonify({"ok": True, "duplicate": True, "listing_id": result["listing_id"],
                        "message": "Already in your dashboard."})

    # New listing — score it right away so it appears ranked
    listing_id = result["listing_id"]
    try:
        from anthropic import Anthropic
        from enrich import enrich_listing
        from db import update_enrichment
        listing = get_listing(listing_id)
        fields = enrich_listing(Anthropic(), listing)
        fields.pop("_usage", None)
        update_enrichment(listing_id, fields)
        return jsonify({"ok": True, "listing_id": listing_id,
                        "fit_score": fields["fit_score"],
                        "summary": fields["ai_summary"],
                        "message": f"Added and scored {fields['fit_score']}/10."})
    except Exception as e:
        # Listing is saved even if scoring failed; it'll be picked up by enrich.py later
        return jsonify({"ok": True, "listing_id": listing_id, "unscored": True,
                        "message": f"Added (scoring will run later): {e}"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    debug = not _is_production
    app.run(debug=debug, port=port)
