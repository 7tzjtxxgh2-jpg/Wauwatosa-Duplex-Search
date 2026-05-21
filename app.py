from __future__ import annotations

import os
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from db import init_db, get_listings, update_status, update_fit_score

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")


@app.before_request
def setup():
    init_db()


@app.route("/")
def index():
    status_filter = request.args.get("status", "")
    source_filter = request.args.get("source", "")
    max_rent = request.args.get("max_rent", type=int)
    duplex_only = request.args.get("duplex_only") == "1"
    search = request.args.get("q", "").strip().lower()

    listings = get_listings()

    if status_filter:
        listings = [l for l in listings if l["status"] == status_filter]
    if source_filter:
        listings = [l for l in listings if l["source"] == source_filter]
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

    sources = sorted({l["source"] for l in get_listings()})
    counts = _status_counts()

    return render_template(
        "index.html",
        listings=listings,
        sources=sources,
        counts=counts,
        filters={
            "status": status_filter,
            "source": source_filter,
            "max_rent": max_rent,
            "duplex_only": duplex_only,
            "q": search,
        },
    )


@app.route("/listings/<int:listing_id>/status", methods=["POST"])
def set_status(listing_id):
    data = request.get_json()
    new_status = data.get("status")
    valid = {"new", "interested", "touring_applying", "passed"}
    if new_status not in valid:
        return jsonify({"error": "invalid status"}), 400
    update_status(listing_id, new_status)
    return jsonify({"ok": True})


@app.route("/listings/<int:listing_id>/notes", methods=["POST"])
def set_notes(listing_id):
    data = request.get_json()
    notes = data.get("notes", "")
    update_status(listing_id, _current_status(listing_id), notes=notes)
    return jsonify({"ok": True})


def _current_status(listing_id: int) -> str:
    listings = get_listings()
    for l in listings:
        if l["id"] == listing_id:
            return l["status"]
    return "new"


def _status_counts() -> dict:
    listings = get_listings()
    counts = {"new": 0, "interested": 0, "touring_applying": 0, "passed": 0}
    for l in listings:
        s = l.get("status", "new")
        if s in counts:
            counts[s] += 1
    counts["total"] = len(listings)
    return counts


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, port=port)
