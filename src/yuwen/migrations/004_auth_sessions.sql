BEGIN;

CREATE TABLE auth_sessions (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    password_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

UPDATE schema_meta SET value = '4' WHERE key = 'version';

COMMIT;
