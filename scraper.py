"""
Chunk 1: Craigslist HTML + small PM site scraper.
Normalizes all sources to a common schema and upserts into SQLite.

After ingesting search-result pages, fetches each new Craigslist detail page
once to capture the listing body, address, and posting date.

Run manually:  python scraper.py
Via cron/GH Actions: see .github/workflows/scrape.yml (currently dormant)
"""
from __future__ import annotations

import json
import re
import time
import yaml
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from db import (
    init_db,
    upsert_listing,
    update_description,
    update_listing_type,
    mark_description_failed,
    get_listings_needing_description,
    delete_listing,
)

# Craigslist tolerates a real browser UA; a custom UA triggers 403 on search.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PM_UA = "wauwatosa-rental-search/1.0 personal-use (contact: jacklaufenberg@icloud.com)"

REQUEST_DELAY = 1.5            # between requests to the same site, polite
DETAIL_FETCH_DELAY = 2.0       # between Craigslist detail-page fetches
DETAIL_MAX_PER_RUN = 50        # cap detail fetches so a fresh DB doesn't take 30 min

DUPLEX_KEYWORDS = {
    "duplex", "upper", "lower", "flat", "2-family", "two-family",
    "upper unit", "lower unit", "upper flat", "lower flat",
}

LISTING_KEYWORDS = {
    "bed", "br", "bath", "rent", "lease", "apartment", "duplex", "upper",
    "lower", "flat", "studio", "room", "available", "month",
}

NON_LISTING_PHRASES = {
    "sign in", "log in", "login", "register", "contact us", "about us",
    "our services", "property management", "owner portal", "tenant portal",
    "resident portal", "application policy", "onboarding", "eviction guarantee",
    "management agreement", "lease only", "accounting", "maintenance", "insurance",
    "investors", "services", "faqs", "privacy policy", "terms of service",
}

# Patterns that indicate the post is NOT a residential rental offering.
# Each tuple is (compiled-pattern-source, label-for-logging).
# Tested against both titles and descriptions; one match → reject.
NON_RENTAL_PATTERNS = [
    # ---- Job postings disguised as housing -----------------------------------
    (r"\bcaregiver\s+(wanted|needed|position|opportunity)\b",   "caregiver job"),
    (r"\blive[-\s]?in\s+(caregiver|nanny|babysitter|au\s*pair|housekeeper)\b",
                                                                  "live-in job"),
    (r"\b(nanny|au\s*pair|housekeeper)\s+(wanted|needed)\b",     "domestic job"),

    # ---- Personals / non-housing -------------------------------------------
    (r"\bseeking\s+(a\s+)?(companion|friend|partner|relationship|romance|date|hookup|woman|man|swf|swm|female|male)\b",
                                                                  "personals"),
    (r"\b(friend\s+with\s+benefits|fwb)\b",                       "personals"),

    # ---- ISO (in-search-of) posts -------------------------------------------
    (r"^\s*iso\b",                                                "ISO post"),
    (r"\bin\s+search\s+of\b.*\b(room|apartment|housing|place|sublet)\b",
                                                                  "ISO post"),

    # ---- Commercial / business spaces ---------------------------------------
    (r"\b(salon|barber|stylist)\s+(chair|booth|station|suite)\b", "salon space"),
    (r"\b(massage|treatment|therapy|reiki|healing|wellness)\s+(room|space|studio\s+space|studio|practice)\s+(rental|for\s+rent|available|lease)\b",
                                                                  "wellness space"),
    (r"\boffice\s+(space|suite)\s+(for\s+rent|for\s+lease|available)\b",
                                                                  "office space"),
    (r"\bprofessional\s+(office|suite)\s+(for\s+rent|for\s+lease|available)\b",
                                                                  "professional office"),
    (r"\b(commercial|retail)\s+(space|property|location|unit|rental)\b",
                                                                  "commercial space"),
    (r"\bstorefront\b",                                           "storefront"),
    (r"\bco-?working\b",                                          "coworking"),
    (r"\bevent\s+(space|venue)\s+(for\s+rent|available|rental)\b","event space"),
    (r"\b(chair|booth)\s+rental\b",                               "chair/booth rental"),
    (r"\b(yoga|fitness|pilates|dance|workout)\s+studio\s+(for\s+rent|space\s+available|rental)\b",
                                                                  "fitness studio"),
]

# Pre-compile for speed
_NON_RENTAL_RE = [(re.compile(p, re.I), label) for p, label in NON_RENTAL_PATTERNS]


