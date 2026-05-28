"""
SQLite layer for the Wauwatosa rental search dashboard.

Schema migrations: idempotent column-add logic at the bottom of init_db.
Designed so that existing rows (with statuses + notes) survive schema bumps.
"""
from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "rentals.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT UNIQUE NOT NULL,
    source              TEXT NOT NULL,
    title               TEXT,
    address             TEXT,
    neighborhood        TEXT,
    beds                TEXT,
    baths               TEXT,
    rent                INTEGER,
    available_date      TEXT,
    contact             TEXT,
    description         TEXT,
    duplex_flag         INTEGER DEFAULT 0,
    fit_score           INTEGER,
    status              TEXT DEFAULT 'new',
    notes               TEXT,
    first_seen          DATETIME DEFAULT CURRENT_TIMESTAMP,
    raw_data            TEXT
);

CREATE INDEX IF NOT EXISTS idx_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_source ON listings(source);
CREATE INDEX IF NOT EXISTS idx_rent   ON listings(rent);
"""

# Columns + indexes added after the initial schema shipped.
# Each tuple is (column_name, column_definition, optional_post_add_sql).
# Note: SQLite ALTER TABLE ADD COLUMN cannot use non-constant defaults like
# CURRENT_TIMESTAMP, so we add the column nullable and backfill in post_add_sql.
MIGRATIONS = [
    ("last_seen", "DATETIME",
     "UPDATE listings SET last_seen = first_seen WHERE last_seen IS NULL"),
    ("times_seen", "INTEGER DEFAULT 1", None),
    ("description_fetched", "INTEGER DEFAULT 0",
     "CREATE INDEX IF NOT EXISTS idx_desc_fetched ON listings(description_fetched)"),
    ("listing_type", "TEXT DEFAULT 'rental'",
     "CREATE INDEX IF NOT EXISTS idx_listing_type ON listings(listing_type)"),
    # Chunk 2: Claude (Haiku) enrichment + fit scoring
    ("ai_summary", "TEXT", None),
    ("pet_policy", "TEXT", None),
    ("parking", "TEXT", None),
    ("laundry", "TEXT", None),
    ("fit_reason", "TEXT", None),
    ("concerns", "TEXT", None),     # JSON array
    ("highlights", "TEXT", None),   # JSON array
    ("enriched_at", "DATETIME",
     "CREATE INDEX IF NOT EXISTS idx_enriched_at ON listings(enriched_at)"),
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create the schema if absent, then apply any missing column migrations."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
        for col_name, col_def, post_sql in MIGRATIONS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {col_name} {col_def}")
                if post_sql:
                    conn.execute(post_sql)
            elif post_sql and post_sql.lstrip().upper().startswith("CREATE INDEX"):
                # Re-run idempotent index creation even if column already exists
                conn.execute(post_sql)


# ---------------------------------------------------------------------------
# Inserts / updates
# ---------------------------------------------------------------------------

def upsert_listing(listing: dict) -> bool:
    """
    Insert a new listing, or if the URL is already present, bump last_seen
    and times_seen. Returns True only when a brand-new row was inserted.
    """
    sql = """
        INSERT INTO listings
            (url, source, title, address, neighborhood, beds, baths,
             rent, available_date, contact, description, duplex_flag,
             raw_data, listing_type, last_seen, times_seen)
        VALUES
            (:url, :source, :title, :address, :neighborhood, :beds, :baths,
             :rent, :available_date, :contact, :description, :duplex_flag,
             :raw_data, :listing_type, CURRENT_TIMESTAMP, 1)
        ON CONFLICT(url) DO UPDATE SET
            last_seen  = CURRENT_TIMESTAMP,
            times_seen = times_seen + 1
        RETURNING (times_seen = 1) AS is_new
    """
    with get_conn() as conn:
        row = conn.execute(sql, listing).fetchone()
        return bool(row["is_new"])


def update_status(listing_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE listings SET status=? WHERE id=?", (status, listing_id))


def update_notes(listing_id: int, notes: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE listings SET notes=? WHERE id=?", (notes, listing_id))


def update_fit_score(listing_id: int, fit_score: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE listings SET fit_score=? WHERE id=?", (fit_score, listing_id))


def update_description(
    listing_id: int,
    description: str,
    address: str | None = None,
    available_date: str | None = None,
) -> None:
    """Used by Craigslist detail-page enrichment."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE listings
               SET description = ?,
                   address = COALESCE(?, address),
                   available_date = COALESCE(?, available_date),
                   description_fetched = 1
             WHERE id = ?
            """,
            (description, address, available_date, listing_id),
        )


def mark_description_failed(listing_id: int) -> None:
    """Mark as fetched=2 to avoid retrying a permanently broken URL."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE listings SET description_fetched=2 WHERE id=?",
            (listing_id,),
        )


