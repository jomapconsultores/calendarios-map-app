-- ============================================================
--  La tabla de permisos de Microsoft, con los nombres que el código usa
-- ============================================================
--
-- `ms_tokens` existía en producción, pero no como la creó la 033: tenía
-- `expires_at` y `created_at` donde el código lee `token_expiry`,
-- `authority` y `actualizado_en`. Venía de una versión anterior —sus dos
-- filas son de junio— y nadie lo notó porque el fallo no se parecía en nada
-- a su causa.
--
-- Lo que se veía: se autorizaba una cuenta de Outlook, Microsoft daba el
-- permiso («Ha iniciado sesión en la aplicación… ya puede cerrar esta
-- ventana»), y la cuenta seguía saliendo SIN AUTORIZAR. Por dentro, cada
-- consulta que nombraba una de las columnas ausentes se contestaba con un
-- error que el cliente convierte en lista vacía, así que el sistema leía
-- «no hay ningún permiso guardado» — que es indistinguible de no haber
-- autorizado nunca. Y al guardar, el INSERT nombraba esas mismas columnas y
-- no entraba: el permiso se concedía y se perdía en el mismo segundo.
--
-- El síntoma que delató el enredo fue que la misma página daba dos cuentas
-- distintas: el aviso de la cabecera pide `email, refresh_token` —columnas
-- que sí estaban— y veía una cuenta autorizada; la tabla de abajo pide
-- además `token_expiry` y las veía todas sin autorizar.
--
-- Se añaden las columnas que faltan en vez de recrear la tabla, y se
-- conserva lo que ya había: las dos filas de junio se quedan, con su fecha
-- de caducidad copiada al nombre nuevo. Si esos permisos ya no valen, el
-- sistema lo dirá al renovarlos y bastará con volver a autorizar; borrarlos
-- aquí sería tirar algo que quizá todavía sirve.
--
-- Las viejas `expires_at` y `created_at` NO se eliminan: no molestan, y
-- borrar columnas de una tabla en producción para dejarla bonita es la
-- clase de arreglo que rompe lo que no se había mirado.

ALTER TABLE ms_tokens ADD COLUMN IF NOT EXISTS token_expiry   timestamptz;
ALTER TABLE ms_tokens ADD COLUMN IF NOT EXISTS authority      text;
ALTER TABLE ms_tokens ADD COLUMN IF NOT EXISTS actualizado_en timestamptz DEFAULT now();

UPDATE ms_tokens SET token_expiry = expires_at
 WHERE token_expiry IS NULL AND expires_at IS NOT NULL;

UPDATE ms_tokens SET actualizado_en = created_at
 WHERE actualizado_en IS NULL AND created_at IS NOT NULL;

-- Por dónde se renueva cada una. Las personales viven en /consumers y las de
-- organización en /organizations; pedirle a Microsoft la renovación por la
-- puerta equivocada devuelve un error que parece de permiso revocado.
UPDATE ms_tokens
   SET authority = CASE
         WHEN lower(email) LIKE '%@hotmail.com'
           OR lower(email) LIKE '%@outlook.com'
           OR lower(email) LIKE '%@live.com'
           OR lower(email) LIKE '%@msn.com'
         THEN 'https://login.microsoftonline.com/consumers'
         ELSE 'https://login.microsoftonline.com/organizations'
       END
 WHERE authority IS NULL;

-- Una cuenta, un permiso. Sin esto, cada autorización nueva podía dejar una
-- fila más y la que se leyera dependía del orden que devolviera la consulta.
CREATE UNIQUE INDEX IF NOT EXISTS ms_tokens_email_unico ON ms_tokens (lower(email));

-- Igual que el resto del sistema (ver 022): el servidor entra con la clave de
-- servicio y filtra por permisos en la aplicación.
ALTER TABLE ms_tokens DISABLE ROW LEVEL SECURITY;
