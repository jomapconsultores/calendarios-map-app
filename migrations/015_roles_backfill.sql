-- EJECUTAR UNA SOLA VEZ, despues de 014_roles.sql y ANTES de desplegar el codigo
-- que empieza a leer el sistema de roles (si no, todos veran "sin acceso" hasta
-- que se corra esto).
--
-- Materializa el acceso ACTUAL de cada usuario (users.modules + calendar_permissions
-- aprobados + ms_account_permissions) en un rol individual "Migrado - {nombre}",
-- y se lo asigna, para que nadie pierda ni gane acceso el dia del lanzamiento.
--
-- Proyectos: hoy NO existe control de acceso a proyectos (todo usuario con modulo
-- 'planning' ve TODOS los proyectos existentes). Para preservar ese status quo,
-- el rol migrado otorga TODOS los proyectos que existen AL MOMENTO DE CORRER ESTE
-- SCRIPT. Un proyecto creado DESPUES de este script no sera visible automaticamente
-- para usuarios migrados -- un admin debera agregarlo al rol correspondiente.
--
-- Reejecutable sin duplicar: si un usuario ya tiene un rol "Migrado - {nombre}"
-- asignado, se salta.
--
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

DO $$
DECLARE
  u RECORD;
  new_role_id uuid;
BEGIN
  FOR u IN SELECT id, full_name, modules FROM users LOOP
    IF EXISTS (
      SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
      WHERE ur.user_id = u.id AND r.name = 'Migrado - ' || u.full_name
    ) THEN
      CONTINUE;
    END IF;

    INSERT INTO roles (name, description, modules, created_by)
    VALUES (
      'Migrado - ' || u.full_name,
      'Rol generado automaticamente al migrar al sistema de roles multiples. Preserva el acceso que este usuario tenia antes del cambio.',
      COALESCE(u.modules, 'calendar,planning'),
      'migration_script'
    )
    RETURNING id INTO new_role_id;

    INSERT INTO user_roles (user_id, role_id) VALUES (u.id, new_role_id);

    INSERT INTO role_calendars (role_id, calendar_id)
    SELECT new_role_id, cp.calendar_id
    FROM calendar_permissions cp
    WHERE cp.user_id = u.id AND cp.status = 'approved';

    INSERT INTO role_ms_accounts (role_id, ms_email)
    SELECT new_role_id, mp.ms_email
    FROM ms_account_permissions mp
    WHERE mp.user_id = u.id;

    INSERT INTO role_projects (role_id, project_id)
    SELECT new_role_id, p.id FROM projects p;   -- TODOS los proyectos existentes hoy

    UPDATE users SET active_role_id = new_role_id WHERE id = u.id;
  END LOOP;
END $$;
