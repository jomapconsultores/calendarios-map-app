-- ============================================================
--  Los correos relacionados, donde los pueda ver el servidor
-- ============================================================
--
-- Igual que con los quipux (ver 034): el correo se lee en la computadora de la
-- persona, que es donde están las credenciales de las nueve cuentas —la
-- contraseña de aplicación en el llavero de Windows, el token de Microsoft en
-- su caché cifrada—, y eso no se mueve a un servidor.
--
-- Lo que sube es poco y a propósito: SÓLO los correos que tienen que ver con
-- otra cosa de la plataforma —un quipux, una invitación, un plazo—, y de cada
-- uno el remitente, el asunto y el principio del texto. No sube el buzón. Lo
-- que no está relacionado con nada no le sirve al servidor, y un servidor con
-- el correo entero de nueve cuentas es un riesgo que nada aquí justifica.
--
-- Es una FOTO, como `quipux_documentos`: lo que se hace con un plazo vive en el
-- cronograma, con sus reglas y su bitácora.

CREATE TABLE IF NOT EXISTS correo_mensajes (
  -- Una huella del Message-ID. Es la misma en las nueve cuentas, así que el
  -- correo que llegó a tres buzones es una sola fila, y una segunda pasada
  -- actualiza en vez de duplicar.
  id           text PRIMARY KEY,

  fecha        text,          -- ISO, con zona, como la dio el correo
  de           text,
  asunto       text,
  resumen      text,          -- el principio del texto, recortado
  cuentas      text,          -- en qué buzones de la casa está, separados por coma
  n_adjuntos   integer DEFAULT 0,
  enlace       text,          -- para abrirlo en Gmail, cuando está en un Gmail

  -- Con qué tiene que ver: [{tipo, clave, detalle, seguro}] en JSON. Va en
  -- texto y no en una tabla aparte porque se lee y se escribe siempre entero
  -- con su mensaje, y son unos cientos de filas.
  vinculos     text,

  actualizado  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS correo_mensajes_fecha_idx ON correo_mensajes (fecha DESC);

-- Igual que el resto del sistema (ver 022): el servidor entra con la clave de
-- servicio y filtra por permisos en la aplicación.
ALTER TABLE correo_mensajes DISABLE ROW LEVEL SECURITY;