# Geographic scope — within ~2 miles of Wauwatosa, plus explicit exceptions
# (Riverwest @ 53212, American Family Field area @ 53214).
#
# Out-of-scope suburbs: anything more than ~2-3 miles from Tosa center,
# plus the Milwaukee neighborhoods east of downtown and south of I-94 that
# the user explicitly excluded by listing only Riverwest + AFF.
OUT_OF_SCOPE_AREAS = {
    # West (beyond West Allis)
    "waukesha", "pewaukee", "brookfield", "elm grove",
    # Lake Country
    "lake country", "oconomowoc", "hartland", "delafield", "lake mills",
    "nashotah", "okauchee", "dousman", "wales",
    # North / northwest suburbs
    "germantown", "menomonee falls", "sussex", "lannon", "richfield",
    "lisbon", "colgate", "thiensville", "mequon", "cedarburg",
    "grafton", "port washington", "saukville", "fredonia",
    "hartford", "slinger", "rubicon", "west bend",
    # North Milwaukee suburbs
    "fox point", "river hills", "bayside", "whitefish bay", "shorewood",
    "glendale", "brown deer",
    # South (beyond West Allis)
    "cudahy", "south milwaukee", "st francis", "saint francis", "st. francis",
    "greenfield", "greendale", "oak creek", "franklin",
    "hales corners", "muskego", "new berlin", "racine", "kenosha",
    # Far west / other counties
    "watertown", "ixonia", "jefferson", "fort atkinson",
    "burlington", "elkhorn", "delavan", "whitewater", "sheboygan",
    # Milwaukee neighborhoods the user implicitly excluded
    "east side", "east side milwaukee", "eastside milwaukee",
    "lower east side", "milwaukee/east side",
    "walker's point", "walkers point",
    "bay view", "bayview",
    "milwaukee north", "milwaukee southside",
}

# Strong in-scope hints — presence of any of these in title or location
# OVERRIDES an out-of-scope match, since the listing is clearly in our area.
IN_SCOPE_PATTERNS = [
    r"\bwauwatosa\b", r"\btosa\b",
    r"\bwest\s+allis\b",
    r"\briverwest\b",
    r"\bstory\s+hill\b", r"\bwashington\s+heights\b",
    r"\btosa\s+village\b", r"\beast\s+tosa\b",
    r"\bmcw\b", r"\bmedical\s+college\b", r"\bfroedtert\b",
    r"\bamerican\s+family\s+field\b", r"\bmiller\s+park\b",
    r"\bbrewers?\s+(stadium|field|park)\b",
    r"\b5321[2-4]\b",   # 53212 Riverwest, 53213 South Tosa, 53214 AFF area
    r"\b53226\b",        # North Tosa (MCW area)
    r"\b53208\b", r"\b53210\b", r"\b53222\b",  # adjacent Milwaukee
]
_IN_SCOPE_RE = [re.compile(p, re.I) for p in IN_SCOPE_PATTERNS]

# Street-suffix words. If an out-of-scope city name is immediately
# followed by one of these, treat it as a street name, not a city.
# Example: "Greenfield Ave" (street) vs "Greenfield" (suburb).
_STREET_SUFFIX_RE = re.compile(
    r"\s+(ave(nue)?|blvd|boulevard|st(reet)?|rd|road|pl(ace)?|"
    r"dr(ive)?|ln|lane|ct|court|hwy|highway|way|terr(ace)?|parkway|pkwy)\b",
    re.I,
)

# Out-of-scope ZIP codes — anywhere more than ~2-3 mi from Tosa center.
# When any of these ZIPs appears in title/location/body, the listing is rejected.
OUT_OF_SCOPE_ZIPS = {
    # Milwaukee neighborhoods (East Side, South Side, Downtown, etc.)
    "53202", "53203",        # Downtown / Lower East Side
    "53204",                 # Walker's Point / South Side
    "53205",                 # Brewers Hill / Marquette campus area
    "53207",                 # Bay View
    "53211",                 # UWM area / North East Side
    "53217",                 # Whitefish Bay / Glendale
    "53218", "53223", "53224", "53225",  # Brown Deer / far north
    "53219", "53220", "53221", "53227", "53228",  # Greenfield / Hales Corners / SW
    "53233",                 # Downtown (Marquette area)
    # Suburb ZIPs (south Milwaukee County)
    "53110",                 # Cudahy
    "53129",                 # Greendale
    "53130",                 # Hales Corners
    "53132",                 # Franklin
    "53154",                 # Oak Creek
    "53172",                 # South Milwaukee
    # Suburb ZIPs (west / north)
    "53005", "53045",        # Brookfield
    "53051",                 # Menomonee Falls
    "53066",                 # Oconomowoc
    "53072",                 # Pewaukee
    "53092",                 # Mequon
    "53185", "53186", "53188", "53189",  # Waukesha
}
_OOS_ZIP_RE = re.compile(rf"\b({'|'.join(OUT_OF_SCOPE_ZIPS)})\b")


