-- Autorización de cuentas Microsoft por usuario
-- Cada fila significa: el usuario X puede ver tareas que provienen de la cuenta MS Y

CREATE TABLE IF NOT EXISTS ms_account_permissions (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  ms_email text NOT NULL,
  created_at timestamptz DEFAULT now(),
  UNIQUE(user_id, ms_email)
);
ALTER TABLE ms_account_permissions DISABLE ROW LEVEL SECURITY;

-- Columna ms_email en tasks: cuenta MS de origen (para tareas importadas)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS ms_email text;

-- Backfill: extraer ms_email del campo tags que ya guardamos como "lista · email"
UPDATE tasks
SET ms_email = trim(split_part(tags, '·', 2))
WHERE source = 'ms_todo' AND tags LIKE '%·%' AND ms_email IS NULL;
