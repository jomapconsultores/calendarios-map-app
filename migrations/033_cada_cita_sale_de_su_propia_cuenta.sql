-- ============================================================
--  Cada cita sale de la cuenta que le corresponde, y lo que agendan entra
--  Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- ============================================================
--
-- Hasta ahora TODAS las citas nacían en una sola cuenta de Google
-- (mposligua0000@gmail.com) y a la cuenta que de verdad correspondía —jomap,
-- atlas, csccue, hotmail— se le mandaba una invitación como si fuera un
-- asistente más. Eso tiene dos consecuencias que se notan del otro lado:
--
--   1. El que recibe la cita ve que se la manda mposligua0000, no el despacho
--      ni la institución. El remitente es parte del mensaje.
--   2. Sólo funciona hacia afuera. Lo que a esas cuentas les agendan a ELLAS
--      —un cliente que propone una reunión, una convocatoria del CSCCUE— no
--      existe para la plataforma, porque la plataforma no mira esas cuentas.
--
-- Esta migración pone las tres piezas que hacen falta para lo uno y lo otro:
--
--   calendar_config.cuenta_email   de qué cuenta sale ese calendario
--   appointments.google_account    en qué cuenta quedó el evento de esta cita
--   agenda_externa                 lo que agendan en esas cuentas y no nació aquí
--
-- `google_account` se guarda EN LA CITA, no se deduce del calendario, y es a
-- propósito: si mañana un calendario cambia de cuenta, el evento viejo sigue
-- estando donde se creó, y para borrarlo o moverlo hay que ir a esa cuenta, no
-- a la que ahora figura en la configuración. Deducirlo sería dejar eventos
-- huérfanos en calendarios que nadie vuelve a mirar.


-- ------------------------------------------------------------
--  1. De qué cuenta sale cada calendario
-- ------------------------------------------------------------
ALTER TABLE calendar_config ADD COLUMN IF NOT EXISTS cuenta_email text;
ALTER TABLE calendar_config ADD COLUMN IF NOT EXISTS proveedor    text;

-- El contacto que ya tenía cada calendario ES, en la práctica, la cuenta a la
-- que pertenece: a jomap se le invitaba a jomap, a atlas a atlas. Así que se
-- toma de ahí, y sólo para las nueve direcciones conocidas —no para un
-- contacto cualquiera que alguien haya escrito en ese campo, que dejaría a la
-- plataforma intentando agendar en la cuenta de un cliente.
UPDATE calendar_config
   SET cuenta_email = lower(btrim(email))
 WHERE (cuenta_email IS NULL OR btrim(cuenta_email) = '')
   AND lower(btrim(email)) IN (
        'mposligua0000@gmail.com',
        'jomapconsultores@gmail.com',
        'marcoantonioposligua@gmail.com',
        'grupjf0000@gmail.com',
        'mapfinanzas@gmail.com',
        'atlas.cenest@gmail.com',
        'sede243cpv@gmail.com',
        'maposligua@hotmail.com',
        'marcoposligua@csccue.gob.ec');

-- Lo que no encajó en ninguna de las nueve se queda donde ha estado siempre.
-- Se deja dicho de forma explícita en vez de dejarlo en NULL: un NULL aquí
-- obliga a que el código adivine, y lo que adivina hoy puede no ser lo que
-- adivine mañana. Después de aplicar esto conviene mirar /admin/cuentas, que
-- enseña qué cuenta quedó en cada calendario y permite corregir lo que haga
-- falta desde Catálogos → Calendarios.
UPDATE calendar_config
   SET cuenta_email = 'mposligua0000@gmail.com'
 WHERE cuenta_email IS NULL OR btrim(cuenta_email) = '';

-- 'google' se agenda con la API de Calendar; 'microsoft' por invitación de
-- correo (.ics), que es lo único que admiten csccue y hotmail sin registrar
-- una aplicación propia en Entra.
UPDATE calendar_config
   SET proveedor = CASE
         WHEN lower(cuenta_email) LIKE '%@gmail.com' THEN 'google'
         ELSE 'microsoft'
       END
 WHERE proveedor IS NULL OR btrim(proveedor) = '';


-- ------------------------------------------------------------
--  2. En qué cuenta quedó el evento de cada cita
-- ------------------------------------------------------------
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS google_account text;

-- Número de versión de la invitación por correo. Un calendario sólo acepta una
-- modificación si viene con un número MAYOR que el que ya tiene apuntado:
-- mandar el cambio de hora con el mismo número deja al invitado con la hora
-- vieja y sin ninguna señal de que algo cambió.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS ics_sequence integer DEFAULT 0;

