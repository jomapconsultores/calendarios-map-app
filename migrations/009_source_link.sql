-- Soporte para correos marcados (flagged emails) que MS To-Do importa como tareas
-- linkedResources de cada tarea: webUrl al email + applicationName ("Outlook", etc.)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_app text;