# ----------------------------------------------------------------------------
# Listing-type classifier: distinguishes
#   - 'rental'   = full unit for rent (apartment, duplex, house, etc.)
#   - 'roommate' = someone seeking a roommate to share their existing dwelling
# ----------------------------------------------------------------------------
ROOMMATE_PATTERNS = [
    # Explicit "looking for roommate" language
    r"\b(looking\s+for|seeking|need|want|wanted|find|finding)\s+(a\s+)?(roommate|housemate|room\s*mate)\b",
    r"\b(roommate|housemate|room\s*mate)\s+(wanted|needed|sought)\b",
    # "Share my place" language
    r"\bshare\s+(my|our|the|a)\s+(apartment|home|house|condo|place|duplex|space|unit)\b",
    r"\broom\s+(in\s+)?(my|our)\s+(apartment|home|house|condo|duplex|\d+\s*br)\b",
    r"\b(spare|extra|available)\s+(room|bedroom)\s+(in\s+)?(my|our)\b",
    r"\bshared\s+(kitchen|bathroom|bath|living|home|space|house|apartment)\b",
    r"\b(join|fit\s+in\s+with)\s+(my|our)\s+(home|house|apartment|household)\b",
    r"\blive\s+with\s+(me|us)\b",
    r"\bi\s+(live|own|rent|reside|am\s+living|am\s+renting)\s+(here|in\s+(this|my))\b",
    # "Room for rent in my X" — common phrasing
    r"\b(room|bedroom)\s+for\s+rent\s+in\s+(my|our|a\s+shared|the)\b",
    r"\b(private|furnished)\s+room\s+(in|available)\s+(my|our|a\s+shared)\b",
    # "(something) in my home/house/apartment" — strong roommate signal
    # because landlords listing a unit they don't live in don't say "my home"
    r"\b(in|at|of)\s+(my|our)\s+(house|home|apartment|condo|duplex|place|unit)\b",
]
_ROOMMATE_RE = [re.compile(p, re.I) for p in ROOMMATE_PATTERNS]

WHOLE_UNIT_PATTERNS = [
    # Even in the rooms category, these signal a full-unit sublease
    r"\bsubleas(ing|e)\s+(my|the|a)\s+(apartment|unit|studio|\d+\s*(br|bed|bedroom))\b",
    r"\b(whole|entire)\s+(unit|apartment|home|house|duplex|floor)\b",
    r"\b\d+\s*(br|bed|bedroom)\s+(apartment|unit|duplex|condo|flat)\s+for\s+rent\b",
    r"\bfull\s+apartment\s+(for\s+rent|available)\b",
]
_WHOLE_UNIT_RE = [re.compile(p, re.I) for p in WHOLE_UNIT_PATTERNS]


def classify_listing_type(title: str, description: str = "", source: str = "") -> str:
    """
    Return 'roommate' if the post is by someone seeking a roommate to share
    their existing dwelling; otherwise return 'rental'.

    Strategy:
      1. Explicit "whole-unit" language (sublease entire apt, etc.) → rental
      2. Explicit roommate language → roommate
      3. Source-based default: craigslist_rooms is Craigslist's "rooms &
         shared" category, so absent other signals → roommate
      4. Everything else → rental
    """
    text = f"{title or ''} {description or ''}"

    # Whole-unit signals win first — handles "Subleasing my 1BR" in rooms category
    for rx in _WHOLE_UNIT_RE:
        if rx.search(text):
            return "rental"

    # Then explicit roommate language
    for rx in _ROOMMATE_RE:
        if rx.search(text):
            return "roommate"

    # Source-based default
    if source == "craigslist_rooms":
        return "roommate"

    return "rental"


NEIGHBORHOOD_PATTERNS = [
    (r"\bwauwatosa\b", "Wauwatosa"),
    (r"\bwashington heights\b", "Washington Heights"),
    (r"\bstory hill\b", "Story Hill"),
    (r"\beast tosa\b", "East Tosa"),
    (r"\bthe village\b", "Tosa Village"),
    (r"\bwest allis\b", "West Allis border"),
    (r"\b5321[36]\b", "Wauwatosa"),
    (r"\b53208\b", "Milwaukee west side"),
    (r"\b53210\b", "Milwaukee west side"),
]


