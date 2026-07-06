-- Indice en tasks.project_id: la columna tiene FK a projects(id) pero no
-- indice, y se usa como filtro de consulta en rutas calientes (listado de
-- tareas por proyecto). Sin indice, esas consultas y los DELETE/UPDATE en
-- cascada de projects hacen full-scan de tasks.
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks (project_id);
