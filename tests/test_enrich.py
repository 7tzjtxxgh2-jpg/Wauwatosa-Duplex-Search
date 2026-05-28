"""
Tests for the enrichment pipeline. The Anthropic API is mocked — these verify
prompt assembly, score clamping, field mapping, and DB storage without spending
tokens or needing a key.
"""
import json
import os
from types import SimpleNamespace

import pytest

# Ensure a dummy key so `from anthropic import Anthropic` construction never errors
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-dummy")

from enrich import (
    build_user_message,
    enrich_listing,
    _clamp_score,
    ListingEnrichment,
    SOLO_BUDGET,
)


def sample_listing(**overrides) -> dict:
    base = {
        "id": 1,
        "source": "craigslist_duplex",
        "title": "Upper 2BR duplex",
        "rent": 1100,
        "beds": "2",
        "neighborhood": "Wauwatosa",
        "address": "1234 N 68th St",
        "description": "Charming upper duplex in Wauwatosa. Garage, in-unit laundry, cats OK.",
    }
    base.update(overrides)
    return base


class TestBuildUserMessage:
    def test_includes_key_fields(self):
        msg = build_user_message(sample_listing())
        assert "craigslist_duplex" in msg
        assert "Upper 2BR duplex" in msg
        assert "$1100" in msg
        assert "1234 N 68th St" in msg
        assert "Charming upper duplex" in msg

    def test_handles_missing_fields(self):
        msg = build_user_message({"source": "x", "title": None, "description": None})
        assert "(none)" in msg
        # Should not raise on missing rent/beds/address


class TestClampScore:
    @pytest.mark.parametrize("raw,expected", [
        (5, 5), (0, 0), (10, 10), (-3, 0), (11, 10), (99, 10),
    ])
    def test_clamps(self, raw, expected):
        assert _clamp_score(raw) == expected


class TestEnrichListing:
    """Mock client.messages.parse to return a known ListingEnrichment."""

    def _mock_client(self, enrichment: ListingEnrichment, in_tok=100, out_tok=50):
        parsed_response = SimpleNamespace(
            parsed_output=enrichment,
            usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
        )
        messages = SimpleNamespace(parse=lambda **kwargs: parsed_response)
        return SimpleNamespace(messages=messages)

    def test_maps_fields_correctly(self):
        enrichment = ListingEnrichment(
            clean_title="2BR upper duplex on N 68th St",
            beds=2, baths=1.0, rent=1100, neighborhood="Wauwatosa",
            is_duplex=True, pet_policy="cats_only", parking="garage",
            laundry="in_unit", available_date="2026-09-01",
            fit_score=9, fit_reason="Great fit: duplex, garage, under budget.",
            concerns=[], highlights=["garage", "in-unit laundry", "under budget"],
        )
        client = self._mock_client(enrichment)
        fields = enrich_listing(client, sample_listing())

        assert fields["ai_summary"] == "2BR upper duplex on N 68th St"
        assert fields["fit_score"] == 9
        assert fields["duplex_flag"] == 1
        assert fields["pet_policy"] == "cats_only"
        assert fields["parking"] == "garage"
        assert fields["laundry"] == "in_unit"
        assert json.loads(fields["highlights"]) == ["garage", "in-unit laundry", "under budget"]
        assert json.loads(fields["concerns"]) == []
        assert "_usage" in fields

    def test_clamps_out_of_range_score(self):
        enrichment = ListingEnrichment(
            clean_title="x", is_duplex=False, pet_policy="unknown",
            parking="unknown", laundry="unknown",
            fit_score=15, fit_reason="model returned out-of-range",
        )
        client = self._mock_client(enrichment)
        fields = enrich_listing(client, sample_listing())
        assert fields["fit_score"] == 10

    def test_falls_back_to_scraper_values_when_model_returns_none(self):
        enrichment = ListingEnrichment(
            clean_title="x", beds=None, rent=None, neighborhood=None,
            is_duplex=False, pet_policy="unknown", parking="unknown",
            laundry="unknown", fit_score=5, fit_reason="ok",
        )
        client = self._mock_client(enrichment)
        listing = sample_listing(rent=1100, beds="2", neighborhood="Wauwatosa")
        fields = enrich_listing(client, listing)
        # When model returns None, keep the scraper's values
        assert fields["rent"] == 1100
        assert fields["beds"] == "2"
        assert fields["neighborhood"] == "Wauwatosa"


class TestRubricInvariants:
    """The system prompt must encode the user's hard rules."""

    def test_budget_in_prompt(self):
        from enrich import SYSTEM_PROMPT
        assert str(SOLO_BUDGET) in SYSTEM_PROMPT
        assert "1200" in SYSTEM_PROMPT

    def test_dealbreakers_in_prompt(self):
        from enrich import SYSTEM_PROMPT
        lower = SYSTEM_PROMPT.lower()
        assert "over budget" in lower
        assert "pet" in lower
        assert "duplex" in lower
        assert "parking" in lower or "garage" in lower