# ---------------------------------------------------------------------------
# Pure parsing helpers (covered by tests in tests/test_parsers.py)
# ---------------------------------------------------------------------------

def detect_neighborhood(text: str) -> str | None:
    for pattern, label in NEIGHBORHOOD_PATTERNS:
        if re.search(pattern, text, re.I):
            return label
    return None


def detect_duplex(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in DUPLEX_KEYWORDS)


def extract_rent(text: str) -> int | None:
    match = re.search(r"\$\s*([\d,]+)", text)
    if not match:
        return None
    val = int(match.group(1).replace(",", ""))
    return val if 300 < val < 10000 else None


def extract_beds(text: str) -> str | None:
    match = re.search(r"(\d+)\s*(?:br|bed|bedroom)", text, re.I)
    return match.group(1) if match else None


def classify_non_rental(text: str) -> str | None:
    """
    Check if text matches any non-rental pattern (commercial, ISO, job, personals).
    Returns the label of the matched pattern, or None if the text looks like
    a real residential rental listing.
    """
    if not text:
        return None
    for regex, label in _NON_RENTAL_RE:
        if regex.search(text):
            return label
    return None


def is_residential_rental(text: str) -> bool:
    """Convenience boolean wrapper around classify_non_rental."""
    return classify_non_rental(text) is None


def classify_out_of_scope(title: str, location: str = "", body: str = "") -> str | None:
    """
    Detect if a listing is geographically out of scope.
    Returns the matched out-of-scope label, or None if in scope.

    Strategy:
      1. If anywhere (title/location/body) mentions a strong in-scope hint
         (Wauwatosa, MCW, Riverwest, target ZIP, etc.) → in scope, return None.
      2. Out-of-scope ZIP found anywhere → reject (ZIPs are specific signals).
      3. Out-of-scope city name in location field → reject (most reliable).
      4. Out-of-scope city name in title at a strong location signal
         (start of title, after dash, "in <city>", "<city>, WI", etc.) → reject.

    City names in the description body are NOT checked (too many false
    positives like "5 minutes to Brookfield"). ZIPs in the body ARE checked
    because they're specific.
    """
    title_lc = (title or "").lower()
    loc_lc = (location or "").lower().strip().strip("()").strip()
    body_lc = (body or "").lower()
    everywhere = f"{title_lc} {loc_lc} {body_lc}"

    # ----- In-scope override (check everywhere — keywords are distinctive) -----
    for rx in _IN_SCOPE_RE:
        if rx.search(everywhere):
            return None

    # ----- Out-of-scope ZIPs (most reliable signal, check everywhere) -----
    m = _OOS_ZIP_RE.search(everywhere)
    if m:
        return f"ZIP {m.group(1)}"

    # ----- Location field: bare match -----
    # Strip ", WI" / "wi" / commas, then check exact match
    loc_clean = re.sub(r"[,.]", "", loc_lc)
    loc_clean = re.sub(r"\bwi(sconsin)?\b", "", loc_clean).strip()
    if loc_clean in OUT_OF_SCOPE_AREAS:
        return loc_clean

    # ----- Location field: substring match (with street-name guard) -----
    # Handles "Sussex Lisbon Pewaukee area" — but skip if the matched word is
    # actually a street name (e.g. "1454 N. Franklin Place").
    for city in OUT_OF_SCOPE_AREAS:
        for match in re.finditer(rf"\b{re.escape(city)}\b", loc_lc):
            rest = loc_lc[match.end():]
            if _STREET_SUFFIX_RE.match(rest):
                continue  # street name, not a city
            return city

    # ----- Title field: only with strong location signals -----
    for city in OUT_OF_SCOPE_AREAS:
        # The patterns are anchored so they only fire when the city name
        # appears as a real location reference, not buried in marketing copy.
        candidate_patterns = [
            rf"^{re.escape(city)}\b",                    # "Bay View - 2BR..."
            rf"\bin\s+{re.escape(city)}\b",              # "live in Waukesha"
            rf"-\s*{re.escape(city)}\b",                 # "Studio Apartment - South Milwaukee"
            rf"\b{re.escape(city)},?\s+wi\b",            # "Sussex, WI"
            rf"\b{re.escape(city)}\s+(home|apartment|duplex|unit|condo|studio|bedroom|br|house|townhouse)\b",
        ]
        for tpat in candidate_patterns:
            m = re.search(tpat, title_lc)
            if not m:
                continue
            # Street-name guard: if immediately followed by Ave/Blvd/St/etc,
            # this is a street name (e.g. "Greenfield Ave"), not a city.
            rest = title_lc[m.end():]
            if _STREET_SUFFIX_RE.match(rest):
                continue
            return city

    return None


