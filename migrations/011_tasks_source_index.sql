-- Arregla la duplicación de tareas sincronizadas desde Microsoft To-Do.
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
--
-- Causa: la tabla `tasks` no tenía índice en source/source_id, el prefetch de
-- deduplicación de la sincronización hacía full-scan y superaba el statement
-- timeout; al fallar, se re-insertaban todas las tareas de To-Do en cada corrida.
--
-- Este script: (1) elimina los duplicados dejando la fila más reciente por
-- source_id, (2) crea índice en source para acelerar el prefetch, y (3) crea un
-- índice ÚNICO en source_id que impide físicamente volver a duplicar una tarea.

SET statement_timeout = '300s';

-- 1) Eliminar duplicados por source_id, conservando la fila más reciente.
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY source_id
           ORDER BY created_at DESC NULLS LAST, id DESC
         ) AS rn
  FROM tasks
  WHERE source_id IS NOT NULL
)
DELETE FROM tasks
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- 2) Índice para que el prefetch de sincronización sea rápido (evita full-scan).
CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks (source);

-- 3) Unicidad de source_id: impide re-importar la misma tarea de To-Do.
--    (Las tareas locales tienen source_id NULL y no se ven afectadas: en un índice
--     único, múltiples NULL están permitidos.)
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_source_id ON tasks (source_id);
