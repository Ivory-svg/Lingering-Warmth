BEGIN;

CREATE TABLE wake_log (
    id INTEGER PRIMARY KEY,
    checked_at TEXT NOT NULL,
    decided TEXT NOT NULL CHECK (decided IN ('send', 'skip', 'blocked')),
    reason TEXT NOT NULL,
    message_id INTEGER REFERENCES messages(id),
    notified INTEGER NOT NULL DEFAULT 0
);

UPDATE schema_meta SET value = '6' WHERE key = 'version';

COMMIT;
