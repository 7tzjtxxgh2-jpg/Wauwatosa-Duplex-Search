"""
Chunk 2: Claude (Haiku) enrichment + fit scoring.

For each listing that hasn't been scored yet, send its raw text to Haiku and
get back a clean structured summary plus a 0-10 fit score tuned to the user's
criteria. Results are stored on the listing row and surfaced in the dashboard.

Run manually:   python enrich.py            (scores all unenriched listings)
                python enrich.py --limit 10 (just the first 10)
                DRY_RUN=1 python enrich.py   (print what would be sent, no API calls)

Model: claude-haiku-4-5 — the user explicitly chose Haiku for cost; extraction
+ scoring is squarely in Haiku's wheelhouse.
"""
from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from db import init_db, get_listings_needing_enrichment, update_enrichment

# override=True: an empty ANTHROPIC_API_KEY in the shell would otherwise shadow .env
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

MODEL = "claude-haiku-4-5"
REQUEST_DELAY = 0.4   # polite pacing between API calls
DRY_RUN = os.getenv("DRY_RUN") == "1"

# Budgets and scoring weights — sourced from the user's stated criteria.
SOLO_BUDGET = 1200    # hard ceiling; over-budget tanks the score


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

class ListingEnrichment(BaseModel):
    """What Haiku extracts + scores for each listing."""
    clean_title: str = Field(
        description="A concise, human-readable title, e.g. '2BR upper duplex on N Hawthorne Ave'. "
                    "Prefer the street address + unit type if present."
    )
    beds: Optional[int] = Field(default=None, description="Number of bedrooms, or null if unknown")
    baths: Optional[float] = Field(default=None, description="Number of bathrooms (e.g. 1.5), or null")
    rent: Optional[int] = Field(default=None, description="Monthly rent in dollars, or null if unknown")
    neighborhood: Optional[str] = Field(
        default=None,
        description="Canonical neighborhood: Wauwatosa, Story Hill, Washington Heights, "
                    "East Tosa, Tosa Village, West Allis, Riverwest, or null",
    )
    is_duplex: bool = Field(
        description="True if this is a duplex / upper / lower / flat (half of a two-family), "
                    "false if a standalone apartment, house, or room"
    )
    pet_policy: Literal["allowed", "cats_only", "dogs_only", "no_pets", "unknown"] = Field(
        description="Pet policy stated in the listing"
    )
    parking: Literal["garage", "off_street", "street", "none", "unknown"] = Field(
        description="Parking type. 'garage' includes attached/detached garages; "
                    "'off_street' is a dedicated spot/driveway/lot"
    )
    laundry: Literal["in_unit", "shared", "hookups", "none", "unknown"] = Field(
        description="Laundry. 'in_unit' = washer/dryer in the unit; 'hookups' = connections only; "
                    "'shared' = building/coin laundry"
    )
    available_date: Optional[str] = Field(
        default=None, description="Move-in date as ISO YYYY-MM-DD if stated, else null"
    )
    fit_score: int = Field(
        description="Overall fit 0-10 per the rubric in the system prompt. "
                    "0 = terrible fit, 10 = perfect fit"
    )
    fit_reason: str = Field(description="One sentence explaining the score")
    concerns: list[str] = Field(
        default_factory=list,
        description="Short flags that lower the score, e.g. ['over budget', 'no laundry', 'no pets']",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short positives, e.g. ['garage', 'near MCW', 'in-unit laundry', 'available September']",
    )


# ---------------------------------------------------------------------------
# System prompt (the rubric)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""\
You are a rental-listing analyst helping someone find a place to live in the \
Wauwatosa, Wisconsin area for a September 2026 move-in (July–October 2026 acceptable).

Your job: read one raw rental listing and return a clean structured summary plus \
a 0–10 fit score. Extract facts only from the listing text — never invent details. \
If something isn't stated, use null / "unknown".

## The renter's criteria

- Budget: HARD ceiling of ${SOLO_BUDGET}/month (renting solo). This is firm.
- Strongly prefers a DUPLEX half (upper/lower/flat) over a large apartment complex.
- Strongly values off-street parking or a garage.
- Has a pet — listings that do not allow pets are a near-dealbreaker.
- Wants the Wauwatosa area (already pre-filtered geographically).
- In-unit laundry and a September-ish move-in date are nice-to-haves.

## Scoring rubric (0–10)

Apply in this order:

1. OVER BUDGET: if rent is known and exceeds ${SOLO_BUDGET}, the score must be at \
   most 3, no matter how nice it is. Add "over budget" to concerns. The further \
   over budget, the closer to 0.

