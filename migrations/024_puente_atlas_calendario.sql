-- =============================================================================
-- 024_puente_atlas_calendario.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Enlaza la agenda de este sistema con las reuniones de ATLAS, en los dos
-- sentidos: lo que se agenda allá aparece aquí y lo que se agenda aquí aparece
-- allá.
--
-- EL DISPARADOR ES EL CALENDARIO «ATLAS», el que tiene atlas.cenest@gmail.com.
-- Sólo se sincronizan las citas de ESE calendario. Una cita de JOMAP o personal
-- no tiene por qué acabar en la agenda de ATLAS, y al revés tampoco.
--
-- Hacen falta dos columnas:
--
--   * atlas_reunion_id — el id de la reunión en la base de ATLAS. Es lo que
--     permite saber que esta cita y aquella reunión son la MISMA cosa, y no
--     duplicarla en cada pasada.
--
--   * atlas_hash — huella del contenido en la última sincronización. ATLAS no
--     guarda fecha de modificación en `reuniones`, así que no hay forma de
--     preguntar «¿cambió allá?». Comparando contra esta huella sí: si lo de
--     ATLAS ya no coincide, cambió allá; si lo nuestro ya no coincide, cambió
--     aquí; si no coincide ninguno, cambiaron los dos y manda ATLAS, que es el
--     disparador.
--
-- Idempotente. Aplicar después de la 023.
-- =============================================================================

ALTER TABLE appointments ADD COLUMN IF NOT EXISTS atlas_reunion_id integer;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS atlas_hash       text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS atlas_synced_at  timestamptz;

-- Una reunión de ATLAS no puede quedar enlazada a dos citas distintas. El índice
-- parcial deja fuera las citas que no vienen de ATLAS, que son la mayoría.
CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_atlas_reunion
  ON appointments (atlas_reunion_id) WHERE atlas_reunion_id IS NOT NULL;