-- Todo lo sincronizado hasta hoy está en la cuenta histórica. Sin este
-- relleno, mover o cancelar una cita vieja buscaría su evento en la cuenta
-- equivocada y no lo encontraría: el calendario del cliente se quedaría con la
-- versión anterior sin que nadie se entere.
UPDATE appointments
   SET google_account = 'mposligua0000@gmail.com'
 WHERE google_event_id IS NOT NULL
   AND (google_account IS NULL OR btrim(google_account) = '');


-- ------------------------------------------------------------
--  3. Lo que agendan en esas cuentas y no nació aquí
-- ------------------------------------------------------------
--
-- Esta tabla NO es la de citas. Son dos cosas distintas y mezclarlas sería un
-- error: una cita de `appointments` es un compromiso del despacho, que alguien
-- pidió, alguien aprobó y de la que se responde. Esto de aquí es lo que otros
-- pusieron en la agenda —una convocatoria, una reunión a la que le invitaron—,
-- sobre lo que la plataforma no manda: sólo lo enseña, para que al mirar el
-- calendario esté TODO lo que ocupa el día y no sólo la mitad que salió de
-- aquí. Si algo de esto merece convertirse en cita del despacho, se convierte,
-- y `appointment_id` deja constancia de que ya se hizo.
CREATE TABLE IF NOT EXISTS agenda_externa (
  id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,

  -- De dónde salió
  cuenta_email    text NOT NULL,          -- la cuenta en cuya agenda apareció
  origen          text NOT NULL DEFAULT 'google',   -- 'google' | 'correo'
  event_id        text NOT NULL,          -- id del evento en Google, o UID del .ics
  gcal_id         text,                   -- calendario de origen dentro de esa cuenta
  calendar_id     text,                   -- nuestro slug, si la cuenta mapea a uno

  -- Qué es
  titulo          text,
  descripcion     text,
  start_time      timestamptz,
  end_time        timestamptz,
  todo_el_dia     boolean DEFAULT false,
  lugar           text,
  enlace          text,                   -- Meet, Teams, Zoom…

  -- Quién
  organizador       text,                 -- correo de quien lo convocó
  organizador_nombre text,
  invitados       text,

  -- En qué quedó
  mi_respuesta    text,                   -- accepted | declined | tentative | needsAction
  estado          text DEFAULT 'activo',  -- activo | cancelado
  visto           boolean DEFAULT false,  -- ya lo miró alguien en la plataforma
  appointment_id  uuid,                   -- se convirtió en cita del despacho

  actualizado_en  timestamptz DEFAULT now(),
  creado_en       timestamptz DEFAULT now()
);

-- El mismo evento visto dos veces es el mismo evento: la sincronización lo
-- vuelve a traer en cada pasada y tiene que ACTUALIZAR, no acumular copias.
CREATE UNIQUE INDEX IF NOT EXISTS agenda_externa_unico
  ON agenda_externa (cuenta_email, event_id);

-- La consulta del calendario: qué hay entre estas dos fechas.
CREATE INDEX IF NOT EXISTS agenda_externa_fecha_idx
  ON agenda_externa (start_time);

-- El aviso de "te agendaron algo y no lo has mirado".
CREATE INDEX IF NOT EXISTS agenda_externa_sin_ver_idx
  ON agenda_externa (visto, start_time) WHERE estado = 'activo';


-- ------------------------------------------------------------
--  4. El permiso de las cuentas de Microsoft
-- ------------------------------------------------------------
--
-- Las cuentas de Microsoft (hotmail, csccue) no admiten contraseña de
-- aplicación: hay que pasar por OAuth. El token se guarda aquí porque el
-- servidor tiene varios workers y ninguno puede quedarse con el permiso en su
-- propia memoria. El `refresh_token` es lo que evita tener que volver a
-- autorizar a mano cada pocos días: mientras se use, se renueva solo.
CREATE TABLE IF NOT EXISTS ms_tokens (
  id             uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  email          text NOT NULL,
  refresh_token  text,
  access_token   text,
  token_expiry   timestamptz,
  authority      text,        -- .../consumers (hotmail) | .../organizations (csccue)
  actualizado_en timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ms_tokens_email_unico ON ms_tokens (lower(email));

-- Igual que el resto del sistema (ver 022): el servidor entra con la clave de
-- servicio y filtra por permisos en la aplicación.
ALTER TABLE agenda_externa DISABLE ROW LEVEL SECURITY;
ALTER TABLE ms_tokens      DISABLE ROW LEVEL SECURITY;
