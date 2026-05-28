"""Tests for the FB/Nextdoor ingestion path (import_listings.py)."""
import importlib
import json
import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    # import_listings imports db symbols at module load, so reload it too
    import import_listings as il
    importlib.reload(il)
    return db_module, il


class TestNormalize:
    def test_skips_missing_url(self, tmp_db):
        _, il = tmp_db
        assert il.normalize({"title": "2BR duplex"}, "facebook_marketplace") is None

    def test_skips_non_rental(self, tmp_db):
        _, il = tmp_db
        result = il.normalize(
            {"url": "https://fb.com/1", "title": "Looking for a roommate",
             "description": "Seeking a companion to hang out with"},
            "facebook_marketplace",
        )
        assert result[0] == "skip_nonrental"

    def test_skips_out_of_scope(self, tmp_db):
        _, il = tmp_db
        result = il.normalize(
            {"url": "https://fb.com/2", "title": "2BR apartment",
             "location": "Waukesha", "description": "nice place in Waukesha"},
            "facebook_marketplace",
        )
        assert result[0] == "skip_geo"

    def test_accepts_in_scope_rental(self, tmp_db):
        _, il = tmp_db
        status, listing = il.normalize(
            {"url": "https://fb.com/3", "title": "2BR upper duplex",
             "location": "Wauwatosa", "rent": "$1,100",
             "description": "Charming upper duplex in Wauwatosa with garage"},
            "facebook_marketplace",
        )
        assert status == "ok"
        assert listing["rent"] == 1100
        assert listing["duplex_flag"] == 1
        assert listing["source"] == "facebook_marketplace"
        assert listing["neighborhood"] == "Wauwatosa"

    def test_rent_coercion(self, tmp_db):
        _, il = tmp_db
        assert il._coerce_rent(1200) == 1200
        assert il._coerce_rent("$1,250/mo") == 1250
        assert il._coerce_rent(None) is None
        assert il._coerce_rent(50) is None  # too low to be rent


class TestIngest:
    def test_ingest_adds_and_dedups(self, tmp_db):
        db_module, il = tmp_db
        rows = [
            {"url": "https://fb.com/a", "title": "2BR duplex Wauwatosa",
             "location": "Wauwatosa", "rent": 1000},
            {"url": "https://fb.com/b", "title": "1BR Story Hill",
             "location": "Story Hill", "rent": 900},
        ]
        counts = il.ingest_listings(rows, "facebook_marketplace")
        assert counts["added"] == 2
        # Re-ingest → both re-seen, none added
        counts2 = il.ingest_listings(rows, "facebook_marketplace")
        assert counts2["added"] == 0
        assert counts2["reseen"] == 2

    def test_ingested_listings_need_enrichment(self, tmp_db):
        db_module, il = tmp_db
        il.ingest_listings(
            [{"url": "https://fb.com/c", "title": "2BR Tosa duplex",
              "location": "Wauwatosa", "rent": 1050}],
            "facebook_marketplace",
        )
        # New FB listing should be queued for scoring (enriched_at IS NULL)
        pending = db_module.get_listings_needing_enrichment()
        assert any(l["url"] == "https://fb.com/c" for l in pending)

    def test_quick_add_in_scope(self, tmp_db):
        db_module, il = tmp_db
        r = il.quick_add(
            "2BR/1BA lower duplex in Wauwatosa near 68th & Wells. $1,150/mo, "
            "off-street parking, cats OK. Available July 1.",
            url="", source="facebook_group",
        )
        assert r["status"] == "ok"
        assert r["listing_id"] is not None
        # Synthetic URL generated, listing queued for scoring
        row = db_module.get_listing(r["listing_id"])
        assert row["source"] == "facebook_group"
        assert row["enriched_at"] is None

    def test_quick_add_dedups_same_text(self, tmp_db):
        db_module, il = tmp_db
        post = "2BR upper duplex in Story Hill, $1000/mo, hardwood floors"
        r1 = il.quick_add(post, source="facebook_group")
        r2 = il.quick_add(post, source="facebook_group")
        assert r1["status"] == "ok"
        assert r2["status"] == "duplicate"
        assert r2["listing_id"] == r1["listing_id"]

    def test_quick_add_skips_iso(self, tmp_db):
        _, il = tmp_db
        r = il.quick_add("Looking for a 2 bedroom for my family, budget $1000",
                         source="facebook_group")
        assert r["status"] == "skip_nonrental"

    def test_quick_add_skips_out_of_scope(self, tmp_db):
        _, il = tmp_db
        r = il.quick_add("3BR house for rent in Waukesha, $1400/mo big yard",
                         source="facebook_group")
        assert r["status"] == "skip_geo"

    def test_quick_add_empty(self, tmp_db):
        _, il = tmp_db
        assert il.quick_add("   ", source="facebook_group")["status"] == "empty"

    def test_mixed_batch_counts(self, tmp_db):
        _, il = tmp_db
        rows = [
            {"url": "https://fb.com/ok", "title": "2BR Wauwatosa duplex",
             "location": "Wauwatosa", "rent": 1000},        # ok
            {"url": "https://fb.com/far", "title": "apt",
             "location": "Waukesha"},                        # geo skip
            {"url": "https://fb.com/job", "title": "Live in caregiver wanted"},  # non-rental
            {"title": "no url here"},                         # no_url
        ]
        counts = il.ingest_listings(rows, "facebook_marketplace")
        assert counts["added"] == 1
        assert counts["skip_geo"] == 1
        assert counts["skip_nonrental"] == 1
        assert counts["no_url"] == 1