def is_in_scope(title: str, location: str = "") -> bool:
    """Convenience boolean wrapper around classify_out_of_scope."""
    return classify_out_of_scope(title, location) is None


def is_likely_listing(text: str) -> bool:
    """Return True only if text looks like a rental listing."""
    lower = text.lower()
    has_price = bool(re.search(r"\$\s*\d+", text))
    keyword_hits = sum(1 for kw in LISTING_KEYWORDS if kw in lower)
    if not has_price and keyword_hits < 2:
        return False
    if any(phrase in lower for phrase in NON_LISTING_PHRASES):
        return False
    if len(text) < 30 or len(text) > 3000:
        return False
    return True


def resolve_href(href: str, base_url: str) -> str:
    if href.startswith("http"):
        return href
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}{href}"


def blank_listing(source: str, url: str) -> dict:
    return {
        "url": url,
        "source": source,
        "title": None,
        "address": None,
        "neighborhood": None,
        "beds": None,
        "baths": None,
        "rent": None,
        "available_date": None,
        "contact": None,
        "description": None,
        "duplex_flag": 0,
        "raw_data": None,
        "listing_type": "rental",
    }


# ---------------------------------------------------------------------------
# Craigslist scrapers
# ---------------------------------------------------------------------------

def parse_craigslist_search(html: str, source_name: str, base_url: str) -> list[dict]:
    """Pure-function parser: given Craigslist search HTML, return listings."""
    soup = BeautifulSoup(html, "lxml")
    elements = soup.select("li.cl-static-search-result") or soup.select("li.result-row")

    listings = []
    for el in elements:
        link = el.find("a", href=True)
        if not link:
            continue
        href = resolve_href(link["href"], base_url)

        title_el = el.select_one("div.title, span#titletextonly")
        price_el = el.select_one("div.price, span.price")
        loc_el = el.select_one("div.location, span.result-hood")

        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        price_text = price_el.get_text(strip=True) if price_el else ""
        location = loc_el.get_text(strip=True) if loc_el else ""

        # Reject obvious non-rentals at the title stage (ISO posts, jobs, personals).
        # Commercial listings often only show up in the description, so those are
        # caught after detail-page enrichment.
        if classify_non_rental(title):
            continue
        # Geographic filter — reject if title+location identify an out-of-scope area
        if classify_out_of_scope(title, location):
            continue

        full_text = f"{title} {price_text} {location}"

        listing = blank_listing(source_name, href)
        listing["title"] = title
        listing["neighborhood"] = (
            detect_neighborhood(full_text) or detect_neighborhood(location)
        )
        listing["duplex_flag"] = int(detect_duplex(title))
        listing["rent"] = extract_rent(price_text) or extract_rent(full_text)
        listing["beds"] = extract_beds(title)
        listing["description"] = None  # filled in by detail-page fetch
        listing["listing_type"] = classify_listing_type(title, "", source_name)
        listing["raw_data"] = json.dumps({
            "title": title,
            "price": price_text,
            "location": location,
            "source_url": base_url,
        })
        listings.append(listing)
    return listings


def parse_craigslist_detail(html: str) -> dict:
    """Pure-function parser: extract body, address, and posting date."""
    soup = BeautifulSoup(html, "lxml")
    body_el = soup.select_one("section#postingbody, #postingbody")
    description = (
        body_el.get_text(" ", strip=True) if body_el else ""
    )
    # Remove the QR-code preamble Craigslist injects into postingbody
    description = re.sub(r"QR Code Link to This Post\s*", "", description).strip()

    address_el = soup.select_one("div.mapaddress")
    address = address_el.get_text(strip=True) if address_el else None

    posted_el = soup.select_one("time.date.timeago, time[datetime]")
    posted = posted_el.get("datetime") if posted_el else None

    return {
        "description": description[:2000],  # cap to keep DB rows reasonable
        "address": address,
        "available_date": posted,
    }


def scrape_craigslist(client: httpx.Client, source_name: str, url: str) -> list[dict]:
    print(f"  [CL] {source_name}")
    try:
        resp = client.get(url, headers={"User-Agent": BROWSER_UA}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"    ! fetch failed: {e}")
        return []
    listings = parse_craigslist_search(resp.text, source_name, url)
    print(f"    → {len(listings)} listings")
    return listings


