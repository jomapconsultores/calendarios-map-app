-- ============================================================
--  Una reunión es una fila, la vean una cuenta o seis
--
--  Aplicar con:  python tools/aplicar_migracion.py migrations/038_*.sql
--  (necesita DATABASE_URL o PG_ADMIN_URL: la clave de servicio habla con
--   PostgREST, que no ejecuta DDL. Con --ver se lee sin aplicar nada.)
-- ============================================================
--
-- El calendario se estaba llenando de la misma cita repetida. No era un error de
-- la pantalla: eran filas de verdad, una por cada vez que la misma reunión entró
-- por un camino distinto.
--
-- Aquí se miran nueve agendas que se invitan entre ellas. Una reunión del
-- despacho con Atlas está en la agenda del despacho Y en la de Atlas: es la
-- misma reunión vista dos veces. La sincronización la reconocía por la pareja
-- (cuenta, evento), así que la copia de la segunda cuenta era siempre una
-- reunión nueva. Y lo mismo por correo: la invitación que esta plataforma manda
-- vuelve a la bandeja de los invitados que son también cuentas de la casa, y
-- volvía a entrar como si alguien de fuera acabara de convocarla.
--
-- Se arregló en el código —el evento se reconoce ahora por lo que NO cambia al
-- cruzar de agenda: el identificador del evento y el UID de calendario—. Esto
-- hace las dos cosas que el código no puede hacer solo:
--
--   1. recoger las filas repetidas que ya quedaron;
--   2. poner en la base la regla que impide que vuelva a pasar, venga el
--      duplicado de esta plataforma o de donde venga.
--
-- LO QUE ESTO NO TOCA: ningún calendario de nadie. Todo es SQL sobre las filas
-- de aquí; no se borra ni un evento de Google ni se manda una sola cancelación
-- por correo. A quien tenga la cita apuntada no le cambia nada.
--
-- Y lo que se recoge no se tira: se guarda en `appointments_duplicadas` con la
-- fila que se quedó como buena. Si algo hubiera que devolver, está.


-- ------------------------------------------------------------
--  0. Las columnas, si la 033 todavía no pasó por aquí
-- ------------------------------------------------------------
-- Idempotente a propósito: esto tiene que poder ejecutarse venga detrás de la
-- 033 o sin ella. Lo que ya exista se queda como está.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS google_account  text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS google_updated  timestamptz;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS sincronizado_en timestamptz;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS external_uid    text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS ics_sequence    integer DEFAULT 0;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS origen text NOT NULL DEFAULT 'plataforma';
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS visto  boolean NOT NULL DEFAULT true;


-- ------------------------------------------------------------
--  1. Dónde va lo que se recoge
-- ------------------------------------------------------------
-- Una copia entera de la fila retirada (`fila`, en JSON: así no hay que ir
-- añadiendo columnas aquí cada vez que `appointments` gane una), con qué fila se
-- quedó en su lugar y por qué se las tomó por la misma reunión.
CREATE TABLE IF NOT EXISTS appointments_duplicadas (
  id          uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  cita_id     uuid,          -- la que se retiró
  se_quedo_id uuid,          -- la que se quedó en su lugar
  motivo      text,          -- evento | uid | invitacion_propia
  fila        jsonb,
  retirada_en timestamptz DEFAULT now()
);
ALTER TABLE appointments_duplicadas DISABLE ROW LEVEL SECURITY;


-- ------------------------------------------------------------
--  2. ANTES DE BORRAR NADA: mirar qué hay
-- ------------------------------------------------------------
-- Estas tres consultas no cambian nada. Conviene ejecutarlas primero y mirar el
-- resultado: es exactamente lo que el paso 3 va a retirar.
--
-- 2.a  El mismo evento de Google en varias filas
--
--   SELECT google_event_id, count(*) AS filas,
--          string_agg(DISTINCT coalesce(google_account, '—'), ', ') AS cuentas,
--          min(title) AS titulo, min(start_time) AS cuando
--     FROM appointments
--    WHERE google_event_id IS NOT NULL
--    GROUP BY google_event_id
--   HAVING count(*) > 1
--    ORDER BY filas DESC, cuando;
--
-- 2.b  El mismo UID de calendario en varias filas
--
--   SELECT lower(btrim(external_uid)) AS uid, count(*) AS filas,
--          min(title) AS titulo, min(start_time) AS cuando
--     FROM appointments
--    WHERE external_uid IS NOT NULL AND btrim(external_uid) <> ''
--    GROUP BY 1 HAVING count(*) > 1 ORDER BY filas DESC;
--
-- 2.c  Nuestras propias invitaciones, recibidas de vuelta como cita ajena
--
--   SELECT d.id AS duplicada, d.title, d.start_time, o.id AS original
--     FROM appointments d
--     JOIN appointments o
--       ON o.id::text = substring(lower(btrim(d.external_uid))
--                                 from '^cita-(.+)@calendario\.map$')
--    WHERE d.external_uid IS NOT NULL AND d.id <> o.id;


-- ------------------------------------------------------------
--  3. Recoger las repetidas
-- ------------------------------------------------------------
-- Qué fila se queda, de cada grupo. No es al azar: se queda la que tiene la
-- historia de la cita. Una cita nacida aquí lleva el tema, el cliente, su
-- aprobación y lo que se le haya editado; la copia que entró de fuera no lleva
-- nada de eso, sólo lo que Google cuenta del evento. Quedarse con la copia sería
-- perder la ficha y conservar la fotografía.
--
--   1.º  la que nació en la plataforma
--   2.º  la que alguien ya miró (`visto`), que es la que está saliendo en las
--        pantallas y en los avisos
--   3.º  la que tiene a qué evento de Google agarrarse
--   4.º  a igualdad de todo, la de id menor, para que esto dé el mismo resultado
--        si se ejecuta dos veces

