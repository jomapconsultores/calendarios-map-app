-- =============================================================================
-- 019_cuenta_autoservicio.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Módulo de administración de la propia cuenta:
--   * El usuario (administrador, socio, funcionario) mantiene sus datos.
--   * Cambia su clave verificando la anterior.
--   * Si la olvidó, el administrador la restablece con una clave temporal de un
--     solo uso; el sistema obliga a cambiarla al entrar.
--
-- Idempotente. Aplicar en el editor SQL de Supabase.
-- =============================================================================

-- Datos de contacto que el propio usuario mantiene
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS position text;

-- Control de clave temporal
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS temp_password_expires timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_updated_at   timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_by     uuid;

-- Bitácora de restablecimientos (nunca guarda la clave, solo el hecho)
CREATE TABLE IF NOT EXISTS password_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  action      text NOT NULL,            -- 'reset_admin' | 'self_change'
  executed_by uuid,
  ip          text,
  created_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_password_log_user
  ON password_log (user_id, created_at DESC);

-- SIN RLS, como el resto de tablas del sistema. Aquí decía ENABLE, y activarlo
-- sin ninguna política no significa «sin restricciones» sino «deniega todo»:
-- la aplicación entra con la llave anónima, que sí respeta RLS, así que no
-- habría podido escribir ni un solo apunte en la bitácora y nadie se habría
-- enterado. Es el mismo fallo que corrigieron las migraciones 022 y 025.
ALTER TABLE password_log DISABLE ROW LEVEL SECURITY;
