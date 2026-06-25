-- Tablas del modulo Planificacion
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

CREATE TABLE IF NOT EXISTS projects (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  name text NOT NULL,
  description text,
  color text,
  status text,
  priority text,
  start_date date,
  due_date date,
  owner text,
  created_by text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  project_id uuid,
  phase text,
  title text NOT NULL,
  description text,
  assigned_to text,
  assigned_email text,
  priority text,
  status text,
  start_date date,
  due_date date,
  completed_date date,
  progress_pct int,
  alert_days int,
  tags text,
  notes text,
  source text,
  source_id text,
  created_by text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_deps (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  task_id uuid NOT NULL,
  depends_on uuid NOT NULL,
  UNIQUE(task_id, depends_on)
);

CREATE TABLE IF NOT EXISTS ms_tokens (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  email text UNIQUE,
  access_token text,
  refresh_token text,
  expires_at timestamptz,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE tasks
  ADD CONSTRAINT tasks_project_fkey
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL;

ALTER TABLE task_deps
  ADD CONSTRAINT task_deps_task_fkey
  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;

ALTER TABLE task_deps
  ADD CONSTRAINT task_deps_depends_fkey
  FOREIGN KEY (depends_on) REFERENCES tasks(id) ON DELETE CASCADE;