def enrich_craigslist_details(client: httpx.Client, limit: int = DETAIL_MAX_PER_RUN) -> tuple[int, int]:
    """
    Fetch Craigslist detail pages for any listings flagged description_fetched=0.
    Returns (n_enriched, n_filtered_as_commercial_or_other).
    """
    pending = get_listings_needing_description("craigslist_")
    if not pending:
        return (0, 0)

    n_to_fetch = min(len(pending), limit)
    print(f"\nEnriching {n_to_fetch} Craigslist detail pages (of {len(pending)} pending)…")
    fetched = 0
    rejected = 0
    for row in pending[:limit]:
        try:
            resp = client.get(
                row["url"],
                headers={"User-Agent": BROWSER_UA},
                timeout=15,
            )
            if resp.status_code == 404:
                mark_description_failed(row["id"])
                continue
            resp.raise_for_status()
            details = parse_craigslist_detail(resp.text)

            # Post-enrichment filter: now that we have the body text, re-check
            # whether this is actually a residential rental.
            label = classify_non_rental(details["description"])
            if label:
                print(f"    × dropping (non-rental: {label}): {row['url']}")
                delete_listing(row["id"])
                rejected += 1
                continue
            # Post-enrichment geographic check: address+body may now reveal OOS
            # (e.g. body says "Milwaukee, WI 53202" which is East Side)
            geo_label = classify_out_of_scope(
                row.get("title") or "",
                details.get("address") or "",
                body=details.get("description") or "",
            )
            if geo_label:
                print(f"    × dropping (out of scope: {geo_label}): {row['url']}")
                delete_listing(row["id"])
                rejected += 1
                continue

            update_description(
                row["id"],
                details["description"],
                address=details["address"],
                available_date=details["available_date"],
            )
            # Re-classify listing_type now that we have the full body text
            new_type = classify_listing_type(
                row.get("title") or "", details["description"], row["source"]
            )
            update_listing_type(row["id"], new_type)
            fetched += 1
        except Exception as e:
            print(f"    ! {row['url'][:60]}… — {e}")
        time.sleep(DETAIL_FETCH_DELAY)
    print(f"    → enriched {fetched}/{n_to_fetch} (dropped {rejected} non-rentals)")
    return (fetched, rejected)


def clean_existing_non_rentals() -> int:
    """
    Retroactive cleanup: scan existing listings, delete any whose title or
    description matches a non-rental pattern. Run once after deploying the
    filter; subsequent scrapes will not insert non-rentals to begin with.
    """
    from db import get_listings  # local import to avoid cycle at import time
    all_listings = get_listings()
    deleted = 0
    by_label: dict[str, int] = {}
    for l in all_listings:
        text = (l.get("title") or "") + " " + (l.get("description") or "")
        label = classify_non_rental(text)
        if label:
            delete_listing(l["id"])
            deleted += 1
            by_label[label] = by_label.get(label, 0) + 1
    if deleted:
        print(f"\nCleanup: removed {deleted} non-rental listings")
        for label, n in sorted(by_label.items(), key=lambda x: -x[1]):
            print(f"  {n:>3} × {label}")
    else:
        print("\nCleanup: no non-rental listings to remove.")
    return deleted


def backfill_listing_types() -> dict[str, int]:
    """
    Recompute listing_type for all existing rows based on current title +
    description + source. Used once after schema migration to populate the
    new column for legacy data.
    """
    from db import get_listings
    all_listings = get_listings()
    counts = {"rental": 0, "roommate": 0, "changed": 0}
    for l in all_listings:
        new_type = classify_listing_type(
            l.get("title") or "",
            l.get("description") or "",
            l.get("source") or "",
        )
        if l.get("listing_type") != new_type:
            update_listing_type(l["id"], new_type)
            counts["changed"] += 1
        counts[new_type] += 1
    print(f"\nBackfill: {counts['rental']} rentals, {counts['roommate']} roommate posts "
          f"({counts['changed']} rows updated)")
    return counts


