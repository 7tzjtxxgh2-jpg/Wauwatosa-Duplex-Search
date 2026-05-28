"""Tests for the database layer. Uses a tmp_path SQLite file per test."""
import os
import sqlite3
import pytest

# Force the DB to a tmp location BEFORE importing db
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    # Re-import db so DB_PATH is re-read
    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_module


def make_listing(url: str = "https://example.com/a", **overrides) -> dict:
    base = {
        "url": url,
        "source": "test",
        "title": "Test listing",
        "address": None,
        "neighborhood": None,
        "beds": "2",
        "baths": None,
        "rent": 1200,
        "available_date": None,
        "contact": None,
        "description": "test",
        "duplex_flag": 0,
        "raw_data": None,
        "listing_type": "rental",
    }
    base.update(overrides)
    return base


class TestUpsert:
    def test_new_url_returns_true(self, tmp_db):
        assert tmp_db.upsert_listing(make_listing()) is True

    def test_duplicate_url_returns_false(self, tmp_db):
        listing = make_listing()
        tmp_db.upsert_listing(listing)
        assert tmp_db.upsert_listing(listing) is False

    def test_resighting_bumps_times_seen(self, tmp_db):
        listing = make_listing()
        tmp_db.upsert_listing(listing)
        tmp_db.upsert_listing(listing)
        tmp_db.upsert_listing(listing)
        with tmp_db.get_conn() as conn:
            row = conn.execute(
                "SELECT times_seen FROM listings WHERE url=?", (listing["url"],)
            ).fetchone()
            assert row["times_seen"] == 3


class TestStatusAndNotes:
    def test_update_status(self, tmp_db):
        tmp_db.upsert_listing(make_listing())
        rid = tmp_db.get_listings()[0]["id"]
        tmp_db.update_status(rid, "interested")
        assert tmp_db.get_listing(rid)["status"] == "interested"

    def test_update_notes_preserves_status(self, tmp_db):
        """Regression test: the original notes endpoint clobbered status."""
        tmp_db.upsert_listing(make_listing())
        rid = tmp_db.get_listings()[0]["id"]
        tmp_db.update_status(rid, "touring_applying")
        tmp_db.update_notes(rid, "called Tuesday")
        row = tmp_db.get_listing(rid)
        assert row["status"] == "touring_applying"
        assert row["notes"] == "called Tuesday"

    def test_get_listing_missing_raises(self, tmp_db):
        with pytest.raises(KeyError):
            tmp_db.get_listing(999_999)


class TestEnrichment:
    def test_needs_enrichment_excludes_scored(self, tmp_db):
        tmp_db.upsert_listing(make_listing(url="https://a/1"))
        tmp_db.upsert_listing(make_listing(url="https://a/2"))
        assert len(tmp_db.get_listings_needing_enrichment()) == 2
        rid = tmp_db.get_listings()[0]["id"]
        tmp_db.update_enrichment(rid, {"fit_score": 8, "ai_summary": "nice"})
        # One now has enriched_at set, so only one remains pending
        assert len(tmp_db.get_listings_needing_enrichment()) == 1

    def test_update_enrichment_stores_fields(self, tmp_db):
        tmp_db.upsert_listing(make_listing())
        rid = tmp_db.get_listings()[0]["id"]
        tmp_db.update_enrichment(rid, {
            "fit_score": 9,
            "ai_summary": "2BR duplex",
            "pet_policy": "cats_only",
            "concerns": '["over budget"]',
            "highlights": '["garage"]',
        })
        row = tmp_db.get_listing(rid)
        assert row["fit_score"] == 9
        assert row["ai_summary"] == "2BR duplex"
        assert row["pet_policy"] == "cats_only"
        assert row["enriched_at"] is not None

    def test_update_enrichment_ignores_unknown_fields(self, tmp_db):
        tmp_db.upsert_listing(make_listing())
        rid = tmp_db.get_listings()[0]["id"]
        # Should not raise on a stray key, just ignore it
        tmp_db.update_enrichment(rid, {"fit_score": 5, "bogus_column": "x"})
        assert tmp_db.get_listing(rid)["fit_score"] == 5


class TestAggregates:
    def test_get_sources_dedups(self, tmp_db):
        tmp_db.upsert_listing(make_listing(url="https://a/1", source="foo"))
        tmp_db.upsert_listing(make_listing(url="https://a/2", source="foo"))
        tmp_db.upsert_listing(make_listing(url="https://b/1", source="bar"))
        assert tmp_db.get_sources() == ["bar", "foo"]

    def test_get_status_counts(self, tmp_db):
        for i, status in enumerate(["new", "new", "interested", "passed"]):
            tmp_db.upsert_listing(make_listing(url=f"https://x/{i}"))
            rid = tmp_db.get_listings()[0]["id"]
            tmp_db.update_status(rid, status)
        counts = tmp_db.get_status_counts()
        assert counts["total"] == 4
        # Last update wins on the most recent listing; check sum is right
        assert counts["new"] + counts["interested"] + counts["passed"] == 4
