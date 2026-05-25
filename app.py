"""
Flask dashboard for the Wauwatosa rental search.

Local dev: no auth, no CSRF nag (debug mode skips them).
Hosted:    set FLASK_ENV=production AND SECRET_KEY AND optionally
           BASIC_AUTH_USERNAME / BASIC_AUTH_PASSWORD to lock it down.
"""
from __future__ import annotations

import os
import secrets
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
    duplex_only = request.args.get("duplex_only") == "1"
    search = request.args.get("q", "").strip().lower()

    listings = get_listings(status=status_filter or None)

    if source_filter:
        listings = [l for l in listings if l["source"] == source_filter]
    if type_filter in ("rental", "roommate"):
        listings = [l for l in listings if l.get("listing_type") == type_filter]
    if max_rent:
        listings = [l for l in listings if l["rent"] and l["rent"] <= max_rent]
    if duplex_only:
        listings = [l for l in listings if l["duplex_flag"]]
    if search:
        listings = [
            l for l in listings
            if search in (l["title"] or "").lower()
            or search in (l["neighborhood"] or "").lower()
            or search in (l["description"] or "").lower()
        ]

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
            "duplex_only": duplex_only,
            "q": search,
        },
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    debug = not _is_production
    app.run(debug=debug, port=port)
