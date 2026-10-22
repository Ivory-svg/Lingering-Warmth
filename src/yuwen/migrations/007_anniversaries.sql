BEGIN;

CREATE TABLE anniversaries (
    id INTEGER PRIMARY KEY,
    month_day TEXT NOT NULL,
    year INTEGER,
    yearly INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    memory_id INTEGER REFERENCES memories(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

UPDATE schema_meta SET value = '7' WHERE key = 'version';

COMMIT;
