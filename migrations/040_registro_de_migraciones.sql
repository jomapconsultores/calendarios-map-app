-- ============================================================
--  El registro de lo aplicado, que nunca llegó a existir
--
--  Aplicar con:  python tools/aplicar_migracion.py migrations/040_*.sql
-- ============================================================
--
-- `tools/aplicar_migracion.py` lleva desde la 035 diciendo que hay una tabla
-- `schema_migrations` donde se anota cada migración aplicada, y que `--estado`
-- dice qué falta. Nada de eso funcionaba: la herramienta manda aplicar
-- `migrations/035_registro_de_migraciones.sql`, y ese archivo NUNCA SE ESCRIBIÓ.
-- El 035 del repo es otra cosa (los avisos de vencimientos). Así que la tabla no
-- nacía, `--estado` contestaba «aplica primero...» señalando a un archivo que no
-- existe, y no había manera de saber qué estaba puesto y qué no.
--
-- Eso es lo que hizo falta para que nadie supiera durante semanas si la 032 y la
-- 033 estaban aplicadas. Se discutió sobre ello, se dieron por pendientes, y
-- estaban puestas las dos.
--
-- Esto crea la tabla y siembra lo que ya está. No a ciegas: el 2026-09-12 se
-- comprobó una por una, buscando en la base el objeto que cada migración crea
-- —su tabla, su columna, su índice—. Las 36 que se pueden comprobar así,
-- estaban. La consulta que se usó queda al final, para poder repetirla.


CREATE TABLE IF NOT EXISTS schema_migrations (
  version     text PRIMARY KEY,
  aplicada_en timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE schema_migrations DISABLE ROW LEVEL SECURITY;


-- ------------------------------------------------------------
--  Lo que ya estaba
-- ------------------------------------------------------------
-- Comprobado en la base, salvo donde se dice. `ON CONFLICT DO NOTHING` para que
-- esto se pueda volver a ejecutar sin consecuencias.
INSERT INTO schema_migrations (version) VALUES
  ('001'), ('002'), ('003'), ('004'), ('006'), ('007'), ('008'), ('009'),
  ('010'), ('011'), ('012'), ('013'), ('014'), ('015'), ('016'), ('017'),
  ('018'), ('019'), ('020'), ('021'), ('023'), ('024'), ('026'), ('027'),
  ('028'), ('029'), ('030'), ('031'), ('032'), ('033'), ('034'), ('035'),
  ('036'), ('037'), ('038'), ('039')
ON CONFLICT (version) DO NOTHING;


-- ------------------------------------------------------------
--  La 022 y la 025: anotadas como superadas, Y A PROPÓSITO
-- ------------------------------------------------------------
-- Son las dos únicas que la comprobación dio por NO aplicadas, y las dos hacen
-- lo mismo: `DISABLE ROW LEVEL SECURITY` sobre un montón de tablas. Hoy la base
-- tiene RLS ACTIVO en 38 tablas, todas sin una sola política.
--
-- Eso no está roto: la aplicación entra con la clave de servicio, que se salta
-- RLS, y por eso todo funciona. Lo que no se puede hacer es ejecutarlas ahora
-- «para ponerse al día», porque apagar RLS deja esas 38 tablas legibles y
-- escribibles por cualquiera que tenga la clave anónima — y la clave anónima
-- está pensada para repartirse, va al navegador.
--
-- O sea: el estado de hoy es MÁS seguro que el que esas dos migraciones
-- dejarían. Se anotan aquí para que nadie las aplique por error con
-- `--pendientes`. Si alguna vez hace falta abrir una tabla concreta, se hace con
-- una política para esa tabla, no apagando RLS en cuarenta.
INSERT INTO schema_migrations (version) VALUES ('022'), ('025')
ON CONFLICT (version) DO NOTHING;


-- ------------------------------------------------------------
--  Cómo se comprobó, por si hay que repetirlo
-- ------------------------------------------------------------
--   SELECT '033' AS ver, to_regclass('public.ms_tokens') IS NOT NULL AS esta
--   UNION ALL SELECT '030', to_regclass('public.vencimiento_avisos') IS NOT NULL
--   UNION ALL SELECT '037', to_regclass('public.ms_autorizaciones') IS NOT NULL
--   UNION ALL SELECT '036', EXISTS(SELECT 1 FROM information_schema.columns
--                                   WHERE table_name='ms_tokens'
--                                     AND column_name='token_expiry')
--   UNION ALL SELECT '035', EXISTS(SELECT 1 FROM pg_indexes
--                                   WHERE indexname='vencimiento_avisos_unico');
--
-- Y para ver el RLS de un vistazo:
--
--   SELECT relname, relrowsecurity,
--          (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname)
--     FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity
--    ORDER BY 1;
