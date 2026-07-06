-- Sistema de roles multiples. Reemplaza el modelo de permisos directos
-- (users.modules / calendar_permissions / ms_account_permissions) como fuente
-- de autorizacion en el codigo de la app. Las tablas viejas NO se borran aqui
-- (ver 015_roles_backfill.sql) -- quedan como red de seguridad para rollback.
--
-- Un usuario puede tener varios roles; cada rol agrupa modulos + calendarios +
-- proyectos + cuentas Microsoft. El admin crea los roles y se los asigna a
-- los usuarios; el usuario elige cual de sus roles tiene activo.
--
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

CREATE TABLE IF NOT EXISTS roles (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  name text NOT NULL,
  description text,
  modules text,                 -- CSV, mismo formato que users.modules (subset de calendar,planning,todo)
  created_by text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS role_calendars (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  role_id uuid NOT NULL,
  calendar_id text NOT NULL,     -- calendar_config.calendar_id (slug de texto, sin FK -- mismo patron que calendar_permissions)
  UNIQUE(role_id, calendar_id)
);
ALTER TABLE role_calendars
  ADD CONSTRAINT role_calendars_role_fkey
  FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;

CREATE TABLE IF NOT EXISTS role_projects (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  role_id uuid NOT NULL,
  project_id uuid NOT NULL,
  UNIQUE(role_id, project_id)
);
ALTER TABLE role_projects
  ADD CONSTRAINT role_projects_role_fkey
  FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;
ALTER TABLE role_projects
  ADD CONSTRAINT role_projects_project_fkey
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

CREATE TABLE IF NOT EXISTS role_ms_accounts (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  role_id uuid NOT NULL,
  ms_email text NOT NULL,        -- sin FK a ms_tokens.email, mismo patron que ms_account_permissions
  UNIQUE(role_id, ms_email)
);
ALTER TABLE role_ms_accounts
  ADD CONSTRAINT role_ms_accounts_role_fkey
  FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;

CREATE TABLE IF NOT EXISTS user_roles (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid NOT NULL,
  role_id uuid NOT NULL,
  assigned_at timestamptz DEFAULT now(),
  UNIQUE(user_id, role_id)
);
ALTER TABLE user_roles
  ADD CONSTRAINT user_roles_user_fkey
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE user_roles
  ADD CONSTRAINT user_roles_role_fkey
  FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;

-- Rol activo persistido entre sesiones/dispositivos (ademas de session['active_role_id'],
-- que es el valor de trabajo rapido dentro de una sesion ya iniciada).
ALTER TABLE users ADD COLUMN IF NOT EXISTS active_role_id uuid;
ALTER TABLE users
  ADD CONSTRAINT users_active_role_fkey
  FOREIGN KEY (active_role_id) REFERENCES roles(id) ON DELETE SET NULL;

ALTER TABLE roles DISABLE ROW LEVEL SECURITY;
ALTER TABLE role_calendars DISABLE ROW LEVEL SECURITY;
ALTER TABLE role_projects DISABLE ROW LEVEL SECURITY;
ALTER TABLE role_ms_accounts DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles DISABLE ROW LEVEL SECURITY;