2. NO PETS: if pets are explicitly not allowed, the score must be at most 3. \
   Add "no pets" to concerns. (If pet policy is unknown, do not penalize — just \
   note it's unclear.)

3. If neither dealbreaker applies, start at 5 and adjust:
   +2  duplex / upper / lower / flat
   +2  garage or off-street parking
   +1  in-unit laundry
   +1  available between 2026-08-01 and 2026-10-15
   +1  clearly in Wauwatosa proper (vs. an adjacent neighborhood)
   -1  if rent is unknown (harder to evaluate)
   Clamp the final score to the range 0–10.

Be decisive and consistent. A listing that's a duplex with a garage, pets OK, \
under budget should score 9–10. A pricey no-pets apartment complex should score 1–2.
"""


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def build_user_message(listing: dict) -> str:
    """Assemble the listing text we send to Haiku."""
    parts = [
        f"Source: {listing.get('source', 'unknown')}",
        f"Title: {listing.get('title') or '(none)'}",
    ]
    if listing.get("rent"):
        parts.append(f"Listed rent (from scraper): ${listing['rent']}")
    if listing.get("beds"):
        parts.append(f"Beds (from scraper): {listing['beds']}")
    if listing.get("neighborhood"):
        parts.append(f"Neighborhood (from scraper): {listing['neighborhood']}")
    if listing.get("address"):
        parts.append(f"Address: {listing['address']}")
    desc = listing.get("description") or ""
    parts.append(f"\nListing text:\n{desc}")
    return "\n".join(parts)


def _clamp_score(score: int) -> int:
    return max(0, min(10, int(score)))


def enrich_listing(client, listing: dict) -> dict:
    """Call Haiku, return a dict of DB fields ready for update_enrichment()."""
    user_msg = build_user_message(listing)

    resp = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        output_format=ListingEnrichment,
    )
    result: ListingEnrichment = resp.parsed_output

    return {
        "ai_summary": result.clean_title,
        "beds": str(result.beds) if result.beds is not None else listing.get("beds"),
        "baths": str(result.baths) if result.baths is not None else listing.get("baths"),
        "rent": result.rent if result.rent is not None else listing.get("rent"),
        "neighborhood": result.neighborhood or listing.get("neighborhood"),
        "duplex_flag": int(result.is_duplex),
        "pet_policy": result.pet_policy,
        "parking": result.parking,
        "laundry": result.laundry,
        "available_date": result.available_date or listing.get("available_date"),
        "fit_score": _clamp_score(result.fit_score),
        "fit_reason": result.fit_reason,
        "concerns": json.dumps(result.concerns),
        "highlights": json.dumps(result.highlights),
        "_usage": resp.usage,  # not stored; used for cost logging
    }


def run_enrichment(limit: int | None = None) -> None:
    init_db()
    pending = get_listings_needing_enrichment(limit=limit)
    if not pending:
        print("Nothing to enrich — all listings already scored.")
        return

    print(f"Enriching {len(pending)} listing(s) with {MODEL}"
          f"{' [DRY RUN]' if DRY_RUN else ''}…\n")

    if DRY_RUN:
        sample = pending[0]
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        print("\n=== SAMPLE USER MESSAGE (listing 1 of "
              f"{len(pending)}) ===")
        print(build_user_message(sample))
        print("\n(DRY_RUN=1 — no API calls made.)")
        return

    from anthropic import Anthropic
    client = Anthropic()  # reads ANTHROPIC_API_KEY

    total_in = total_out = scored = 0
    for i, listing in enumerate(pending, 1):
        try:
            fields = enrich_listing(client, listing)
            usage = fields.pop("_usage")
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            update_enrichment(listing["id"], fields)
            scored += 1
            print(f"  [{i}/{len(pending)}] score {fields['fit_score']}/10 — "
                  f"{fields['ai_summary'][:55]}")
        except Exception as e:
            print(f"  [{i}/{len(pending)}] ! failed: {e}")
        time.sleep(REQUEST_DELAY)

    # Haiku 4.5 pricing: $1/1M input, $5/1M output
    cost = total_in / 1_000_000 * 1.0 + total_out / 1_000_000 * 5.0
    print(f"\nDone. Scored {scored}/{len(pending)}.")
    print(f"Tokens: {total_in:,} in / {total_out:,} out  (~${cost:.3f})")


if __name__ == "__main__":
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    run_enrichment(limit=limit)
