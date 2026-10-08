BEGIN;

ALTER TABLE voice_notes ADD COLUMN overturned_at TEXT;
ALTER TABLE voice_notes ADD COLUMN overturn_note TEXT;

UPDATE schema_meta SET value = '2' WHERE key = 'version';

COMMIT;
