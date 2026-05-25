"""
Tests against saved Craigslist HTML fixtures.

When Craigslist changes their markup, these will break loudly and you'll
know to update the selectors. Re-capture fixtures with:

    curl -A "<browser UA>" "<URL>" > tests/fixtures/craigslist_search.html
"""
from pathlib import Path
import pytest

from scraper import parse_craigslist_search, parse_craigslist_detail

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_html() -> str:
    return (FIXTURES / "craigslist_search.html").read_text()


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "craigslist_detail.html").read_text()


class TestSearchPageParser:
    def test_finds_listings(self, search_html):
        listings = parse_craigslist_search(
            search_html,
            "craigslist_wauwatosa",
            "https://milwaukee.craigslist.org/search/apa?query=wauwatosa",
        )
        # The saved snapshot had 37 listings; allow some flex for future refetches
        assert len(listings) >= 5, "expected at least a handful of listings"

    def test_listings_have_required_fields(self, search_html):
        listings = parse_craigslist_search(
            search_html, "craigslist_wauwatosa", "https://milwaukee.craigslist.org/"
        )
        first = listings[0]
        assert first["url"].startswith("http")
        assert first["source"] == "craigslist_wauwatosa"
        assert first["title"] is not None and len(first["title"]) > 0

    def test_most_listings_have_rent(self, search_html):
        listings = parse_craigslist_search(
            search_html, "craigslist_wauwatosa", "https://milwaukee.craigslist.org/"
        )
        with_rent = [l for l in listings if l["rent"] is not None]
        assert len(with_rent) / len(listings) > 0.5, "majority should have rent"

    def test_at_least_one_has_wauwatosa_neighborhood(self, search_html):
        """The query was 'wauwatosa' — at least some hits should match."""
        listings = parse_craigslist_search(
            search_html, "craigslist_wauwatosa", "https://milwaukee.craigslist.org/"
        )
        hoods = {l["neighborhood"] for l in listings if l["neighborhood"]}
        assert "Wauwatosa" in hoods


class TestDetailPageParser:
    def test_extracts_body_description(self, detail_html):
        details = parse_craigslist_detail(detail_html)
        assert details["description"], "detail page must yield a description"
        assert len(details["description"]) > 50, "description should be substantive"

    def test_strips_qr_code_preamble(self, detail_html):
        details = parse_craigslist_detail(detail_html)
        assert "QR Code Link" not in details["description"]

    def test_description_capped(self, detail_html):
        details = parse_craigslist_detail(detail_html)
        assert len(details["description"]) <= 2000
