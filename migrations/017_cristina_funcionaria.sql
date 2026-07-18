-- Incorpora a Cristina Chicaiza como FUNCIONARIA con acceso al calendario Tributos.
-- Script de datos (una sola vez), idempotente y re-ejecutable sin duplicar.
--
-- REQUISITO: correr ANTES 016_role_levels_and_activities.sql (usa roles.level).
--
-- Que hace:
--   1) Crea (o actualiza) el usuario de Cristina con sus credenciales.
--      - email: jhoa-5@hotmail.com
--      - clave: Cristina2026@  (guardada como hash scrypt de werkzeug, igual que
--        /register y /login; NUNCA en texto plano)
--   2) Crea el rol "Funcionaria - Tributos" (nivel funcionario, modulo calendario)
--      si no existe.
--   3) Enlaza el calendario cuyo nombre contiene "Tributos" a ese rol.
--   4) Le asigna el rol a Cristina y lo deja como su rol activo.
--
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

DO $$
DECLARE
  v_user_id uuid;
  v_role_id uuid;
  v_cal_id  text;
BEGIN
  -- 1) Usuario (upsert por email) -------------------------------------------
  INSERT INTO users (email, full_name, role, password_hash, modules)
  VALUES (
    'jhoa-5@hotmail.com',
    'Cristina Chicaiza',
    'staff',
    'scrypt:32768:8:1$yuuonn3HgY3w76Lv$0ec9ccd1ca4fa0d54c4fb8a9efa1dbe34cf333dbe890cea69dd912a1c94d5d4e8589a52448e2261ec09a5871422063b31ba4821b63e3ce44d91c71072d6680dd',
    'calendar'
  )
  ON CONFLICT (email) DO UPDATE
    SET full_name     = EXCLUDED.full_name,
        password_hash = EXCLUDED.password_hash
  RETURNING id INTO v_user_id;

  -- 2) Rol "Funcionaria - Tributos" (reutiliza si ya existe por nombre) ------
  SELECT id INTO v_role_id FROM roles WHERE name = 'Funcionaria - Tributos' LIMIT 1;
  IF v_role_id IS NULL THEN
    INSERT INTO roles (name, description, level, modules, created_by)
    VALUES (
      'Funcionaria - Tributos',
      'Funcionaria del area de Tributos. Acceso al calendario de Tributos.',
      'funcionario',
      'calendar',
      'seed_cristina'
    )
    RETURNING id INTO v_role_id;
  END IF;

  -- 3) Calendario Tributos -> rol ------------------------------------------
  SELECT calendar_id INTO v_cal_id
    FROM calendar_config
   WHERE name ILIKE '%tributo%'
   ORDER BY name
   LIMIT 1;

  IF v_cal_id IS NOT NULL THEN
    INSERT INTO role_calendars (role_id, calendar_id)
    VALUES (v_role_id, v_cal_id)
    ON CONFLICT (role_id, calendar_id) DO NOTHING;
  ELSE
    RAISE NOTICE 'No se encontro un calendario cuyo nombre contenga "tributo". El rol quedo sin calendario; agregalo a mano en Admin -> Roles.';
  END IF;

  -- 4) Asignar rol al usuario + dejarlo activo ------------------------------
  INSERT INTO user_roles (user_id, role_id)
  VALUES (v_user_id, v_role_id)
  ON CONFLICT (user_id, role_id) DO NOTHING;

  UPDATE users SET active_role_id = v_role_id WHERE id = v_user_id;

  RAISE NOTICE 'Cristina lista: user_id=%, role_id=%, calendar_id=%', v_user_id, v_role_id, v_cal_id;
END $$;