def delete_listing(listing_id: int) -> None:
    """Permanently remove a listing (used to clean out non-rental noise)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM listings WHERE id=?", (listing_id,))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_listings(status: str | None = None, min_score: int | None = None) -> list[dict]:
    sql = "SELECT * FROM listings WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if min_score is not None:
        sql += " AND (fit_score IS NULL OR fit_score>=?)"
        params.append(min_score)
    sql += " ORDER BY first_seen DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_listing(listing_id: int) -> dict:
    """Fetch one listing by id. Raises KeyError if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE id=?", (listing_id,)
        ).fetchone()
    if row is None:
        raise KeyError(f"listing {listing_id} not found")
    return dict(row)


def get_sources() -> list[str]:
    with get_conn() as conn:
        return [
            r["source"] for r in conn.execute(
                "SELECT DISTINCT source FROM listings ORDER BY source"
            )
        ]


def get_status_counts() -> dict:
    counts = {"new": 0, "interested": 0, "touring_applying": 0, "passed": 0, "total": 0}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM listings GROUP BY status"
        ).fetchall()
        for r in rows:
            if r["status"] in counts:
                counts[r["status"]] = r["n"]
            counts["total"] += r["n"]
    return counts


def get_type_counts() -> dict:
    """Count listings by listing_type ('rental' / 'roommate')."""
    counts = {"rental": 0, "roommate": 0}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT listing_type, COUNT(*) AS n FROM listings GROUP BY listing_type"
        ).fetchall()
        for r in rows:
            if r["listing_type"] in counts:
                counts[r["listing_type"]] = r["n"]
    return counts


def update_listing_type(listing_id: int, listing_type: str) -> None:
    """Used by the backfill helper and the post-enrichment re-classifier."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE listings SET listing_type=? WHERE id=?",
            (listing_type, listing_id),
        )


def get_listings_needing_enrichment(limit: int | None = None) -> list[dict]:
    """Listings that haven't been scored by Claude yet (enriched_at IS NULL)."""
    sql = "SELECT * FROM listings WHERE enriched_at IS NULL ORDER BY first_seen DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def update_enrichment(listing_id: int, fields: dict) -> None:
    """
    Store Claude enrichment results. `fields` may contain any of:
    ai_summary, neighborhood, beds, baths, rent, duplex_flag, pet_policy,
    parking, laundry, available_date, fit_score, fit_reason, concerns,
    highlights. Sets enriched_at to now.
    """
    allowed = {
        "ai_summary", "neighborhood", "beds", "baths", "rent", "duplex_flag",
        "pet_policy", "parking", "laundry", "available_date", "fit_score",
        "fit_reason", "concerns", "highlights",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    set_clause = ", ".join(f"{k}=:{k}" for k in updates)
    set_clause += ", enriched_at=CURRENT_TIMESTAMP" if set_clause else "enriched_at=CURRENT_TIMESTAMP"
    params = {**updates, "lid": listing_id}
    with get_conn() as conn:
        conn.execute(f"UPDATE listings SET {set_clause} WHERE id=:lid", params)


def get_listings_needing_description(source_prefix: str = "craigslist_") -> list[dict]:
    """Find listings whose detail pages haven't been fetched yet."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, url, source
              FROM listings
             WHERE source LIKE ? AND description_fetched = 0
            """,
            (source_prefix + "%",),
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
