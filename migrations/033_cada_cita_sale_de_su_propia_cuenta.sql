-- ============================================================
--  Una sola agenda, que entra y sale por donde haga falta
--  Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- ============================================================
--
-- Dos cosas estaban mal, y son la misma cosa vista por sus dos lados.
--
-- La primera: TODAS las citas nacían en una sola cuenta de Google
-- (mposligua0000@gmail.com) y a la cuenta que de verdad correspondía —jomap,
-- atlas, csccue, hotmail— se le mandaba una invitación como si fuera un
-- asistente más. Quien recibía la cita veía una cuenta personal donde esperaba
-- al despacho o a la institución.
--
-- La segunda, y la que más duele: sólo funcionaba de salida. Google era el
-- sitio donde de verdad vivían los eventos y la plataforma un emisor que no
-- escuchaba. Mover una cita desde el móvil, aceptar una convocatoria en
-- Outlook o apuntar una reunión directamente en el calendario no llegaba aquí
-- nunca. La plataforma decía que el martes estaba libre y el martes había
-- audiencia.
--
-- Lo que se arregla: el programa pasa a ser el sitio donde está la agenda
-- entera, y los eventos entran y salen por donde toque —Google, correo, o la
-- propia pantalla—. Todo va a `appointments`, no a una lista aparte de sólo
-- lectura: una cita que no se puede tocar desde donde se mira no es una agenda,
-- es una fotografía.
--
--   calendar_config.cuenta_email        de qué cuenta sale ese calendario
--   calendar_config.proveedor           google (API) | microsoft (invitación)
--   calendar_config.sincronizar_entrada si además se trae lo que aparezca allí
--   appointments.google_account         en qué cuenta vive el evento
--   appointments.origen                 nació aquí, o se recogió de fuera
--   appointments.google_updated         qué versión de fuera se conoce ya
--   appointments.external_uid           el UID del .ics, cuando entra por correo
--   appointments.ics_sequence           versión de la invitación por correo
--   appointments.visto                  lo de fuera, ¿ya lo miró alguien?


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

-- Traer lo que aparezca en esa agenda se puede apagar calendario por
-- calendario. Por defecto está encendido —el objetivo es ver el día entero—,
-- pero una cuenta personal cuya agenda no tiene por qué salir en la pantalla
-- del despacho se apaga aquí sin dejar de poder agendar EN ella.
ALTER TABLE calendar_config
  ADD COLUMN IF NOT EXISTS sincronizar_entrada boolean NOT NULL DEFAULT true;


-- ------------------------------------------------------------
--  2. Dónde vive cada cita, y qué versión de ella conocemos
-- ------------------------------------------------------------
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS google_account text;

-- Se guarda EN LA CITA y no se deduce del calendario, a propósito: si mañana
-- un calendario cambia de cuenta, el evento viejo sigue estando donde se creó,
-- y para borrarlo o moverlo hay que ir a esa cuenta. Deducirlo dejaría eventos
-- huérfanos en calendarios que nadie vuelve a mirar.
UPDATE appointments
   SET google_account = 'mposligua0000@gmail.com'
 WHERE google_event_id IS NOT NULL
   AND (google_account IS NULL OR btrim(google_account) = '');

-- Nació aquí, o se recogió de fuera. No es una etiqueta decorativa: decide si
-- la cita pasa por el circuito de aprobación del despacho (lo que se pide aquí
-- se aprueba aquí) o entra ya confirmada porque el compromiso lo adquirió otro.
ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS origen text NOT NULL DEFAULT 'plataforma';

-- La marca de tiempo que Google le pone al evento. Es lo que permite saber de
-- qué lado vino un cambio: si lo que hay en Google es más nuevo que la última
-- versión que conocemos, el cambio se hizo allí y hay que traerlo; si coincide,
-- el cambio salió de aquí y no hay nada que hacer. Sin esto, cada pasada
-- «traería» lo que la propia plataforma acababa de escribir, y a la larga un
-- cambio hecho aquí se pisaría con su propio eco.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS google_updated  timestamptz;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS sincronizado_en timestamptz;

-- El identificador del evento cuando entra por correo (.ics) en vez de por la
-- API: las cuentas de Microsoft no tienen id de Google, tienen UID de iCalendar.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS external_uid text;

-- Número de versión de la invitación por correo. Un calendario sólo acepta una
-- modificación si viene con un número MAYOR que el que ya tiene apuntado:
-- mandar el cambio de hora con el mismo número deja al invitado con la hora
-- vieja y sin ninguna señal de que algo cambió.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS ics_sequence integer DEFAULT 0;

-- Lo que entra de fuera empieza sin mirar. Lo que nace aquí ya lo vio quien lo
-- creó, así que no tiene sentido reclamarle que lo mire.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS visto boolean NOT NULL DEFAULT true;


-- ------------------------------------------------------------
--  3. Que el mismo evento no entre dos veces
-- ------------------------------------------------------------
-- La sincronización pasa cada cuarto de hora y vuelve a ver lo mismo. Sin esto,
-- la misma reunión se acumularía una vez por pasada hasta llenar el día.
-- Parciales (WHERE ... IS NOT NULL) porque la inmensa mayoría de las citas no
-- tienen todavía identificador externo, y varios NULL no pueden chocar entre sí.
CREATE UNIQUE INDEX IF NOT EXISTS appointments_evento_google_unico
  ON appointments (google_account, google_event_id)
  WHERE google_event_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS appointments_uid_externo_unico
  ON appointments (external_uid)
  WHERE external_uid IS NOT NULL;

-- La consulta de cada pasada: «de esta cuenta, ¿qué tengo ya?».
CREATE INDEX IF NOT EXISTS appointments_cuenta_idx
  ON appointments (google_account) WHERE google_account IS NOT NULL;

-- El aviso de «te agendaron algo y no lo has mirado».
CREATE INDEX IF NOT EXISTS appointments_sin_ver_idx
  ON appointments (visto, start_time) WHERE visto = false;


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
ALTER TABLE ms_tokens DISABLE ROW LEVEL SECURITY;
