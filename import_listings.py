"""
Chunk 6: ingestion path for manually-gathered listings (Facebook Marketplace,
FB Groups, Nextdoor) captured via Claude in Chrome.

These sources are login-walled and their ToS prohibits automated scraping, so
we never run a headless scraper against them. Instead, Claude in Chrome reads
the user's own logged-in session, and the structured results flow through this
module — the SAME non-rental / geo / type filters as the automated scrapers —
then get scored by enrich.py on the next run.

Usage:
    # From a JSON file (list of listing dicts):
    python import_listings.py --source facebook_marketplace listings.json

    # From stdin:
    cat listings.json | python import_listings.py --source nextdoor -

Each input listing dict may contain:
    url        (required) — the post/item URL; used as the dedup key
    title      — listing title / first line
    rent       — monthly rent (int or "$1,200" string)
    description— body text
    location   — neighborhood / city string
    beds       — bedroom count
    available  — availability text/date
    contact    — poster name or contact
"""
from __future__ import annotations

import sys
import json
import hashlib
import argparse

from db import init_db, upsert_listing, get_listing
from scraper import (
    blank_listing,
    classify_non_rental,
    classify_out_of_scope,
    classify_listing_type,
    detect_neighborhood,
    detect_duplex,
    extract_rent,
    extract_beds,
)

VALID_SOURCE_PREFIXES = ("facebook_", "fb_", "nextdoor")


def _coerce_rent(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if 300 < value < 10000 else None
    s = str(value).strip()
    # Plain numeric string like "1000" or "1,200" (no $ sign)
    digits = s.replace(",", "").replace("$", "").strip()
    if digits.isdigit():
        n = int(digits)
        return n if 300 < n < 10000 else None
    # Fall back to extracting a $-prefixed amount from free text
    return extract_rent(s)


def normalize(raw: dict, source: str) -> dict | None:
    """
    Turn a raw captured listing into a DB-ready row, applying the same filters
    as the automated scrapers. Returns None if the listing should be skipped
    (non-rental, out of scope, or missing a URL).
    """
    url = (raw.get("url") or "").strip()
    if not url:
        return None

    title = (raw.get("title") or "").strip()
    description = (raw.get("description") or "").strip()
    location = (raw.get("location") or "").strip()
    combined = f"{title} {description} {location}"

    # Same gates as the scrapers
    if classify_non_rental(combined):
        return ("skip_nonrental", None)
    if classify_out_of_scope(f"{title} {location}", location, body=description):
        return ("skip_geo", None)

    listing = blank_listing(source, url)
    listing["title"] = title or description[:80] or "Untitled"
    listing["description"] = description or title
    listing["rent"] = _coerce_rent(raw.get("rent")) or extract_rent(combined)
    listing["beds"] = str(raw["beds"]) if raw.get("beds") else extract_beds(combined)
    listing["neighborhood"] = location or detect_neighborhood(combined)
    listing["duplex_flag"] = int(detect_duplex(combined))
    listing["available_date"] = raw.get("available")
    listing["contact"] = raw.get("contact")
    listing["listing_type"] = classify_listing_type(title, description, source)
    listing["raw_data"] = json.dumps(raw)
    return ("ok", listing)


def ingest_listings(raw_listings: list[dict], source: str) -> dict:
    """
    Ingest captured listings. Returns counts. New listings have enriched_at
    NULL, so the next `python enrich.py` run will score them.
    """
    init_db()
    counts = {"added": 0, "reseen": 0, "skip_nonrental": 0, "skip_geo": 0, "no_url": 0}
    for raw in raw_listings:
        result = normalize(raw, source)
        if result is None:
            counts["no_url"] += 1
            continue
        status, listing = result
        if status != "ok":
            counts[status] += 1
            continue
        if upsert_listing(listing):
            counts["added"] += 1
        else:
            counts["reseen"] += 1
    return counts


def quick_add(text: str, url: str = "", source: str = "facebook_group") -> dict:
    """
    Add a single pasted listing (e.g. a Facebook group post copied by hand).

    - text: the full pasted post body (required)
    - url:  optional permalink; if blank a stable synthetic URL is generated
            from a hash of the text so re-pasting the same post dedups
    - source: tag, e.g. 'facebook_group', 'nextdoor'

    Returns {"status": "ok"|"skip_nonrental"|"skip_geo"|"empty"|"duplicate",
             "listing_id": int|None, "title": str}
    """
    init_db()
    text = (text or "").strip()
    if not text:
        return {"status": "empty", "listing_id": None, "title": ""}

    if not (url or "").strip():
        digest = hashlib.md5(text.encode()).hexdigest()[:12]
        url = f"manual://{source}/{digest}"

    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    raw = {
        "url": url.strip(),
        "title": first_line[:120],
        "description": text,
        "location": "",
    }

    result = normalize(raw, source)
    if result is None:
        return {"status": "empty", "listing_id": None, "title": first_line[:80]}
    status, listing = result
    if status != "ok":
        return {"status": status, "listing_id": None, "title": first_line[:80]}

    is_new = upsert_listing(listing)
    if not is_new:
        # Find existing id for the URL so the caller can still link to it
        try:
            from db import get_conn
            with get_conn() as conn:
                row = conn.execute("SELECT id FROM listings WHERE url=?", (listing["url"],)).fetchone()
            return {"status": "duplicate", "listing_id": row["id"] if row else None,
                    "title": listing["title"]}
        except Exception:
            return {"status": "duplicate", "listing_id": None, "title": listing["title"]}

    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM listings WHERE url=?", (listing["url"],)).fetchone()
    return {"status": "ok", "listing_id": row["id"] if row else None, "title": listing["title"]}


def main():
    parser = argparse.ArgumentParser(description="Ingest FB/Nextdoor listings")
    parser.add_argument("--source", required=True,
                        help="Source tag, e.g. facebook_marketplace, fb_group_wauwatosa, nextdoor")
    parser.add_argument("input", help="Path to JSON file, or '-' for stdin")
    args = parser.parse_args()

    if not args.source.startswith(VALID_SOURCE_PREFIXES):
        print(f"Warning: source '{args.source}' doesn't start with one of "
              f"{VALID_SOURCE_PREFIXES} — continuing anyway.")

    raw = sys.stdin.read() if args.input == "-" else open(args.input).read()
    data = json.loads(raw)
    if not isinstance(data, list):
        print("Error: input must be a JSON array of listing objects.")
        sys.exit(1)

    counts = ingest_listings(data, args.source)
    print(f"Ingested from {args.source}:")
    print(f"  added:           {counts['added']}")
    print(f"  re-seen:         {counts['reseen']}")
    print(f"  skipped (non-rental): {counts['skip_nonrental']}")
    print(f"  skipped (out of scope): {counts['skip_geo']}")
    print(f"  skipped (no URL): {counts['no_url']}")
    if counts["added"]:
        print(f"\nRun `python enrich.py` to score the {counts['added']} new listing(s).")


if __name__ == "__main__":
    main()
