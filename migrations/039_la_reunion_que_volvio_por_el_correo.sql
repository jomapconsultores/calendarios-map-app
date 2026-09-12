-- ============================================================
--  La reunión que convocamos nosotros, de vuelta por el correo
--
--  Aplicar con:  python tools/aplicar_migracion.py migrations/039_*.sql
--  (necesita DATABASE_URL o PG_ADMIN_URL. Con --ver se lee sin aplicar nada.)
-- ============================================================
--
-- La 038 recogió los duplicados que se reconocían por el evento de Google o por
-- el UID de calendario. Quedó vivo un quinto patrón que no se ve desde ninguno
-- de los dos, porque cada fila lleva justo el dato que a la otra le falta:
--
--   * la fila que nació aquí tiene `google_event_id` y NINGÚN `external_uid`;
--   * la copia que entró por correo tiene `external_uid` y NINGÚN
--     `google_event_id`.
--
-- Son la misma reunión. La plataforma la crea en la agenda de una cuenta, Google
-- manda la invitación a los asistentes, y uno de ellos es la cuenta de Microsoft
-- de la casa: el .ics entra por su bandeja y se apunta como cita ajena. Agrupar
-- por `google_event_id` no las junta (una lo tiene en NULL) y agrupar por
-- `external_uid` tampoco (la otra lo tiene en NULL).
--
-- Lo que sí las junta es que el UID de Google LLEVA DENTRO el identificador del
-- evento: a lo que nace en Google, Google le pone de UID `<id del evento>@google.com`.
--
-- Esto se veía en el registro del servidor cada quince minutos, sin que nadie lo
-- mirara: la pasada encontraba el evento, quería guardarle su UID a la fila que
-- nació aquí, y el índice contestaba que ese UID ya era de otra fila. Como el
-- UPDATE fallaba entero, tampoco se guardaba la marca de versión, así que a la
-- vuelta siguiente volvía a intentarlo. Para siempre.
--
-- El código ya lo reconoce (`_evento_de_google`), así que no van a aparecer
-- más. Esto recoge los que quedaron.
--
-- Como en la 038: no se toca ningún calendario de nadie, y lo que se retira se
-- guarda entero en `appointments_duplicadas`.


-- ------------------------------------------------------------
--  1. ANTES DE BORRAR NADA: mirar qué hay
-- ------------------------------------------------------------
-- No cambia nada. Es exactamente lo que el paso 2 va a retirar.
--
--   SELECT e.id AS se_retira, p.id AS se_queda, e.title, e.start_time,
--          e.google_account AS cuenta_del_correo,
--          p.google_account AS cuenta_que_convoco,
--          (e.start_time = p.start_time) AS misma_hora
--     FROM appointments e
--     JOIN appointments p
--       ON p.google_event_id = split_part(lower(btrim(e.external_uid)), '@', 1)
--    WHERE e.external_uid IS NOT NULL AND e.id <> p.id
--    ORDER BY e.start_time;


-- ------------------------------------------------------------
--  2. Recoger la copia que entró por correo
-- ------------------------------------------------------------
-- Se queda la que nació aquí, por lo mismo que en la 038: lleva el tema, el
-- cliente, su aprobación y lo que se le haya editado. La copia sólo trae lo que
-- cabe en un .ics.
WITH pares AS (
  SELECT e.id AS copia, p.id AS original
    FROM appointments e
    JOIN appointments p
      ON p.google_event_id = split_part(lower(btrim(e.external_uid)), '@', 1)
   WHERE e.external_uid IS NOT NULL
     AND btrim(e.external_uid) <> ''
     AND e.id <> p.id
),
guardadas AS (
  INSERT INTO appointments_duplicadas (cita_id, se_quedo_id, motivo, fila)
  SELECT p.copia, p.original, 'evento_en_uid', to_jsonb(a)
    FROM pares p JOIN appointments a ON a.id = p.copia
  RETURNING cita_id
)
DELETE FROM appointments a
 USING guardadas g
 WHERE a.id = g.cita_id;


-- ------------------------------------------------------------
--  3. Y pasarle el UID a la que se queda
-- ------------------------------------------------------------
-- Va DESPUÉS del borrado a propósito: mientras la copia existía, ese UID era
-- suyo y el índice único no dejaba ponerlo en dos sitios.
--
-- Sin esto, la fila que se queda vuelve a tener el evento y ningún UID, que es
-- el estado desde el que empezó todo: la próxima invitación por correo volvería
-- a no reconocerla. Con el UID puesto, se reconoce a la primera.
UPDATE appointments a
   SET external_uid = d.fila->>'external_uid'
  FROM appointments_duplicadas d
 WHERE d.motivo = 'evento_en_uid'
   AND a.id = d.se_quedo_id
   AND a.external_uid IS NULL
   AND d.fila->>'external_uid' IS NOT NULL;


-- ------------------------------------------------------------
--  4. Cómo saber que quedó bien
-- ------------------------------------------------------------
--   -- Cuántas se recogieron:
--   SELECT motivo, count(*) FROM appointments_duplicadas GROUP BY motivo;
--
--   -- Y que no queda ninguna (tiene que dar 0 filas):
--   SELECT e.id FROM appointments e
--     JOIN appointments p
--       ON p.google_event_id = split_part(lower(btrim(e.external_uid)), '@', 1)
--    WHERE e.external_uid IS NOT NULL AND e.id <> p.id;
--
--   -- En el registro del servidor, que dejen de salir los 409:
--   --   docker logs --since 30m <contenedor> | grep '409'
