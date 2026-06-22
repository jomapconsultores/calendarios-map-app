-- Soporte para sincronización bidireccional con Microsoft To-Do
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS ms_list_id text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;
