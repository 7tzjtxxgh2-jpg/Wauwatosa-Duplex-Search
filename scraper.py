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

            update_description(
                row["id"],
                details["description"],
                address=details["address"],
                available_date=details["available_date"],
            )
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


# ---------------------------------------------------------------------------
# Generic HTML scraper for PM sites
# ---------------------------------------------------------------------------

def scrape_html_site(
    client: httpx.Client,
    source_name: str,
    url: str,
    listing_selector: str,
    verify_ssl: bool = True,
) -> list[dict]:
    print(f"  [HTML] {source_name}")
    try:
        # httpx.Client doesn't accept verify per-request, so for the rare
        # self-signed-cert site we make a one-off request outside the pool.
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

    soup = BeautifulSoup(resp.text, "lxml")
    elements = []
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

        link = el.find("a", href=True)
        href = resolve_href(link["href"], url) if link else url

        listing = blank_listing(source_name, href)
        listing["title"] = (link.get_text(strip=True) if link else None) or text[:80]
        listing["description"] = text[:500]
        listing["neighborhood"] = detect_neighborhood(text)
        listing["duplex_flag"] = int(detect_duplex(text))
        listing["rent"] = extract_rent(text)
        listing["beds"] = extract_beds(text)
        listing["raw_data"] = json.dumps({"raw_text": text[:1000], "source_url": url})
        listings.append(listing)

    print(f"    → {len(listings)} listings ({skipped} non-listings skipped)")
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

    with httpx.Client(follow_redirects=True, timeout=15) as client:
        # Craigslist search pages
        for source in config.get("craigslist_html", []):
            listings = scrape_craigslist(client, source["name"], source["url"])
            for listing in listings:
                if upsert_listing(listing):
                    total_new += 1
                else:
                    total_reseen += 1
            time.sleep(REQUEST_DELAY)

        # PM and boutique sites
        for source in config.get("html_sites", []):
            listings = scrape_html_site(
                client,
                source["name"],
                source["url"],
                source["listing_selector"],
                verify_ssl=source.get("verify_ssl", True),
            )
            for listing in listings:
                if upsert_listing(listing):
                    total_new += 1
                else:
                    total_reseen += 1
            time.sleep(REQUEST_DELAY)

        # Enrich Craigslist with detail pages; this also drops non-rentals
        # that only become visible once we have the body text.
        enrich_craigslist_details(client)

    # Retroactive sweep — catches anything that pre-dated the filter or
    # whose description was already fetched in an earlier run.
    clean_existing_non_rentals()

    print(f"\nDone. {total_new} new listings, {total_reseen} re-sighted.")


if __name__ == "__main__":
    run()
