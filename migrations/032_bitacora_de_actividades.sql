-- ============================================================
--  Bitácora de actividades: nada se borra ni se mueve sin decir por qué
--  Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- ============================================================
--
-- Un plazo que se puede correr sin dar explicaciones no es un plazo. Y una
-- actividad que se puede borrar sin dejar rastro convierte el incumplimiento en
-- algo que se arregla con la tecla de suprimir: si el viernes no llegó, el
-- lunes ya no existía. Esta tabla es lo que impide las dos cosas.
--
-- Guarda COPIAS del título, el proyecto y el responsable, no sólo referencias.
-- Es a propósito: el apunte tiene que seguir contando la historia cuando la
-- actividad ya no está, que es justamente el caso que más importa. Por eso
-- tampoco hay clave foránea contra `tasks` — borrar la actividad no puede
-- llevarse por delante la constancia de que se borró.
--
-- Lo que se apunta:
--   creado        alta de la actividad            (sin justificación)
--   reprogramado  se movió una fecha              (JUSTIFICACIÓN OBLIGATORIA)
--   cumplido      se cerró                        (sin justificación)
--   reabierto     se deshizo el cierre            (sin justificación)
--   borrado       se eliminó                      (JUSTIFICACIÓN OBLIGATORIA)
--
-- Y con eso sale solo el reporte de lo realizado: qué se hizo, quién lo hizo,
-- cuándo, y en los dos casos delicados, por qué.

CREATE TABLE IF NOT EXISTS actividad_bitacora (
  id             uuid DEFAULT gen_random_uuid() PRIMARY KEY,

  -- A qué se refiere. `task_id` se conserva aunque la actividad ya no exista.
  task_id        uuid,
  project_id     uuid,
  titulo         text,
  proyecto       text,
  responsable    text,

  accion         text NOT NULL,
  campo          text,            -- 'due_date' | 'start_date' | null
  valor_antes    text,
  valor_despues  text,
  dias_movidos   int,             -- + se alargó, − se adelantó

  justificacion  text,            -- obligatoria en 'reprogramado' y 'borrado'

  -- Cómo estaba la actividad en ese momento: es lo que convierte el apunte en
  -- un informe de lo realizado y no en una línea suelta.
  estado         text,
  avance_pct     int,
  vencia_el      date,
  semaforo       text,            -- rojo | ambar | verde | cumplido | tardio | gris

  usuario_id     uuid,
  usuario_nombre text,
  usuario_email  text,
  creado_en      timestamptz DEFAULT now()
);

-- El reporte se lee por fecha (lo último primero) y se filtra por proyecto.
CREATE INDEX IF NOT EXISTS actividad_bitacora_fecha_idx
  ON actividad_bitacora (creado_en DESC);
CREATE INDEX IF NOT EXISTS actividad_bitacora_proyecto_idx
  ON actividad_bitacora (project_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS actividad_bitacora_tarea_idx
  ON actividad_bitacora (task_id, creado_en DESC);

-- Un apunte no se corrige ni se borra: para eso está. Aquí sólo se deja dicho;
-- la aplicación entra con la clave de servicio y no expone ninguna ruta que
-- edite o elimine filas de esta tabla.
ALTER TABLE actividad_bitacora DISABLE ROW LEVEL SECURITY;
