-- =============================================================================
-- 028_recursos_por_persona.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Tercera y última capa del gobierno de accesos: los RECURSOS CONCRETOS.
--
-- Hasta aquí el sistema sabía decir «esta persona entra al Calendario» (módulo)
-- y «puede eliminar citas» (submódulo), pero no «puede ver el calendario de
-- JOMAP, y sólo ése». Los calendarios, los proyectos y las cuentas de Microsoft
-- se concedían ÚNICAMENTE por rol, así que para dar a una persona un calendario
-- más que a sus compañeros de rol había que inventarle un rol entero. Ese fue
-- el origen del rol «Acceso - {nombre}» que ya genera el sistema solo: un
-- parche que confiesa que faltaba esta tabla.
--
-- Las tres tablas siguen el mismo patrón que user_modules (027): lo concedido a
-- la persona se SUMA a lo que le da su rol, nunca lo resta. Quitar un
-- calendario de un rol no puede dejar sin él a quien lo tenía a título propio,
-- y al revés tampoco.
--
-- Una consecuencia buscada, en actividades: si a una persona se le concede un
-- proyecto aquí, lo ve ENTERO. El recorte por actividades (role_tasks) es del
-- rol; lo que el administrador da aparte, a mano y a una persona, no tiene por
-- qué llegar recortado por una regla que se escribió pensando en otros.
--
-- Sin FK a calendar_config ni a ms_tokens: son claves de texto y el resto del
-- sistema (calendar_permissions, role_calendars, role_ms_accounts) tampoco las
-- tiene. Se mantiene el mismo criterio en vez de estrenar uno distinto aquí.
--
-- RLS desactivado, igual que en las otras tablas del proyecto (ver la 022).
--
-- Idempotente. Aplicar después de la 027.
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
--  Calendarios concedidos a una persona
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_calendars (
  id          uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     uuid NOT NULL,
  calendar_id text NOT NULL,        -- calendar_config.calendar_id
  granted_by  uuid,
  created_at  timestamptz DEFAULT now(),
  UNIQUE (user_id, calendar_id)
);

ALTER TABLE user_calendars DROP CONSTRAINT IF EXISTS user_calendars_user_fkey;
ALTER TABLE user_calendars
  ADD CONSTRAINT user_calendars_user_fkey
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_user_calendars_user ON user_calendars (user_id);

-- ─────────────────────────────────────────────────────────────────────────────
--  Proyectos concedidos a una persona (gobiernan Proyectos y las tareas de To-Do
--  que cuelgan de un proyecto)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_projects (
  id         uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id    uuid NOT NULL,
  project_id uuid NOT NULL,
  granted_by uuid,
  created_at timestamptz DEFAULT now(),
  UNIQUE (user_id, project_id)
);

ALTER TABLE user_projects DROP CONSTRAINT IF EXISTS user_projects_user_fkey;
ALTER TABLE user_projects
  ADD CONSTRAINT user_projects_user_fkey
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE user_projects DROP CONSTRAINT IF EXISTS user_projects_project_fkey;
ALTER TABLE user_projects
  ADD CONSTRAINT user_projects_project_fkey
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_user_projects_user ON user_projects (user_id);

-- ─────────────────────────────────────────────────────────────────────────────
--  Cuentas de Microsoft concedidas a una persona (gobiernan qué listas de To-Do
--  ve y sincroniza)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_ms_accounts (
  id         uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id    uuid NOT NULL,
  ms_email   text NOT NULL,         -- ms_tokens.email
  granted_by uuid,
  created_at timestamptz DEFAULT now(),
  UNIQUE (user_id, ms_email)
);

ALTER TABLE user_ms_accounts DROP CONSTRAINT IF EXISTS user_ms_accounts_user_fkey;
ALTER TABLE user_ms_accounts
  ADD CONSTRAINT user_ms_accounts_user_fkey
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_user_ms_accounts_user ON user_ms_accounts (user_id);

-- ─────────────────────────────────────────────────────────────────────────────
--  Mismo modelo que el resto del sistema (ver la 022)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE user_calendars   DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_projects    DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_ms_accounts DISABLE ROW LEVEL SECURITY;
