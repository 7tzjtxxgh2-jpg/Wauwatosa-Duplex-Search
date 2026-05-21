"""
Chunk 1: Craigslist RSS + small PM site scraper.
Normalizes all sources to a common schema and upserts into SQLite.

Run manually:  python scraper.py
Via cron/GH Actions: see .github/workflows/scrape.yml
"""

from __future__ import annotations

import json
import re
import time
import yaml
import feedparser
import httpx
from bs4 import BeautifulSoup
from db import init_db, upsert_listing

UA = "wauwatosa-rental-search/1.0 personal-use (contact: jacklaufenberg@icloud.com)"
HEADERS = {"User-Agent": UA}
REQUEST_DELAY = 1.5  # seconds between HTML requests; be polite

DUPLEX_KEYWORDS = {
    "duplex", "upper", "lower", "flat", "2-family", "two-family",
    "upper unit", "lower unit", "upper flat", "lower flat",
}

TARGET_ZIPS = {"53213", "53226", "53208", "53210", "53222"}

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
    text = text.lower()
    for pattern, label in NEIGHBORHOOD_PATTERNS:
        if re.search(pattern, text, re.I):
            return label
    return None


def detect_duplex(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in DUPLEX_KEYWORDS)


def extract_rent(text: str) -> int | None:
    """Pull the first dollar amount from a string."""
    match = re.search(r"\$\s*([\d,]+)", text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def extract_beds(text: str) -> str | None:
    match = re.search(r"(\d+)\s*(?:br|bed|bedroom)", text, re.I)
    if match:
        return match.group(1)
    return None


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
# Craigslist RSS
# ---------------------------------------------------------------------------

def scrape_craigslist_rss(source_name: str, url: str) -> list[dict]:
    print(f"  [RSS] {source_name}")
    feed = feedparser.parse(url, request_headers=HEADERS)
    listings = []
    for entry in feed.entries:
        text = f"{entry.get('title', '')} {entry.get('summary', '')}"
        listing = blank_listing(source_name, entry.get("link", ""))
        listing["title"] = entry.get("title")
        listing["description"] = BeautifulSoup(
            entry.get("summary", ""), "lxml"
        ).get_text(" ", strip=True)
        listing["neighborhood"] = detect_neighborhood(text)
        listing["duplex_flag"] = int(detect_duplex(text))
        listing["rent"] = extract_rent(text)
        listing["beds"] = extract_beds(text)
        listing["available_date"] = entry.get("published")
        listing["raw_data"] = json.dumps({
            "title": entry.get("title"),
            "summary": entry.get("summary"),
            "published": entry.get("published"),
        })
        listings.append(listing)
    print(f"    → {len(listings)} entries")
    return listings


# ---------------------------------------------------------------------------
# Generic HTML scraper for PM sites
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> BeautifulSoup | None:
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        print(f"    ! fetch failed: {e}")
        return None


def scrape_html_site(source_name: str, url: str, listing_selector: str) -> list[dict]:
    print(f"  [HTML] {source_name} — {url}")
    soup = fetch_html(url)
    if not soup:
        return []

    # Try each CSS selector in the comma-separated list
    selectors = [s.strip() for s in listing_selector.split(",")]
    elements = []
    for sel in selectors:
        elements = soup.select(sel)
        if elements:
            break

    # Fallback: grab all links that look like individual listing pages
    if not elements:
        elements = soup.find_all(["article", "li", "tr"], limit=50)

    listings = []
    for el in elements:
        text = el.get_text(" ", strip=True)
        if not text or len(text) < 20:
            continue

        # Try to find a link inside the element
        link = el.find("a", href=True)
        href = link["href"] if link else url
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"

        listing = blank_listing(source_name, href)
        listing["title"] = (link.get_text(strip=True) if link else None) or text[:80]
        listing["description"] = text[:500]
        listing["neighborhood"] = detect_neighborhood(text)
        listing["duplex_flag"] = int(detect_duplex(text))
        listing["rent"] = extract_rent(text)
        listing["beds"] = extract_beds(text)
        listing["raw_data"] = json.dumps({"raw_text": text[:1000], "source_url": url})
        listings.append(listing)

    print(f"    → {len(listings)} elements found")
    return listings


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run():
    init_db()

    with open("sources.yaml") as f:
        config = yaml.safe_load(f)

    total_new = 0

    # Craigslist RSS (no delay needed — RSS is designed for polling)
    for source in config.get("craigslist_rss", []):
        listings = scrape_craigslist_rss(source["name"], source["url"])
        for l in listings:
            if upsert_listing(l):
                total_new += 1

    # HTML sites (rate-limited)
    for source in config.get("html_sites", []):
        listings = scrape_html_site(
            source["name"], source["url"], source["listing_selector"]
        )
        for l in listings:
            if upsert_listing(l):
                total_new += 1
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {total_new} new listings added to database.")


if __name__ == "__main__":
    run()
