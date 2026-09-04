-- ============================================================
--  Los quipux recogidos, donde los pueda ver el servidor
--  Ejecutar en: (el proyecto de Supabase vigente) → SQL Editor
-- ============================================================
--
-- La recolección corre en la computadora de la persona: entrar a CuencaDOC
-- necesita su credencial del llavero de Windows y, cuando el sistema lo pide,
-- que ella escriba el texto de la imagen. Eso no se puede mover a un servidor,
-- y está bien que no se pueda.
--
-- Pero mirar lo recogido sí tiene que poder hacerse desde cualquier parte —el
-- teléfono, la oficina, la plataforma desplegada—, y ahí el archivo que queda
-- en el disco de una computadora no sirve de nada. Esta tabla es el puente: la
-- computadora recoge y sube; el servidor sólo enseña.
--
-- Se guarda una FOTO de lo que dice CuencaDOC, no una copia de trabajo. Lo que
-- se hace con cada documento —cumplirlo, reprogramarlo, cerrarlo— vive en el
-- cronograma, con sus reglas y su bitácora. Mezclar las dos cosas convertiría
-- esta tabla en un segundo sitio donde llevar los plazos, y dos sitios para lo
-- mismo acaban siempre en que ninguno de los dos es el bueno.

CREATE TABLE IF NOT EXISTS quipux_documentos (
  -- El identificador interno del documento en CuencaDOC (veinte dígitos). Es
  -- suyo, no nuestro: por eso es la clave, y por eso una segunda pasada
  -- actualiza la fila en vez de crear otra.
  id             text PRIMARY KEY,

  numero         text,          -- DGPG-2050-2026: con esto se cita y se busca
  asunto         text,
  remitente      text,          -- «de» en la bandeja
  tipo           text,          -- Oficio | Memorando | Circular…
  fecha_doc      text,          -- como la da el sistema, sin reinterpretar
  tramite        text,
  referencia     text,
  categoria      text,

  area           text,          -- Observatorio | Planificación
  bandeja         text,         -- Recibidos, Reasignados, Archivados…
  estado         text,          -- abierto | cerrado

  -- Dónde quedaron los archivos EN LA COMPUTADORA. Desde el servidor no se
  -- pueden abrir, y aun así se guarda: es la única forma de que quien esté
  -- delante de esa computadora sepa a qué carpeta ir.
  carpeta        text,
  enlace         text,          -- de vuelta al documento en CuencaDOC
  n_adjuntos     integer DEFAULT 0,

  -- El plazo, y de dónde salió. `plazo_seguro` distingue lo que dijo el
  -- sistema de lo que se dedujo leyendo el texto del documento. Las dos cosas
  -- sirven; mezclarlas, no: una fecha sacada de una frase puede estar mal, y
  -- quien la mire tiene derecho a saber cuál es cuál.
  plazo_fecha    text,
  plazo_origen   text,
  plazo_seguro   boolean DEFAULT false,

  actualizado    timestamptz DEFAULT now()
);

-- La consulta de la pantalla: lo que vence antes, primero.
CREATE INDEX IF NOT EXISTS quipux_documentos_plazo_idx
  ON quipux_documentos (plazo_fecha) WHERE estado <> 'cerrado';

-- El contador del menú y los filtros por área.
CREATE INDEX IF NOT EXISTS quipux_documentos_area_idx
  ON quipux_documentos (area, bandeja);

-- Igual que el resto del sistema (ver 022): el servidor entra con la clave de
-- servicio y filtra por permisos en la aplicación.
ALTER TABLE quipux_documentos DISABLE ROW LEVEL SECURITY;
