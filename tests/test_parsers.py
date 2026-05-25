"""Unit tests for the pure parsing helpers in scraper.py."""
import pytest
from scraper import (
    detect_neighborhood,
    detect_duplex,
    extract_rent,
    extract_beds,
    is_likely_listing,
    resolve_href,
)


class TestExtractRent:
    def test_simple_dollar_amount(self):
        assert extract_rent("$1,350 / 2br") == 1350

    def test_no_comma(self):
        assert extract_rent("rent is $850") == 850

    def test_no_dollar_sign_returns_none(self):
        assert extract_rent("very nice apartment") is None

    def test_rejects_too_low(self):
        # $99 isn't a rent; might be a fee or a typo
        assert extract_rent("$99 deposit") is None

    def test_rejects_too_high(self):
        # $25,000 isn't a Milwaukee rent
        assert extract_rent("$25,000 for sale") is None

    def test_picks_first_dollar_amount(self):
        # Multiple prices — first one wins. Acceptable trade-off.
        assert extract_rent("$1,200/mo, $50 pet fee") == 1200


class TestExtractBeds:
    def test_br_form(self):
        assert extract_beds("2BR / 1BA upper") == "2"

    def test_bedroom_form(self):
        assert extract_beds("3 bedroom duplex") == "3"

    def test_no_match(self):
        assert extract_beds("studio apartment") is None

    def test_case_insensitive(self):
        assert extract_beds("1 BED") == "1"


class TestDetectNeighborhood:
    def test_wauwatosa(self):
        assert detect_neighborhood("Wauwatosa upper duplex") == "Wauwatosa"

    def test_zip_53213(self):
        assert detect_neighborhood("Near 53213, MCW area") == "Wauwatosa"

    def test_story_hill(self):
        assert detect_neighborhood("Story Hill 2BR") == "Story Hill"

    def test_no_match(self):
        assert detect_neighborhood("Milwaukee east side") is None

    def test_case_insensitive(self):
        assert detect_neighborhood("WAUWATOSA") == "Wauwatosa"


class TestDetectDuplex:
    @pytest.mark.parametrize("text", [
        "upper duplex available",
        "lower flat in Tosa",
        "2-family home",
        "Upper unit, hardwood floors",
    ])
    def test_positive(self, text):
        assert detect_duplex(text) is True

    @pytest.mark.parametrize("text", [
        "single-family home",
        "studio apartment",
        "modern condo",
    ])
    def test_negative(self, text):
        assert detect_duplex(text) is False


class TestIsLikelyListing:
    def test_real_listing_with_price(self):
        assert is_likely_listing(
            "Wauwatosa 2 Bdrm, 1.5 Bath Near MCW $1,350 monthly lease available"
        ) is True

    def test_navigation_item_rejected(self):
        # Sonnet originally pulled in these as "listings"
        assert is_likely_listing("CLIENT ONBOARDING TIMELINE") is False
        assert is_likely_listing("INVESTORS") is False
        assert is_likely_listing("ACCOUNTING & BILLPAY") is False

    def test_marketing_phrase_rejected(self):
        assert is_likely_listing(
            "Our services include property management and tenant portal access"
        ) is False

    def test_too_short_rejected(self):
        assert is_likely_listing("$500") is False

    def test_too_long_rejected(self):
        # Full-page text dumps should be rejected
        assert is_likely_listing("apartment " * 500) is False

    def test_keywords_without_price_passes_if_enough(self):
        assert is_likely_listing(
            "Spacious bedroom available with lease, monthly rent flexible"
        ) is True


class TestResolveHref:
    def test_absolute_url_passes_through(self):
        assert (
            resolve_href("https://example.com/a", "https://other.com/b")
            == "https://example.com/a"
        )

    def test_relative_url_resolved(self):
        assert (
            resolve_href("/listings/42", "https://www.example.com/search")
            == "https://www.example.com/listings/42"
        )