def clean_out_of_scope_listings() -> int:
    """
    Retroactive geographic cleanup: scan existing listings, delete any whose
    title/location identify an out-of-scope area (more than ~2 mi from Tosa,
    excluding Riverwest and the American Family Field area).
    """
    import json as _json
    from db import get_listings
    all_listings = get_listings()
    deleted = 0
    by_label: dict[str, int] = {}
    for l in all_listings:
        # Reconstruct the location field from raw_data when available
        location = ""
        if l.get("raw_data"):
            try:
                raw = _json.loads(l["raw_data"])
                location = raw.get("location", "") or ""
            except _json.JSONDecodeError:
                pass
        # Fall back to address (set by Craigslist enrichment)
        location = location or (l.get("address") or "")

        label = classify_out_of_scope(
            l.get("title") or "",
            location,
            body=l.get("description") or "",
        )
        if label:
            delete_listing(l["id"])
            deleted += 1
            by_label[label] = by_label.get(label, 0) + 1
    if deleted:
        print(f"\nGeo cleanup: removed {deleted} out-of-scope listings")
        for label, n in sorted(by_label.items(), key=lambda x: -x[1]):
            print(f"  {n:>3} × {label}")
    else:
        print("\nGeo cleanup: no out-of-scope listings to remove.")
    return deleted


# ---------------------------------------------------------------------------
# PM site scraper — supports httpx for static HTML and Playwright for JS-rendered
# ---------------------------------------------------------------------------

import hashlib as _hashlib

# Tight address pattern (max 3 words between street number and suffix) to
# avoid greedy matches like "$1,498 House 221 W Ring St" → "498 House 221...".
# Word boundary ensures the number isn't part of a price ("$1,498").
_ADDR_RE = re.compile(
    r"\b\d{1,5}\s+(?:[NSEW]\.?\s+)?[\w'\.]+(?:\s+[\w'\.]+){0,3}\s+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Pl|Place|Dr|Drive|"
    r"Ln|Lane|Ct|Court|Way|Pkwy|Parkway|Terrace|Terr|Hwy|Highway)\b",
    re.I,
)

# Strip leading "N / M [NEW]" pagination/badge prefixes that Atari and
# similar AppFolio sites prepend to listing-card link text.
_PAGINATION_PREFIX_RE = re.compile(r"^\s*\d+\s*/\s*\d+\s*(?:NEW\s*)?", re.I)


def _extract_address(text: str) -> str | None:
    """
    Find the shortest street-address-like pattern in text. We try matching
    starting at each plausible street-number position and pick the shortest
    result, which avoids greedy false matches like
        "498 House 221 W Ring St"  →  "221 W Ring St"
    where "498" is actually the trailing digits of a price like "$1,498".
    """
    candidates: list[str] = []
    # Only consider numbers that are followed by a space — skips "1,498" cases
    for m in re.finditer(r"\b\d{1,5}(?=\s)", text):
        sub = text[m.start():]
        m2 = _ADDR_RE.match(sub)
        if m2:
            candidates.append(m2.group(0).strip())
    return min(candidates, key=len) if candidates else None


def _stable_id_for_card(card_text: str, source_url: str) -> str:
    """
    Build a deterministic URL fragment for a card whose surrounding <a> link
    points back to the search page rather than a per-listing page.
    Uses the street address if present; otherwise an 8-char content hash.
    """
    addr = _extract_address(card_text)
    if addr:
        slug = re.sub(r"\s+", "-", addr.strip().lower())
        slug = re.sub(r"[^a-z0-9-]", "", slug)[:50]
        return f"{source_url}#{slug}"
    h = _hashlib.md5(card_text.strip().encode()).hexdigest()[:10]
    return f"{source_url}#h-{h}"


def _parse_listing_html(
    html: str, source_name: str, url: str, listing_selector: str
) -> tuple[list[dict], int]:
    """Pure-function parser shared between httpx and Playwright engines."""
    soup = BeautifulSoup(html, "lxml")
    elements: list = []
    for sel in [s.strip() for s in listing_selector.split(",")]:
        elements = soup.select(sel)
        if elements:
            break
    if not elements:
        elements = soup.find_all(["article", "li", "tr"], limit=50)

    listings = []
    skipped = 0
    for el in elements:
        text = el.get_text(" ", strip=True)
        if not is_likely_listing(text):
            skipped += 1
            continue
        if classify_non_rental(text):
            skipped += 1
            continue
        # PM-site geo filter: ZIP-based + city-name in title context
        if classify_out_of_scope(text[:200], "", body=text):
            skipped += 1
            continue

        link = el.find("a", href=True)
        # When the card's link points back to the source page (or no link at all),
        # synthesize a stable per-card URL so dedup-by-URL works correctly.
        if link:
            raw_href = link["href"]
            href = resolve_href(raw_href, url)
            if href.rstrip("/") == url.rstrip("/") or raw_href in ("#", "/"):
                href = _stable_id_for_card(text, url)
        else:
            href = _stable_id_for_card(text, url)

        listing = blank_listing(source_name, href)
        # Title: prefer the shortest extracted street address from card text
        # (cleanest signal). Fall back to link text (minus pagination prefix).
        link_text = link.get_text(strip=True) if link else ""
        link_text = _PAGINATION_PREFIX_RE.sub("", link_text).strip()
        addr = _extract_address(text)
        if addr:
            listing["title"] = addr
        elif 10 < len(link_text) < 150:
            listing["title"] = link_text
        else:
            listing["title"] = text[:80]
        listing["description"] = text[:500]
        listing["neighborhood"] = detect_neighborhood(text)
        listing["duplex_flag"] = int(detect_duplex(text))
        listing["rent"] = extract_rent(text)
        listing["beds"] = extract_beds(text)
        listing["listing_type"] = classify_listing_type(listing["title"], text, source_name)
        listing["raw_data"] = json.dumps({"raw_text": text[:1000], "source_url": url})
        listings.append(listing)
    return listings, skipped


