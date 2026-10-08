BEGIN;

ALTER TABLE memories ADD COLUMN embedding_model TEXT;
ALTER TABLE memories ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0;

UPDATE schema_meta SET value = '3' WHERE key = 'version';

COMMIT;
