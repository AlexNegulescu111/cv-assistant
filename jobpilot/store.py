"""The workspace database (SQLite): collected jobs, collector runs, Jev scores, pipeline events.

connect() creates every table and migrates older databases in place (columns added, nothing dropped).
"""
import datetime as dt
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE NOT NULL,            -- source:board:job_id
    source TEXT NOT NULL, board TEXT NOT NULL, job_id TEXT NOT NULL,
    company TEXT, title TEXT NOT NULL, location TEXT, remote INTEGER, workplace TEXT,
    department TEXT, employment_type TEXT,
    url TEXT, apply_url TEXT, published_at TEXT, description TEXT,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new', reason TEXT, duplicate_of INTEGER,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_run INTEGER, closed_at TEXT,
    application TEXT,                    -- folder name under applications_dir, once an application is started
    cv TEXT                              -- CV file for this job (workspace-relative), set by `apply set-cv`
);
CREATE INDEX IF NOT EXISTS jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    scored_at TEXT NOT NULL, model TEXT,
    match REAL, verdict TEXT NOT NULL, knockouts TEXT, review_flags TEXT, role_family TEXT,
    details TEXT,                         -- JSON: requirements with hard/met, rubric scores, probabilities
    input_tokens INTEGER, key TEXT NOT NULL,  -- key = hash(profile, config, description): unchanged key → not rescored
    output_tokens INTEGER                     -- when the Jev response reports them
);
CREATE INDEX IF NOT EXISTS scores_job ON scores(job_id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    at TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, note TEXT
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id);
CREATE TABLE IF NOT EXISTS board_suggestions (     -- ATS boards seen through an aggregator but not collected yet
    source TEXT NOT NULL, board TEXT NOT NULL, company TEXT, jobs_seen INTEGER NOT NULL DEFAULT 0,
    example_url TEXT, found_by TEXT, first_seen TEXT, last_seen TEXT,
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | added | ignored
    PRIMARY KEY (source, board)
);
"""
MIGRATIONS = {"jobs": {"last_run": "INTEGER", "application": "TEXT", "cv": "TEXT"},
              "scores": {"output_tokens": "INTEGER"}}


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path):
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # several processes share the file (the dashboard, its child steps): wait for a lock instead of failing at once,
    # and WAL lets readers go on while one of them writes
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    if path != ":memory:":
        db.execute("PRAGMA journal_mode=WAL")
    for table, cols in MIGRATIONS.items():  # before SCHEMA: its indexes may name the new columns
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if have:
            for col, kind in cols.items():
                if col not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    db.executescript(SCHEMA)
    return db
