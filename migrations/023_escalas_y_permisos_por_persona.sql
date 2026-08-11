-- =============================================================================
-- 023_escalas_y_permisos_por_persona.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Alinea el sistema con el modelo que ya usa ATLAS:
--
--   1) LAS MISMAS ESCALAS. ATLAS distingue admin, socio, secretaria, profesor y
--      psicólogo. Aquí sólo había administrador, socio y funcionario, así que a
--      una secretaria había que meterla en el cajón de «funcionario» y se perdía
--      la distinción. Se añaden las tres que faltaban.
--
--   2) PERMISOS POR PERSONA, NO SÓLO POR ROL. ATLAS guarda en
--      `usuario_permisos` una fila por usuario y módulo, con notación
--      `familia.submodulo` (academico.asignaturas, personas.docentes...). Eso
--      permite afinar a una persona concreta sin tener que inventarle un rol
--      nuevo. Aquí faltaba por completo: el rol daba el módulo entero o nada.
--
-- El rol sigue siendo la base —lo que da acceso a un módulo— y estos permisos
-- son la capa fina encima: habilitan las acciones delicadas (importar, exportar,
-- eliminar, planificar con IA, administrar sectores) persona a persona.
--
-- Idempotente. Aplicar después de la 022.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
--  1. Permisos por persona y submódulo
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_permissions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Notación `modulo.accion`, igual que ATLAS: directorio.importar,
  -- cronograma.planificar_ia, calendar.aprobar...
  permiso     text NOT NULL,
  granted_by  uuid,
  granted_at  timestamptz DEFAULT now()
);

-- Una persona no puede tener dos veces el mismo permiso.
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_permissions_unico
  ON user_permissions (user_id, permiso);
CREATE INDEX IF NOT EXISTS idx_user_permissions_user
  ON user_permissions (user_id);

-- Sin RLS, igual que el resto de tablas del sistema. Activarlo sin políticas
-- fue justo el fallo que corrigió la migración 022: deniega todo en silencio.

-- ─────────────────────────────────────────────────────────────────────────────
--  2. Las escalas nuevas, también en la base
--
--  `roles.level` NO era texto libre: tenía un CHECK que sólo aceptaba
--  administrador, socio y funcionario. Crear un rol con escala 'secretaria'
--  fallaba con `roles_level_chk`. Se rehace la restricción con las seis escalas,
--  las mismas que ROLE_LEVELS en app/__init__.py.
--
--  La restricción se conserva (no se borra sin más) porque es lo que impide que
--  un rol acabe con una escala inventada que luego ninguna pantalla sabe pintar.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE roles DROP CONSTRAINT IF EXISTS roles_level_chk;
ALTER TABLE roles ADD CONSTRAINT roles_level_chk CHECK (
  level = ANY (ARRAY['administrador'::text, 'socio'::text, 'funcionario'::text,
                     'secretaria'::text, 'profesor'::text, 'psicologo'::text])
);
