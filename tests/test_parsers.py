"""Unit tests for the pure parsing helpers in scraper.py."""
import pytest
from scraper import (
    detect_neighborhood,
    detect_duplex,
    extract_rent,
    extract_beds,
    is_likely_listing,
    is_residential_rental,
    classify_non_rental,
    classify_out_of_scope,
    is_in_scope,
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


class TestNonRentalFilter:
    # ---- Things that should be REJECTED -----------------------------------
    @pytest.mark.parametrize("text,expected_label", [
        ("Live In Caregiver Wanted - Free housing", "caregiver job"),
        ("Live-in nanny wanted, room provided",     "live-in job"),
        ("Seeking a companion to hangout with",     "personals"),
        ("Seeking SWF for relationship",            "personals"),
        ("ISO room near downtown",                  "ISO post"),
        ("In search of a room to rent in Tosa",     "ISO post"),
        ("Salon chair rental - prime location",     "salon space"),
        ("Hair stylist booth available",            "salon space"),
        ("Massage room for rent, private entrance", "wellness space"),
        ("Wellness studio space available",         "wellness space"),
        ("Office space for rent in downtown",       "office space"),
        ("Professional suite for lease",            "professional office"),
        ("Commercial space available",              "commercial space"),
        ("Retail location for rent",                "commercial space"),
        ("Storefront available, high traffic",      "storefront"),
        ("Coworking desk available",                "coworking"),
        ("Event venue for rent - weddings welcome", "event space"),
        ("Yoga studio for rent, fully equipped",    "fitness studio"),
    ])
    def test_rejects_non_rentals(self, text, expected_label):
        label = classify_non_rental(text)
        assert label == expected_label, (
            f"expected {expected_label!r}, got {label!r} for {text!r}"
        )
        assert not is_residential_rental(text)

    # ---- Critical: things that should NOT be rejected (false-positive guards) ----
    @pytest.mark.parametrize("text", [
        # Real listings we saw in the DB that "sound" commercial
        "Wauwatosa 2 Bdrm, 1.5 Bath Near MCW",
        "Primary suite with walk-in closet, en suite bath",
        "Located near the thriving business district of Tosa Village",
        "Large 2-room master suite + private bathroom in quiet suburban house",
        "Furnished room in quiet apartment, close to wellness center and yoga studio",
        "Spacious 2 bedroom near the salon and coffee shop on Brady",
        "Beautiful studio apartment, heat included",
        "Office in the spare bedroom — perfect work-from-home setup",
        "Looking for a roommate - $800/mo includes utilities",
        # ISO-adjacent but not ISO
        "Roommate wanted for 2BR apartment",
        # Commercial-keyword-but-residential
        "Quiet building with a barber on the ground floor",
        "Walking distance to massage therapist and yoga classes",
    ])
    def test_accepts_residential(self, text):
        label = classify_non_rental(text)
        assert label is None, f"rejected {text!r} as {label!r} — false positive"
        assert is_residential_rental(text)

    def test_empty_text_is_residential(self):
        # Don't reject a listing just because we have no text to check yet
        assert is_residential_rental("") is True
        assert is_residential_rental(None) is True


class TestGeographicFilter:
    # ---- Out-of-scope locations (should reject) ----------------------------
    @pytest.mark.parametrize("title,location,expected", [
        # Bare location field matches
        ("3BR home",                       "Waukesha",                      "waukesha"),
        ("Spacious apartment",             "Brookfield",                    "brookfield"),
        ("Updated 2 bedroom",              "Bay View",                      "bay view"),
        # Multi-city location strings — set iteration order is arbitrary,
        # so accept any of the cities as the label (test reads as a set membership)
        ("Looking for Roommate",           "Sussex Lisbon Pewaukee area",   {"sussex", "lisbon", "pewaukee"}),
        ("Apartment",                      "Oconomowoc, Waukesha, Brookfield", {"oconomowoc", "waukesha", "brookfield"}),
        ("2BR duplex",                     "Watertown",                     "watertown"),
        ("Cozy room",                      "Cudahy",                        "cudahy"),
        ("Bedroom",                        "Shorewood",                     "shorewood"),
        ("Apt",                            "East Side Milwaukee",           "east side milwaukee"),
        ("Studio",                         "Lower East Side",               "lower east side"),
        ("Loft",                           "Walker's Point",                "walker's point"),
        # Comma + WI cleanup
        ("Place",                          "Waukesha, WI",                  "waukesha"),
        # Title at start
        ("Bay View - 2 bedroom lower - 7/1",   "Milwaukee",                 "bay view"),
        ("Studio Apartment - South Milwaukee", "",                          "south milwaukee"),
        ("Sussex Furnished Room $800/mo",      "",                          "sussex"),
        # "in <city>"
        ("Updated apartment in Waukesha",      "Milwaukee",                 "waukesha"),
        # "<city> + dwelling word"
        ("Cudahy duplex 3BR",                  "",                          "cudahy"),
    ])
    def test_rejects_out_of_scope(self, title, location, expected):
        label = classify_out_of_scope(title, location)
        if isinstance(expected, set):
            assert label in expected, f"expected one of {expected!r}, got {label!r}"
        else:
            assert label == expected, f"expected {expected!r}, got {label!r}"
        assert not is_in_scope(title, location)

    # ---- In-scope locations (must NOT be rejected) -------------------------
    @pytest.mark.parametrize("title,location", [
        # Direct Tosa
        ("Wauwatosa 2 Bdrm, 1.5 Bath Near MCW", "Wauwatosa"),
        ("Tosa upper duplex",                   ""),
        ("Cute apartment",                      "Wauwatosa"),
        # West Allis — immediate Tosa border
        ("2BR apt",                             "West Allis"),
        # Riverwest exception
        ("Room Available Now in Riverwest Duplex", "Riverwest"),
        ("Furnished room",                       "Riverwest"),
        # ZIP-based identification
        ("Duplex in 53213",                     "Milwaukee"),
        ("Apartment near 53212",                "Milwaukee"),
        # MCW / Froedtert keyword
        ("Bedroom near MCW",                    "Milwaukee"),
        ("Studio walking distance to Froedtert", ""),
        # AFF / Brewers stadium
        ("Apartment near American Family Field", "Milwaukee"),
        ("2BR by Miller Park",                  ""),
        # Tosa neighborhoods
        ("Story Hill 2BR upper",                "Milwaukee"),
        ("Washington Heights apartment",        "Milwaukee"),
        # Ambiguous "Milwaukee" alone — keep, user can manually pass
        ("3BR lower duplex",                    "Milwaukee"),
    ])
    def test_accepts_in_scope(self, title, location):
        label = classify_out_of_scope(title, location)
        assert label is None, f"rejected {title!r}/{location!r} as {label!r}"
        assert is_in_scope(title, location)

    # ---- Critical street-name false-positive guards ------------------------
    @pytest.mark.parametrize("title,location", [
        # Greenfield Ave is a major Milwaukee artery, not Greenfield WI suburb
        ("11616 W. Greenfield Ave",             ""),
        ("Apartment at Greenfield Ave",         ""),
        ("Listing",                             "11616 W. Greenfield Ave"),
        # Franklin Place / Franklin St are Milwaukee streets
        ("2BRD upper duplex Franklin Place",    ""),
        ("Studio at Franklin St",               ""),
        ("Place",                               "1454 N. Franklin Place"),
        # Jefferson St is a Milwaukee street
        ("Apartment on Jefferson St",           ""),
        # Port Washington Rd is a Milwaukee road
        ("Studio near Port Washington Rd",      ""),
    ])
    def test_street_names_are_not_cities(self, title, location):
        label = classify_out_of_scope(title, location)
        assert label is None, (
            f"false positive: rejected {title!r}/{location!r} (label={label!r}) "
            f"— that's a street name, not a city"
        )

    # ---- Out-of-scope ZIP-code detection -----------------------------------
    @pytest.mark.parametrize("title,location,expected_zip", [
        ("Apartment downtown",          "Milwaukee, WI 53202",          "53202"),
        ("Studio in Walker's Point",    "Milwaukee 53204",              "53204"),
        ("2BR Bay View",                "Milwaukee 53207",              "53207"),
        ("Place",                       "1454 N. Franklin Pl 53202",    "53202"),
    ])
    def test_rejects_out_of_scope_zips(self, title, location, expected_zip):
        label = classify_out_of_scope(title, location)
        assert label == f"ZIP {expected_zip}", (
            f"expected 'ZIP {expected_zip}', got {label!r}"
        )

    def test_zip_in_description_body_rejected(self):
        """Body-text ZIP catch: title/address don't mention East Side but body does."""
        label = classify_out_of_scope(
            title="2BRD upper duplex Franklin Place",
            location="1454 N. Franklin Place",
            body="Beautiful unit at 1454 N. Franklin Place, Milwaukee, WI 53202. 2BR, 1BA.",
        )
        assert label == "ZIP 53202"

    def test_in_scope_keyword_in_body_overrides_oos_zip(self):
        """If body says Wauwatosa, that wins even if an OOS ZIP appears."""
        label = classify_out_of_scope(
            title="2BR apartment",
            location="Milwaukee",
            body="Apartment in Wauwatosa, with mailing address 53202 occasionally used by landlord.",
        )
        assert label is None

    def test_empty_inputs(self):
        assert classify_out_of_scope("", "") is None
        assert classify_out_of_scope(None, None) is None  # type: ignore


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
