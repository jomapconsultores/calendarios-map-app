-- =============================================================================
-- 022_rls_bloqueaba_los_modulos.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- CORRIGE UN FALLO DE LA MIGRACIÓN 020.
--
-- La 020 terminaba con ENABLE ROW LEVEL SECURITY sobre las cinco tablas nuevas
-- pero NO creaba ninguna política. En PostgreSQL eso no es "más seguro": es
-- denegar absolutamente todo. Y la aplicación se conecta con la clave `anon`,
-- que sí respeta RLS —a diferencia de `service_role`, que lo ignora—, así que
-- el Directorio y el Cronograma devolvían cero filas siempre, sin ningún error
-- visible: las pantallas simplemente salían vacías.
--
-- Se desactiva RLS en esas tablas para dejarlas como las otras 22 del sistema
-- (users, tasks, appointments, roles…). NO es una relajación de la seguridad
-- respecto al resto del proyecto: es ponerlas en el MISMO modelo que ya usa
-- todo lo demás, donde la autorización la hace la aplicación en Python —rol
-- activo, módulos, proyectos, cuentas de Microsoft— y la clave de Supabase vive
-- sólo en el servidor, nunca en el navegador.
--
-- Si algún día se quiere seguridad a nivel de fila de verdad, hay que hacerlo
-- en el sistema entero y con políticas escritas, no tabla por tabla y sin ellas.
--
-- Idempotente. Aplicar después de la 020 y la 021.
-- =============================================================================

ALTER TABLE sectors          DISABLE ROW LEVEL SECURITY;
ALTER TABLE contacts         DISABLE ROW LEVEL SECURITY;
ALTER TABLE contact_audit    DISABLE ROW LEVEL SECURITY;
ALTER TABLE gantt_plans      DISABLE ROW LEVEL SECURITY;
ALTER TABLE gantt_activities DISABLE ROW LEVEL SECURITY;

-- ─────────────────────────────────────────────────────────────────────────────
--  El mismo fallo venía de antes en tres tablas más. Estaban con RLS activado y
--  sin políticas desde sus propias migraciones, así que el inicio de sesión con
--  huella o rostro y los permisos por cuenta de Microsoft llevaban tiempo sin
--  poder leer ni escribir nada — fallando en silencio, porque el código captura
--  la excepción y sigue.
--
--  Se dejan comentadas: descoméntalas para arreglarlas también.
-- ─────────────────────────────────────────────────────────────────────────────
-- ALTER TABLE webauthn_credentials   DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE face_descriptors       DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE ms_account_permissions DISABLE ROW LEVEL SECURITY;
