-- Subtareas (checklist items de Microsoft To-Do) guardadas como JSON
-- Formato: [{id, name, done, checked_at}]
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS subtasks jsonb DEFAULT '[]'::jsonb;
