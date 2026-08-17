-- ============================================================
--  Calendario de vencimientos de proyectos y actividades
--  Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- ============================================================
--
-- Un proyecto se abre con fecha de vencimiento y con un responsable —quien lo
-- crea—, y sus actividades llevan su propio plazo. Cuando la fecha pasa sin que
-- el trabajo esté terminado, el compromiso está INCUMPLIDO y sale un correo.
--
-- Esta tabla es la constancia de esos avisos. Cumple dos funciones:
--   1) Que el hilo de fondo y el cron externo no manden el mismo aviso dos
--      veces el mismo día (de ahí el UNIQUE por tipo + referencia + fecha).
--   2) Dejar por escrito de qué se avisó, cuándo y a quién, que es lo que
--      convierte un recordatorio en algo que se puede reclamar después.
--
-- No hace falta tocar `projects` ni `tasks`: el responsable ya vive en
-- projects.owner / tasks.assigned_to, y los plazos en due_date.

CREATE TABLE IF NOT EXISTS vencimiento_avisos (
  id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  tipo            text NOT NULL,          -- 'proyecto' | 'actividad'
  ref_id          uuid NOT NULL,          -- projects.id o tasks.id
  fecha           date NOT NULL,          -- día en que se avisó
  destinatario    text,
  titulo          text,
  due_date        date,                   -- la fecha que se incumplió
  dias_incumplido int,
  responsable     text,
  enviado_en      timestamptz DEFAULT now()
);

-- La misma cosa no se avisa dos veces el mismo día.
CREATE UNIQUE INDEX IF NOT EXISTS vencimiento_avisos_unico
  ON vencimiento_avisos (tipo, ref_id, fecha);

-- La consulta que hace el sistema en cada revisión: "¿de qué avisé hoy?".
CREATE INDEX IF NOT EXISTS vencimiento_avisos_fecha_idx
  ON vencimiento_avisos (fecha DESC);

-- La revisión diaria pregunta "qué venció antes de hoy y sigue sin cerrarse".
-- Sin índice, esa consulta se pasaba del tiempo límite de PostgreSQL en frío y
-- volvía vacía: el sistema habría dado por bueno que no se incumple nada.
CREATE INDEX IF NOT EXISTS tasks_due_date_pendientes_idx
  ON tasks (due_date) WHERE status <> 'done';

CREATE INDEX IF NOT EXISTS projects_due_date_idx
  ON projects (due_date);

-- El resto de tablas del módulo no tienen RLS activo (ver 022): el servidor
-- entra con la clave de servicio y filtra por permisos en la aplicación. Esta
-- se deja igual para no ser la única que se comporta distinto.
ALTER TABLE vencimiento_avisos DISABLE ROW LEVEL SECURITY;

-- Los proyectos que ya existían no tienen responsable escrito. Se les pone el
-- de quien los creó, que es la regla que sigue de aquí en adelante.
UPDATE projects p
   SET owner = u.full_name
  FROM users u
 WHERE p.created_by = u.id::text
   AND (p.owner IS NULL OR btrim(p.owner) = '')
   AND u.full_name IS NOT NULL;