def scrape_html_site(
    client: httpx.Client,
    source_name: str,
    url: str,
    listing_selector: str,
    verify_ssl: bool = True,
) -> list[dict]:
    """Fetch a static-HTML site via httpx, then parse."""
    print(f"  [HTML] {source_name}")
    try:
        if not verify_ssl:
            resp = httpx.get(
                url, headers={"User-Agent": PM_UA}, timeout=15,
                follow_redirects=True, verify=False,
            )
        else:
            resp = client.get(url, headers={"User-Agent": PM_UA}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"    ! fetch failed: {e}")
        return []

    listings, skipped = _parse_listing_html(resp.text, source_name, url, listing_selector)
    print(f"    → {len(listings)} listings ({skipped} skipped)")
    return listings


def scrape_playwright_site(
    browser,
    source_name: str,
    url: str,
    listing_selector: str,
    wait_extra_ms: int = 2000,
    scroll: bool = True,
) -> list[dict]:
    """Fetch a JS-rendered site via a shared headless Chromium, then parse."""
    print(f"  [JS]   {source_name}")
    try:
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30_000)
        if scroll:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        if wait_extra_ms:
            page.wait_for_timeout(wait_extra_ms)
        html = page.content()
        page.close()
    except Exception as e:
        print(f"    ! fetch failed: {e}")
        return []
    listings, skipped = _parse_listing_html(html, source_name, url, listing_selector)
    print(f"    → {len(listings)} listings ({skipped} skipped)")
    return listings


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run():
    init_db()

    with open("sources.yaml") as f:
        config = yaml.safe_load(f)

    total_new = 0
    total_reseen = 0

    def _ingest(listings):
        nonlocal total_new, total_reseen
        for listing in listings:
            if upsert_listing(listing):
                total_new += 1
            else:
                total_reseen += 1

    with httpx.Client(follow_redirects=True, timeout=15) as client:
        # Craigslist search pages
        for source in config.get("craigslist_html", []):
            _ingest(scrape_craigslist(client, source["name"], source["url"]))
            time.sleep(REQUEST_DELAY)

        # Static-HTML PM and boutique sites
        for source in config.get("html_sites", []):
            _ingest(scrape_html_site(
                client,
                source["name"],
                source["url"],
                source["listing_selector"],
                verify_ssl=source.get("verify_ssl", True),
            ))
            time.sleep(REQUEST_DELAY)

        # Enrich Craigslist with detail pages; this also drops non-rentals
        # that only become visible once we have the body text.
        enrich_craigslist_details(client)

    # JavaScript-rendered PM sites — only spin up Chromium if we have any
    js_sites = config.get("playwright_sites", [])
    if js_sites:
        from playwright.sync_api import sync_playwright
        print(f"\nLaunching headless Chromium for {len(js_sites)} JS-rendered site(s)…")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for source in js_sites:
                _ingest(scrape_playwright_site(
                    browser,
                    source["name"],
                    source["url"],
                    source["listing_selector"],
                    wait_extra_ms=source.get("wait_extra_ms", 2000),
                    scroll=source.get("scroll", True),
                ))
                time.sleep(REQUEST_DELAY)
            browser.close()

    # Retroactive sweeps — catch anything that pre-dated the filters or
    # whose detail page was already fetched in an earlier run.
    clean_existing_non_rentals()
    clean_out_of_scope_listings()

    print(f"\nDone. {total_new} new listings, {total_reseen} re-sighted.")


if __name__ == "__main__":
    run()
