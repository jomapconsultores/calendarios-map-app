-- Niveles de rol (administrador / socio / funcionario) + acceso granular a
-- ACTIVIDADES (tareas de un proyecto de Planificacion).
--
-- Contexto: 014_roles.sql ya dio roles con modulos + calendarios + proyectos +
-- cuentas MS. Esto agrega DOS cosas encima, sin romper nada de lo existente:
--
--   1) roles.level  -> clasifica cada rol en un nivel de negocio. Es una
--      etiqueta/agrupacion; el acceso real lo siguen definiendo los grants que
--      el admin marca (modulos/calendarios/proyectos/actividades). El
--      administrador del sistema real sigue siendo users.role = 'admin'.
--
--   2) role_tasks  -> permite al admin "marcar" actividades (tareas) puntuales
--      dentro de un proyecto. Semantica ADITIVA y de AFINACION, no retroactiva:
--        - Un proyecto SIN ninguna tarea marcada para el rol  -> el rol ve
--          TODAS sus tareas (comportamiento actual, no cambia nada).
--        - Un proyecto CON al menos una tarea marcada para el rol -> el rol solo
--          ve dentro de ese proyecto las tareas marcadas.
--      Se guarda project_id denormalizado para saber, sin joins, que proyectos
--      quedan "afinados" (narrowed) para el rol.
--
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

-- 1) Nivel del rol -------------------------------------------------------------
ALTER TABLE roles ADD COLUMN IF NOT EXISTS level text NOT NULL DEFAULT 'funcionario';

-- Solo se aceptan los tres niveles del negocio.
ALTER TABLE roles DROP CONSTRAINT IF EXISTS roles_level_chk;
ALTER TABLE roles
  ADD CONSTRAINT roles_level_chk
  CHECK (level IN ('administrador', 'socio', 'funcionario'));

-- 2) Actividades (tareas) marcadas por rol ------------------------------------
CREATE TABLE IF NOT EXISTS role_tasks (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  role_id uuid NOT NULL,
  task_id uuid NOT NULL,
  project_id uuid,               -- denormalizado desde tasks.project_id al marcar
  UNIQUE(role_id, task_id)
);
ALTER TABLE role_tasks DROP CONSTRAINT IF EXISTS role_tasks_role_fkey;
ALTER TABLE role_tasks
  ADD CONSTRAINT role_tasks_role_fkey
  FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;
ALTER TABLE role_tasks DROP CONSTRAINT IF EXISTS role_tasks_task_fkey;
ALTER TABLE role_tasks
  ADD CONSTRAINT role_tasks_task_fkey
  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS role_tasks_role_idx ON role_tasks(role_id);

ALTER TABLE role_tasks DISABLE ROW LEVEL SECURITY;