-- 3.a  Varias filas para el mismo evento de Google
WITH grupos AS (
  SELECT id, google_event_id,
         first_value(id) OVER (
           PARTITION BY google_event_id
           ORDER BY (origen = 'plataforma') DESC, visto DESC, id) AS se_queda
    FROM appointments
   WHERE google_event_id IS NOT NULL
),
sobrantes AS (
  SELECT g.id, g.se_queda FROM grupos g WHERE g.id <> g.se_queda
),
guardadas AS (
  INSERT INTO appointments_duplicadas (cita_id, se_quedo_id, motivo, fila)
  SELECT s.id, s.se_queda, 'evento', to_jsonb(a)
    FROM sobrantes s JOIN appointments a ON a.id = s.id
  RETURNING cita_id
)
DELETE FROM appointments a
 USING guardadas g
 WHERE a.id = g.cita_id;

-- 3.b  Varias filas para el mismo UID de calendario
WITH grupos AS (
  SELECT id,
         first_value(id) OVER (
           PARTITION BY lower(btrim(external_uid))
           ORDER BY (origen = 'plataforma') DESC, visto DESC,
                    (google_event_id IS NOT NULL) DESC, id) AS se_queda
    FROM appointments
   WHERE external_uid IS NOT NULL AND btrim(external_uid) <> ''
),
sobrantes AS (
  SELECT g.id, g.se_queda FROM grupos g WHERE g.id <> g.se_queda
),
guardadas AS (
  INSERT INTO appointments_duplicadas (cita_id, se_quedo_id, motivo, fila)
  SELECT s.id, s.se_queda, 'uid', to_jsonb(a)
    FROM sobrantes s JOIN appointments a ON a.id = s.id
  RETURNING cita_id
)
DELETE FROM appointments a
 USING guardadas g
 WHERE a.id = g.cita_id;

-- 3.c  Nuestra propia invitación, que volvió a entrar como convocatoria ajena.
--      Se reconoce porque el UID lo puso esta plataforma y lleva dentro el
--      número de la cita original: `cita-<id>@calendario.map`.
WITH pares AS (
  SELECT d.id AS duplicada, o.id AS original
    FROM appointments d
    JOIN appointments o
      ON o.id::text = substring(lower(btrim(d.external_uid))
                                from '^cita-(.+)@calendario\.map$')
   WHERE d.external_uid IS NOT NULL AND d.id <> o.id
),
guardadas AS (
  INSERT INTO appointments_duplicadas (cita_id, se_quedo_id, motivo, fila)
  SELECT p.duplicada, p.original, 'invitacion_propia', to_jsonb(a)
    FROM pares p JOIN appointments a ON a.id = p.duplicada
  RETURNING cita_id
)
DELETE FROM appointments a
 USING guardadas g
 WHERE a.id = g.cita_id;


-- ------------------------------------------------------------
--  4. Que no vuelva a entrar dos veces
-- ------------------------------------------------------------
-- Esto es el cinturón, no el motor: el código ya reconoce la reunión repetida y
-- no la manda. Pero «ya lo arreglé en el código» es exactamente lo que se decía
-- la primera vez, y una agenda que se llena de citas fantasma no puede depender
-- de que nadie se vuelva a equivocar. Si un camino nuevo intenta meter por
-- segunda vez el mismo evento, aquí se le dice que no.
--
-- Si alguno de estos CREATE INDEX falla diciendo que hay claves repetidas, es
-- que quedan duplicados que los pasos de arriba no vieron: las consultas del
-- paso 2 dicen cuáles, sin tocar nada.

-- Un evento de Google pertenece a UNA cita, venga por la cuenta que venga. Es
-- precisamente lo que faltaba: la 033 lo permitía una vez por cuenta, y de ahí
-- salía un duplicado por cada cuenta de la casa invitada a la misma reunión.
DROP INDEX IF EXISTS appointments_evento_google_unico;
CREATE UNIQUE INDEX IF NOT EXISTS appointments_evento_unico
  ON appointments (google_event_id)
  WHERE google_event_id IS NOT NULL;

-- Y un UID de calendario, igual. Parcial (WHERE ... IS NOT NULL) porque la
-- inmensa mayoría de las citas no tiene identificador externo, y varios NULL no
-- chocan entre sí.
DROP INDEX IF EXISTS appointments_uid_externo_unico;
CREATE UNIQUE INDEX IF NOT EXISTS appointments_uid_unico
  ON appointments (lower(btrim(external_uid)))
  WHERE external_uid IS NOT NULL AND btrim(external_uid) <> '';

-- Las consultas de cada pasada de sincronización: «¿tengo ya este evento?»,
-- «¿tengo ya este UID?», «¿qué lleva esta cuenta?».
CREATE INDEX IF NOT EXISTS appointments_cuenta_idx
  ON appointments (google_account) WHERE google_account IS NOT NULL;

-- El aviso de «te agendaron algo y no lo has mirado».
CREATE INDEX IF NOT EXISTS appointments_sin_ver_idx
  ON appointments (visto, start_time) WHERE visto = false;


-- ------------------------------------------------------------
--  5. Cómo saber que quedó bien
-- ------------------------------------------------------------
--   -- Cuántas se recogieron, y por qué:
--   SELECT motivo, count(*) FROM appointments_duplicadas GROUP BY motivo;
--
--   -- Y que no queda ninguna repetida (las dos tienen que dar 0 filas):
--   SELECT google_event_id, count(*) FROM appointments
--    WHERE google_event_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
--   SELECT lower(btrim(external_uid)), count(*) FROM appointments
--    WHERE external_uid IS NOT NULL AND btrim(external_uid) <> ''
--    GROUP BY 1 HAVING count(*) > 1;
