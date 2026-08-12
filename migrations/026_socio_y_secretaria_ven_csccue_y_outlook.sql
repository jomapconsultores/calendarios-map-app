-- =============================================================================
-- 026_socio_y_secretaria_ven_csccue_y_outlook.sql
-- Desarrollado por Marco Antonio Posligua San Martin
--
-- En "Mis calendarios" no salia el calendario correspondiente: los roles Socio y
-- Secretaria solo tenian concedidos ATLAS y JOMAP, asi que a quien los tiene le
-- faltaban CSCCUE y Outlook, tanto en la barra lateral de /calendar como en el
-- desplegable CALENDARIO de la cita y en las citas que se le muestran.
--
-- No era un fallo de la vista: la lista sale de role_calendars del rol ACTIVO
-- (ver get_user_calendars en app/__init__.py), y ahi faltaban esas dos filas.
--
-- Script de datos, idempotente: UNIQUE(role_id, calendar_id) mas ON CONFLICT,
-- de modo que volver a correrlo no duplica nada. Se limita a los roles que ya
-- existen; si alguno no esta, esa parte simplemente no inserta.
--
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- =============================================================================

INSERT INTO role_calendars (role_id, calendar_id)
SELECT r.id, c.calendar_id
  FROM roles r
  CROSS JOIN (VALUES ('trabajo'), ('hotmail')) AS c(calendar_id)
 WHERE r.name IN ('Socio', 'Secretaria')
   AND EXISTS (SELECT 1 FROM calendar_config cc WHERE cc.calendar_id = c.calendar_id)
ON CONFLICT (role_id, calendar_id) DO NOTHING;
