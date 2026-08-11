-- =============================================================================
-- 020_directorio_y_cronograma.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Dos módulos nuevos:
--
--   1) DIRECTORIO (base de datos de personas y empresas)
--      * sectores: de dónde proviene el dato (firmas electrónicas, declaraciones
--        de impuestos, clases ATLAS, y cualquier otro servicio). Se crean desde
--        la interfaz, no están fijos en el código.
--      * contactos: cédula / RUC / pasaporte, nombres, apellidos, celular,
--        convencional, web, redes sociales (varias), dirección de trabajo. Si el
--        documento es un RUC se guarda además lo que devuelve el SRI: razón
--        social, estado, clase, y TODAS las actividades económicas.
--      * bitácora: quién modificó, por qué, qué campo y con qué valores. Es la
--        exigencia de trazabilidad del módulo: ninguna edición pasa sin motivo.
--
--      El número de documento es ÚNICO: la base misma rechaza el duplicado, no
--      sólo la validación de la aplicación (importar en bloque no puede colar
--      repetidos aunque dos filas del Excel lleguen a la vez).
--
--   2) CRONOGRAMA (Gantt por actividad, asistido por IA)
--      * planes y actividades con fechas, duración, avance y dependencias.
--      * las actividades pueden nacer de una tarea del To-Do (task_id) o
--        escribirse a mano; la IA propone fechas y duraciones.
--
-- Idempotente. Aplicar en el editor SQL de Supabase.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
--  1. SECTORES — origen / servicio al que pertenece cada registro
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sectors (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  slug        text NOT NULL,
  description text,
  color       text DEFAULT '#4f46e5',
  icon        text DEFAULT '📁',
  active      boolean NOT NULL DEFAULT true,
  created_by  uuid,
  created_at  timestamptz DEFAULT now()
);

-- El slug identifica al sector sin depender de mayúsculas ni tildes del nombre.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sectors_slug ON sectors (slug);

-- Sectores de arranque. ON CONFLICT DO NOTHING: si ya se creó desde la interfaz
-- con otro color o descripción, no se pisa.
INSERT INTO sectors (name, slug, description, color, icon) VALUES
  ('Firmas electrónicas',      'firmas-electronicas',
   'Clientes de emisión y renovación de firma electrónica', '#4f46e5', '🔏'),
  ('Declaraciones de impuestos','declaraciones-impuestos',
   'Contribuyentes con declaraciones mensuales o anuales',  '#0ea5e9', '🧾'),
  ('Clases (ATLAS)',           'clases-atlas',
   'Estudiantes y participantes del programa ATLAS',        '#f59e0b', '🎓'),
  ('Servicios generales',      'servicios-generales',
   'Otros servicios contratados', '#10b981', '🛠️')
ON CONFLICT (slug) DO NOTHING;


-- ─────────────────────────────────────────────────────────────────────────────
--  2. CONTACTOS — el registro maestro
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contacts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sector_id     uuid REFERENCES sectors(id) ON DELETE SET NULL,

  -- Identificación
  doc_type      text NOT NULL DEFAULT 'cedula',   -- cedula | ruc | pasaporte
  doc_number    text NOT NULL,
  doc_valid     boolean,                          -- pasó el dígito verificador
  sri_verified  boolean NOT NULL DEFAULT false,   -- confirmado contra el SRI

  -- Persona / empresa
  first_name    text,
  last_name     text,
  business_name text,                             -- razón social (RUC)
  trade_name    text,                             -- nombre comercial (RUC)

  -- Contacto
  mobile        text,
  landline      text,
  email         text,
  website       text,
  socials       jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{red, url, usuario}]

  -- Ubicación
  work_address  text,
  home_address  text,
  city          text,
  province      text,

  -- Datos que devuelve el SRI para un RUC
  ruc_state     text,                             -- ACTIVO / SUSPENDIDO ...
  ruc_class     text,                             -- clase de contribuyente
  ruc_type      text,                             -- persona natural / sociedad
  ruc_obligado_contabilidad text,
  ruc_start_date  date,
  ruc_end_date    date,
  ruc_activities  jsonb NOT NULL DEFAULT '[]'::jsonb, -- actividades económicas
  ruc_establishments jsonb NOT NULL DEFAULT '[]'::jsonb,
  ruc_raw         jsonb,                          -- respuesta cruda del SRI

  notes         text,
  tags          text,
  source        text DEFAULT 'manual',            -- manual | excel | pdf | word
  source_file   text,
  active        boolean NOT NULL DEFAULT true,

  created_by    uuid,
  created_at    timestamptz DEFAULT now(),
  updated_by    uuid,
  updated_at    timestamptz DEFAULT now()
);

