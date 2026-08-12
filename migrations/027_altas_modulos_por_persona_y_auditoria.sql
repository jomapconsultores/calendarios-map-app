-- =============================================================================
-- 027_altas_modulos_por_persona_y_auditoria.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- El administrador pasa a ser el ÚNICO que abre la puerta del sistema.
--
-- Hasta ahora /register era público: cualquiera que llegara a la dirección se
-- creaba una cuenta con su propia clave y ENTRABA. Lo que esperaba al
-- administrador era sólo el acceso a los calendarios, no el ingreso en sí. Se
-- cierra ese registro y el alta pasa al panel, donde ya estaba el resto del
-- gobierno de accesos (ver el cambio en app/__init__.py).
--
-- Esta migración añade las tres piezas que faltaban para sostenerlo:
--
--   * users.is_active — la columna YA EXISTÍA, pero no se consultaba en NINGUNA
--     línea del sistema: era una bandera que no cerraba ninguna puerta. Aquí
--     sólo se asegura su presencia y su valor por defecto; quien le da sentido
--     es el código (ver load_user y _cuenta_activa). Hasta ahora la única forma
--     de cortarle el acceso a alguien era ELIMINARLO, y con él se iban sus
--     roles y sus permisos. Quien se va de la oficina deja de entrar, pero su
--     rastro en el sistema tiene que quedar.
--
--   * user_modules — módulos sueltos concedidos a UNA persona. Los módulos
--     venían sólo por rol, así que para dar Directorio a una sola persona había
--     que inventarle un rol entero. Esto se suma a lo del rol, nunca lo resta:
--     los permisos se acumulan, no compiten.
--
--   * permission_audit — quién concedió o retiró qué, a quién y cuándo. Un
--     sistema donde el administrador reparte accesos y nadie puede reconstruir
--     después quién dio qué no es un sistema de permisos, es una costumbre.
--
-- Sobre RLS: las tablas nuevas quedan SIN row level security, igual que las
-- otras del proyecto. No es relajar nada — es el modelo que ya usa todo el
-- sistema (la autorización la hace la aplicación en Python y la clave de
-- Supabase vive sólo en el servidor). Activarla sin escribir políticas es
-- denegarlo todo en silencio, que es exactamente el fallo que corrigió la 022.
--
-- Idempotente. Aplicar después de la 026.
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
--  1. Cuentas activables
--     «is_active» ya existe en esta base; el IF NOT EXISTS es para instalaciones
--     nuevas. DEFAULT true: las cuentas que ya están siguen entrando. Una
--     migración de permisos jamás debe dejar a nadie fuera por omisión.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE users ALTER COLUMN is_active SET DEFAULT true;
UPDATE users SET is_active = true WHERE is_active IS NULL;

-- Quién dio de alta a esta persona y cuándo. Sirve para distinguir las cuentas
-- creadas desde el panel de las que quedaron del registro público antiguo.
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by_admin uuid;

-- ─────────────────────────────────────────────────────────────────────────────
--  2. Módulos sueltos por persona
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_modules (
  id         uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id    uuid NOT NULL,
  modulo     text NOT NULL,          -- 'calendar', 'planning', 'todo', 'cronograma', 'directorio'
  granted_by uuid,
  created_at timestamptz DEFAULT now(),
  UNIQUE (user_id, modulo)
);

ALTER TABLE user_modules DROP CONSTRAINT IF EXISTS user_modules_user_fkey;
ALTER TABLE user_modules
  ADD CONSTRAINT user_modules_user_fkey
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_user_modules_user ON user_modules (user_id);

-- ─────────────────────────────────────────────────────────────────────────────
--  3. Auditoría de permisos
--     Se guardan los correos además de los identificadores: si mañana se borra
--     una cuenta, el registro tiene que seguir diciendo A QUIÉN se le dio qué.
--     Un historial que se vacía cuando desaparece el implicado no sirve de nada.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS permission_audit (
  id             uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  actor_id       uuid,
  actor_email    text,
  target_user_id uuid,
  target_email   text,
  accion         text NOT NULL,      -- alta, baja, estado, roles, modulos, permisos, clave
  detalle        text,               -- resumen legible de lo concedido y lo retirado
  ip             text,
  created_at     timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_permission_audit_fecha
  ON permission_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_permission_audit_persona
  ON permission_audit (target_user_id, created_at DESC);

-- Sin FK a users a propósito: el registro debe sobrevivir al borrado de la
-- persona auditada, que es justo cuando más falta hace.

-- ─────────────────────────────────────────────────────────────────────────────
--  4. Mismo modelo que el resto del sistema (ver cabecera y la 022)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE user_modules     DISABLE ROW LEVEL SECURITY;
ALTER TABLE permission_audit DISABLE ROW LEVEL SECURITY;
