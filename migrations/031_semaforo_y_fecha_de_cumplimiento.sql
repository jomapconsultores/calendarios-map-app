-- ============================================================
--  Semaforización: cuándo se cerró de verdad cada actividad
--  Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- ============================================================
--
-- El cronograma tiene que servir para analizar QUÉ SE CUMPLIÓ Y QUÉ NO, y para
-- eso no basta con saber si algo está marcado como hecho: hace falta saber si
-- se cerró antes o después de su plazo. Sin esa fecha, marcar una actividad
-- como terminada tres semanas tarde se pinta igual de verde que entregarla a
-- tiempo, y el semáforo deja de decir nada — que es peor que no tenerlo,
-- porque tranquiliza.
--
-- `tasks` ya tenía `completed_date`. `gantt_activities` no, y era la mitad que
-- faltaba para que las dos vistas de la planificación cuenten lo mismo.
--
-- La aplicación funciona SIN esta migración: si la columna no existe, el
-- guardado se reintenta sin ella (ver _guardar_actividad en app/cronograma.py)
-- y lo único que se pierde es distinguir «cumplida» de «cumplida con retraso».

ALTER TABLE gantt_activities
  ADD COLUMN IF NOT EXISTS completed_date date;

-- Lo que ya estaba cerrado antes de esta migración no tiene fecha de cierre y
-- nunca la tendrá: nadie la apuntó. Se le da la de su propio plazo, que es la
-- lectura más prudente —cumplida, sin acusar de un retraso que no consta—.
-- Las que se cierren de aquí en adelante llevan la fecha real.
UPDATE gantt_activities
   SET completed_date = end_date
 WHERE status = 'done'
   AND completed_date IS NULL
   AND end_date IS NOT NULL;

-- El semáforo pregunta siempre lo mismo: qué vence antes de hoy y sigue
-- abierto. Con los planes grandes esa consulta se hace en cada pintado.
CREATE INDEX IF NOT EXISTS gantt_activities_end_date_idx
  ON gantt_activities (end_date) WHERE status <> 'done';
