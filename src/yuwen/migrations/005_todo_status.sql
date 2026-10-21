BEGIN;

ALTER TABLE memories ADD COLUMN status TEXT;
ALTER TABLE memories ADD COLUMN status_as_of TEXT;
ALTER TABLE memories ADD COLUMN resolved_at TEXT;
ALTER TABLE memories ADD COLUMN resolved_note TEXT;
CREATE INDEX memories_status_idx ON memories(status);

UPDATE schema_meta SET value = '5' WHERE key = 'version';

COMMIT;
