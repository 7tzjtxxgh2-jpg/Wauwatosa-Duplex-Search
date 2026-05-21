from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "rentals.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT UNIQUE NOT NULL,
    source      TEXT NOT NULL,
    title       TEXT,
    address     TEXT,
    neighborhood TEXT,
    beds        TEXT,
    baths       TEXT,
    rent        INTEGER,
    available_date TEXT,
    contact     TEXT,
    description TEXT,
    duplex_flag INTEGER DEFAULT 0,
    fit_score   INTEGER,
    status      TEXT DEFAULT 'new',
    notes       TEXT,
    first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
    raw_data    TEXT
);

CREATE INDEX IF NOT EXISTS idx_status  ON listings(status);
CREATE INDEX IF NOT EXISTS idx_source  ON listings(source);
CREATE INDEX IF NOT EXISTS idx_rent    ON listings(rent);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_listing(listing: dict) -> bool:
    """Insert listing; skip if URL already exists. Returns True if inserted."""
    sql = """
        INSERT OR IGNORE INTO listings
            (url, source, title, address, neighborhood, beds, baths,
             rent, available_date, contact, description, duplex_flag, raw_data)
        VALUES
            (:url, :source, :title, :address, :neighborhood, :beds, :baths,
             :rent, :available_date, :contact, :description, :duplex_flag, :raw_data)
    """
    with get_conn() as conn:
        cursor = conn.execute(sql, listing)
        return cursor.rowcount > 0


def update_status(listing_id: int, status: str, notes: str = None):
    with get_conn() as conn:
        if notes is not None:
            conn.execute(
                "UPDATE listings SET status=?, notes=? WHERE id=?",
                (status, notes, listing_id),
            )
        else:
            conn.execute(
                "UPDATE listings SET status=? WHERE id=?",
                (status, listing_id),
            )


def update_fit_score(listing_id: int, fit_score: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE listings SET fit_score=? WHERE id=?",
            (fit_score, listing_id),
        )


def get_listings(status: str = None, min_score: int = None) -> list[dict]:
    sql = "SELECT * FROM listings WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if min_score is not None:
        sql += " AND (fit_score IS NULL OR fit_score>=?)"
        params.append(min_score)
    sql += " ORDER BY first_seen DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
