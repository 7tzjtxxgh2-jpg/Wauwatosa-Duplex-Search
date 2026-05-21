"""
Chunk 1: Craigslist HTML + small PM site scraper.
Normalizes all sources to a common schema and upserts into SQLite.

Run manually:  python scraper.py
Via cron/GH Actions: see .github/workflows/scrape.yml
"""

from __future__ import annotations

import json
import re
import time
import yaml
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from db import init_db, upsert_listing

# Craigslist tolerates a real browser UA; custom UA triggers 403 on their search
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
PM_UA = "wauwatosa-rental-search/1.0 personal-use (contact: jacklaufenberg@icloud.com)"

REQUEST_DELAY = 1.5  # seconds between requests; be polite

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
# Helpers
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
    if match:
        val = int(match.group(1).replace(",", ""))
        # Sanity check: ignore implausibly low/high values
        return val if 300 < val < 10000 else None
    return None


def extract_beds(text: str) -> str | None:
    match = re.search(r"(\d+)\s*(?:br|bed|bedroom)", text, re.I)
    return match.group(1) if match else None


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


def is_likely_listing(text: str) -> bool:
    """Return True only if text looks like a rental listing, not a nav/marketing item."""
    lower = text.lower()
    # Must have a price OR at least two rental keywords
    has_price = bool(re.search(r"\$\s*\d+", text))
    keyword_hits = sum(1 for kw in LISTING_KEYWORDS if kw in lower)
    if not has_price and keyword_hits < 2:
        return False
    # Reject known non-listing phrases
    if any(phrase in lower for phrase in NON_LISTING_PHRASES):
        return False
    # Reject very short items (nav links) and very long items (full page dumps)
    if len(text) < 30 or len(text) > 3000:
        return False
    return True


def resolve_href(href: str, base_url: str) -> str:
    if href.startswith("http"):
        return href
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}{href}"


# ---------------------------------------------------------------------------
# Craigslist HTML scraper
# ---------------------------------------------------------------------------

def scrape_craigslist(source_name: str, url: str) -> list[dict]:
    print(f"  [CL] {source_name}")
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": BROWSER_UA},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"    ! fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    elements = soup.select("li.cl-static-search-result")
    if not elements:
        # Fallback for older Craigslist markup
        elements = soup.select("li.result-row")

    listings = []
    for el in elements:
        link = el.find("a", href=True)
        if not link:
            continue
        href = resolve_href(link["href"], url)

        title_el = el.select_one("div.title, span#titletextonly")
        price_el = el.select_one("div.price, span.price")
        loc_el   = el.select_one("div.location, span.result-hood")

        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        price_text = price_el.get_text(strip=True) if price_el else ""
        location = loc_el.get_text(strip=True) if loc_el else ""

        full_text = f"{title} {price_text} {location}"

        listing = blank_listing(source_name, href)
        listing["title"] = title
        listing["neighborhood"] = detect_neighborhood(full_text) or detect_neighborhood(location)
        listing["duplex_flag"] = int(detect_duplex(title))
        listing["rent"] = extract_rent(price_text) or extract_rent(full_text)
        listing["beds"] = extract_beds(title)
        listing["description"] = title
        listing["raw_data"] = json.dumps({
            "title": title,
            "price": price_text,
            "location": location,
            "source_url": url,
        })
        listings.append(listing)

    print(f"    → {len(listings)} listings")
    return listings


# ---------------------------------------------------------------------------
# Generic HTML scraper for PM sites
# ---------------------------------------------------------------------------

def fetch_html(url: str, verify_ssl: bool = True) -> BeautifulSoup | None:
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": PM_UA},
            timeout=15,
            follow_redirects=True,
            verify=verify_ssl,
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        print(f"    ! fetch failed: {e}")
        return None


def scrape_html_site(source_name: str, url: str, listing_selector: str, verify_ssl: bool = True) -> list[dict]:
    print(f"  [HTML] {source_name}")
    soup = fetch_html(url, verify_ssl=verify_ssl)
    if not soup:
        return []

    selectors = [s.strip() for s in listing_selector.split(",")]
    elements = []
    for sel in selectors:
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

    # Craigslist HTML search pages
    for source in config.get("craigslist_html", []):
        listings = scrape_craigslist(source["name"], source["url"])
        for listing in listings:
            if upsert_listing(listing):
                total_new += 1
        time.sleep(REQUEST_DELAY)

    # PM and boutique sites
    for source in config.get("html_sites", []):
        listings = scrape_html_site(
            source["name"],
            source["url"],
            source["listing_selector"],
            verify_ssl=source.get("verify_ssl", True),
        )
        for listing in listings:
            if upsert_listing(listing):
                total_new += 1
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {total_new} new listings added to database.")


if __name__ == "__main__":
    run()
