-- ============================================================
--  El recordatorio de incumplimiento va a QUIEN NO LO HIZO
-- ============================================================
--
-- Hasta ahora el aviso diario salía entero a una sola dirección: la del
-- despacho. Quien tenía el trabajo sin hacer no se enteraba salvo que alguien
-- le reenviara el correo, y así el reclamo dependía de que un tercero se
-- acordara. Desde ahora cada responsable recibe UN correo al día con lo suyo, y
-- a la dirección sube sólo lo que hay que decidir: lo que pasa de
-- AVISO_ESCALADO_DIAS días de retraso y lo que no se le pudo reclamar a nadie.
--
-- Eso significa que UN MISMO incumplimiento puede generar DOS avisos el mismo
-- día, a dos direcciones distintas: el recordatorio a su responsable y el
-- escalado a la dirección. El índice único de la migración 030 era
-- (tipo, ref_id, fecha), así que el segundo se descartaba en silencio y la
-- constancia quedaba incompleta: decía que se avisó, pero no a todos los que
-- se avisó. La constancia de un reclamo sin el destinatario no sirve para
-- reclamar.
--
-- Además, `_ya_avisado` deduplica ahora por (tipo, ref_id, destinatario). Con
-- el índice viejo, el escalado a la dirección se veía como "no avisado" en cada
-- pasada y se reenviaba si la revisión se disparaba dos veces el mismo día.
--
-- DÓNDE SE EJECUTA: en el servidor, no en ningún panel de la nube. La base del
-- calendario vive en el contenedor `contable-supabase-db-1`, base `calendario`,
-- y se llega por Tailscale:
--
--   ssh atlas
--   docker exec -i contable-supabase-db-1 psql -U postgres -d calendario \
--     < 035_el_aviso_va_a_quien_no_lo_hizo.sql
--
-- Es reversible y no borra ninguna fila.

BEGIN;

-- El destinatario entra en la clave: la misma cosa, al mismo destinatario, no
-- se avisa dos veces el mismo día. A destinatarios distintos, sí.
DROP INDEX IF EXISTS vencimiento_avisos_unico;

CREATE UNIQUE INDEX IF NOT EXISTS vencimiento_avisos_unico
  ON vencimiento_avisos (tipo, ref_id, fecha, coalesce(destinatario, ''));

-- La consulta de cada revisión ("¿de qué avisé hoy y a quién?") ya lee también
-- el destinatario.
CREATE INDEX IF NOT EXISTS vencimiento_avisos_fecha_destinatario_idx
  ON vencimiento_avisos (fecha DESC, destinatario);

COMMIT;

-- ------------------------------------------------------------
-- Para volver atrás (si se apaga el reparto con AVISO_PERSONAL=0):
--
--   DROP INDEX IF EXISTS vencimiento_avisos_unico;
--   CREATE UNIQUE INDEX vencimiento_avisos_unico
--     ON vencimiento_avisos (tipo, ref_id, fecha);
--   DROP INDEX IF EXISTS vencimiento_avisos_fecha_destinatario_idx;
--
-- Ojo: si ya hay filas del mismo día para dos destinatarios, ese índice viejo
-- no se puede recrear sin borrarlas antes.
-- ------------------------------------------------------------
