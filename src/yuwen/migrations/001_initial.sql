PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    continues_handoff_id INTEGER REFERENCES handoffs(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'event')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    meta TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    happened_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    quote TEXT,
    emotion_text TEXT NOT NULL,
    valence REAL,
    arousal REAL,
    certainty REAL NOT NULL,
    source_message_ids TEXT NOT NULL,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    sealed INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 1.0,
    last_recalled_at TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS memory_history (
    id INTEGER PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    snapshot TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    created_at TEXT NOT NULL,
    doing TEXT NOT NULL,
    where_we_are TEXT NOT NULL,
    emotion TEXT NOT NULL,
    raw_tail_message_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_notes (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('gatekeeper', 'player')),
    message_id INTEGER REFERENCES messages(id),
    original TEXT NOT NULL,
    rewritten TEXT NOT NULL,
    reason TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

INSERT INTO schema_meta (key, value)
VALUES ('version', '1')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