-- No puede haber dos registros con el mismo documento. Es la regla que impide
-- duplicados tanto en el alta individual como en la importación en bloque.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_doc_number ON contacts (doc_number);
CREATE INDEX IF NOT EXISTS idx_contacts_sector  ON contacts (sector_id);
CREATE INDEX IF NOT EXISTS idx_contacts_names   ON contacts (last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_contacts_email   ON contacts (email);


-- ─────────────────────────────────────────────────────────────────────────────
--  3. BITÁCORA DE CAMBIOS — quién, cuándo, por qué y qué cambió
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contact_audit (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id  uuid REFERENCES contacts(id) ON DELETE CASCADE,
  doc_number  text,                    -- se conserva aunque se borre el contacto
  action      text NOT NULL,           -- create | update | delete | import
  field       text,                    -- campo modificado (uno por fila)
  field_label text,                    -- nombre legible del campo
  old_value   text,
  new_value   text,
  reason      text,                    -- MOTIVO declarado por quien edita
  user_id     uuid,
  user_name   text,
  ip          text,
  created_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contact_audit_contact
  ON contact_audit (contact_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_contact_audit_user
  ON contact_audit (user_id, created_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
--  4. CRONOGRAMA — planes y actividades (Gantt)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gantt_plans (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  description text,
  project_id  uuid,                    -- opcional: plan atado a un proyecto
  start_date  date,
  end_date    date,
  status      text DEFAULT 'active',   -- active | archived
  color       text DEFAULT '#4f46e5',
  created_by  uuid,
  created_at  timestamptz DEFAULT now(),
  updated_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gantt_plans_created_by ON gantt_plans (created_by);

CREATE TABLE IF NOT EXISTS gantt_activities (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id       uuid NOT NULL REFERENCES gantt_plans(id) ON DELETE CASCADE,
  task_id       uuid,                  -- tarea del To-Do de la que proviene
  name          text NOT NULL,
  description   text,
  responsible   text,
  start_date    date,
  end_date      date,
  duration_days int,
  progress_pct  int NOT NULL DEFAULT 0,
  status        text NOT NULL DEFAULT 'pending',
  priority      text DEFAULT 'medium',
  color         text,
  is_milestone  boolean NOT NULL DEFAULT false,
  depends_on    jsonb NOT NULL DEFAULT '[]'::jsonb,  -- ids de otras actividades
  order_index   int NOT NULL DEFAULT 0,
  ai_generated  boolean NOT NULL DEFAULT false,
  ai_notes      text,                  -- justificación de la propuesta de la IA
  created_by    uuid,
  created_at    timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gantt_activities_plan ON gantt_activities (plan_id, order_index);
-- Una tarea del To-Do no se importa dos veces al mismo plan.
CREATE UNIQUE INDEX IF NOT EXISTS idx_gantt_activities_plan_task
  ON gantt_activities (plan_id, task_id) WHERE task_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
--  5. Los módulos nuevos se otorgan por rol, igual que los existentes.
--     roles.modules es una lista separada por comas: se agregan los dos nuevos
--     a los roles de nivel administrador para que no queden invisibles.
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE roles
   SET modules = trim(both ',' from
         coalesce(nullif(modules, ''), '') ||
         CASE WHEN modules LIKE '%directorio%'  THEN '' ELSE ',directorio'  END ||
         CASE WHEN modules LIKE '%cronograma%'  THEN '' ELSE ',cronograma'  END)
 WHERE level = 'administrador';

-- ─────────────────────────────────────────────────────────────────────────────
--  6. SIN ROW LEVEL SECURITY, igual que las otras 22 tablas del sistema.
--
--  Aquí había un ENABLE ROW LEVEL SECURITY sobre las cinco tablas. Era un error:
--  activar RLS SIN crear políticas no protege, DENIEGA TODO. Y como la
--  aplicación se conecta con la clave `anon` —que respeta RLS, al contrario que
--  `service_role`—, el Directorio y el Cronograma devolvían cero filas siempre,
--  sin error visible: las pantallas salían vacías y no había forma de saber por
--  qué. Se corrigió en la migración 022.
--
--  La autorización de este sistema la hace la aplicación en Python (rol activo,
--  módulos, proyectos, cuentas de Microsoft) y la clave vive sólo en el
--  servidor. Poner RLS de verdad exigiría escribir políticas para el sistema
--  entero, no activarlo tabla por tabla y dejarlo sin ellas.
-- ─────────────────────────────────────────────────────────────────────────────
