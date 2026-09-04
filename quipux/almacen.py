# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los quipux se guardan en el propio servidor. Sin Supabase de por medio.

Todo lo que este módulo necesita guardar es la lista de documentos de UNA
persona: unos cientos de filas que sólo lee esta aplicación. Para eso, una base
en la nube es una dependencia que no paga lo que cuesta — hay que crear tablas,
aplicar migraciones, tener la clave correcta y que el servicio esté en pie. Y
cuando ese servicio no está, no se ve nada, aunque los documentos estén
descargados a dos centímetros del programa que los quiere enseñar. Ya pasó.

SQLite es un archivo. Vive junto a los documentos descargados, se copia con
ellos, no hay que configurar nada y funciona igual el día que internet falla.
Si algún día esto tuviera que verse desde varios sitios a la vez, entonces sí
haría falta otra cosa; hoy no.

El archivo se crea solo la primera vez. No hay migraciones que aplicar ni
pantallas que avisen de que falta un paso.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

CAMPOS = (
    'id', 'numero', 'asunto', 'remitente', 'tipo', 'fecha_doc', 'tramite',
    'referencia', 'categoria', 'area', 'bandeja', 'estado', 'carpeta',
    'enlace', 'n_adjuntos', 'plazo_fecha', 'plazo_origen', 'plazo_seguro',
    'visto', 'actualizado',
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS documentos (
  id            TEXT PRIMARY KEY,   -- el identificador del documento en CuencaDOC
  numero        TEXT,
  asunto        TEXT,
  remitente     TEXT,
  tipo          TEXT,
  fecha_doc     TEXT,
  tramite       TEXT,
  referencia    TEXT,
  categoria     TEXT,
  area          TEXT,
  bandeja       TEXT,
  estado        TEXT DEFAULT 'abierto',
  carpeta       TEXT,
  enlace        TEXT,
  n_adjuntos    INTEGER DEFAULT 0,
  plazo_fecha   TEXT,
  plazo_origen  TEXT,
  plazo_seguro  INTEGER DEFAULT 0,
  visto         INTEGER DEFAULT 0,
  actualizado   TEXT
);
CREATE INDEX IF NOT EXISTS doc_plazo  ON documentos (plazo_fecha);
CREATE INDEX IF NOT EXISTS doc_area   ON documentos (area, bandeja);

-- Lo que hay que hacer, con su fecha. Vive aquí y no en el cronograma de la
-- plataforma a propósito: esto es lo que el Municipio dice que hay que hacer,
-- no lo que el despacho se organizó para hacer. Que se pueda llevar al
-- cronograma es otra cosa, y voluntaria.
CREATE TABLE IF NOT EXISTS tareas (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  documento_id  TEXT,
  titulo        TEXT NOT NULL,
  detalle       TEXT,
  vence         TEXT,               -- AAAA-MM-DD
  origen        TEXT,               -- de dónde salió la fecha
  seguro        INTEGER DEFAULT 0,  -- ¿lo dijo el sistema o se dedujo del texto?
  area          TEXT,
  estado        TEXT DEFAULT 'pendiente',   -- pendiente | hecha | descartada
  hecha_en      TEXT,
  creada_en     TEXT,
  UNIQUE(documento_id)
);
CREATE INDEX IF NOT EXISTS tarea_vence ON tareas (vence, estado);

-- Una línea por pasada: cuándo se trajo, qué entró y qué falló.
CREATE TABLE IF NOT EXISTS pasadas (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  cuando        TEXT,
  documentos    INTEGER,
  nuevos        INTEGER,
  adjuntos      INTEGER,
  segundos      INTEGER,
  fallos        TEXT
);
"""


def ruta_por_defecto():
    """Junto a los documentos descargados: lo que va junto, se copia junto."""
    base = (os.getenv('QUIPUX_DESTINO')
            or os.path.join(os.path.expanduser('~'), 'Documentos', 'Quipux'))
    return os.path.join(base, 'quipux.db')


@contextmanager
def abrir(ruta=None):
    ruta = ruta or ruta_por_defecto()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    con = sqlite3.connect(ruta, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(ESQUEMA)
        yield con
        con.commit()
    finally:
        con.close()


# ============================================================
#  DOCUMENTOS
# ============================================================
def guardar(documentos, ruta=None):
    """Inserta o actualiza. Un documento visto otra vez es el mismo documento:
    se pone al día, no se duplica."""
    if not documentos:
        return 0
    ahora = datetime.now().isoformat(timespec='seconds')
    filas = []
    for d in documentos:
        if not d.get('id'):
            continue
        plazo = d.get('plazo') or {}
        filas.append((
            str(d['id']), d.get('numero'), d.get('asunto'), d.get('de'),
            d.get('tipo'), (d.get('fecha_doc') or '')[:10] or None,
            d.get('tramite'), d.get('referencia'), d.get('categoria'),
            d.get('area'), d.get('bandeja'), d.get('estado') or 'abierto',
            d.get('carpeta'), d.get('enlace'), int(d.get('n_adjuntos') or 0),
            plazo.get('fecha') or None, plazo.get('origen') or None,
            1 if plazo.get('seguro') else 0, ahora,
        ))
    with abrir(ruta) as con:
        con.executemany("""
            INSERT INTO documentos
              (id,numero,asunto,remitente,tipo,fecha_doc,tramite,referencia,
               categoria,area,bandeja,estado,carpeta,enlace,n_adjuntos,
               plazo_fecha,plazo_origen,plazo_seguro,actualizado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              numero=excluded.numero, asunto=excluded.asunto,
              remitente=excluded.remitente, tipo=excluded.tipo,
              fecha_doc=excluded.fecha_doc, tramite=excluded.tramite,
              referencia=excluded.referencia, categoria=excluded.categoria,
              area=excluded.area, bandeja=excluded.bandeja,
              estado=excluded.estado, carpeta=excluded.carpeta,
              enlace=excluded.enlace, n_adjuntos=excluded.n_adjuntos,
              plazo_fecha=excluded.plazo_fecha, plazo_origen=excluded.plazo_origen,
              plazo_seguro=excluded.plazo_seguro, actualizado=excluded.actualizado
        """, filas)
    return len(filas)


def documentos(ruta=None, ver='pendientes', area='', bandeja='', busca='', tope=800):
    """Lo guardado, filtrado y en el orden en que hay que trabajarlo: lo que
    vence antes, primero; lo que no tiene plazo, al final."""
    hoy = date.today().isoformat()
    donde, params = [], []
    if ver == 'vencidos':
        donde.append("plazo_fecha IS NOT NULL AND plazo_fecha < ? AND estado <> 'cerrado'")
        params.append(hoy)
    elif ver == 'con_plazo':
        donde.append('plazo_fecha IS NOT NULL')
    elif ver == 'pendientes':
        donde.append("estado <> 'cerrado'")
    if area:
        donde.append('area = ?'); params.append(area)
    if bandeja:
        donde.append('bandeja = ?'); params.append(bandeja)
    if busca:
        donde.append('(asunto LIKE ? OR numero LIKE ? OR remitente LIKE ? '
                     'OR tramite LIKE ? OR referencia LIKE ?)')
        params += [f'%{busca}%'] * 5
    sql = 'SELECT * FROM documentos'
    if donde:
        sql += ' WHERE ' + ' AND '.join(donde)
    sql += " ORDER BY COALESCE(plazo_fecha,'9999-99-99'), fecha_doc DESC LIMIT ?"
    params.append(tope)
    with abrir(ruta) as con:
        return [dict(f) for f in con.execute(sql, params)]


def resumen(ruta=None):
    hoy = date.today().isoformat()
    with abrir(ruta) as con:
        def uno(sql, *p):
            fila = con.execute(sql, p).fetchone()
            return fila[0] if fila else 0
        r = {
            'total': uno('SELECT COUNT(*) FROM documentos'),
            'abiertos': uno("SELECT COUNT(*) FROM documentos WHERE estado <> 'cerrado'"),
            'con_plazo': uno("SELECT COUNT(*) FROM documentos "
                             "WHERE plazo_fecha IS NOT NULL AND estado <> 'cerrado'"),
            'vencidos': uno("SELECT COUNT(*) FROM documentos WHERE plazo_fecha < ? "
                            "AND plazo_fecha IS NOT NULL AND estado <> 'cerrado'", hoy),
            'deducidos': uno("SELECT COUNT(*) FROM documentos WHERE plazo_fecha IS NOT NULL "
                             "AND plazo_seguro = 0 AND estado <> 'cerrado'"),
            'adjuntos': uno('SELECT COALESCE(SUM(n_adjuntos),0) FROM documentos'),
            'areas': [f[0] for f in con.execute(
                'SELECT DISTINCT area FROM documentos WHERE area IS NOT NULL ORDER BY area')],
            'bandejas': [f[0] for f in con.execute(
                'SELECT DISTINCT bandeja FROM documentos WHERE bandeja IS NOT NULL ORDER BY bandeja')],
        }
        fila = con.execute('SELECT cuando, documentos, fallos FROM pasadas '
                           'ORDER BY id DESC LIMIT 1').fetchone()
        r['ultima_pasada'] = fila['cuando'] if fila else None
        r['ultimos_fallos'] = (fila['fallos'] or '') if fila else ''
    r['al_dia'] = bool(r['ultima_pasada'] and r['ultima_pasada'][:10] == hoy)
    return r


# ============================================================
#  TAREAS
# ============================================================
def crear_tareas(documentos_con_plazo, ruta=None):
    """Convierte en tarea cada documento con plazo. Devuelve cuántas se crearon.

    No se pisa lo que una persona ya tocó: una tarea marcada como hecha o
    descartada se queda como está. Lo que el Municipio dice manda sobre la
    fecha; sobre si ya se cumplió, manda quien la cumplió."""
    ahora = datetime.now().isoformat(timespec='seconds')
    creadas = actualizadas = 0
    with abrir(ruta) as con:
        for d in documentos_con_plazo:
            plazo = d.get('plazo') or {}
            if not plazo.get('fecha') or not d.get('id'):
                continue
            fila = con.execute('SELECT id, vence, estado FROM tareas '
                               'WHERE documento_id = ?', (str(d['id']),)).fetchone()
            titulo = f"[{d.get('numero','s/n')}] {(d.get('asunto') or '')[:170]}"
            if fila is None:
                con.execute(
                    'INSERT INTO tareas (documento_id,titulo,detalle,vence,origen,'
                    'seguro,area,estado,creada_en) VALUES (?,?,?,?,?,?,?,?,?)',
                    (str(d['id']), titulo, d.get('de') or '', plazo['fecha'],
                     plazo.get('origen') or '', 1 if plazo.get('seguro') else 0,
                     d.get('area') or '', 'pendiente', ahora))
                creadas += 1
            elif fila['estado'] == 'pendiente' and (fila['vence'] or '') != plazo['fecha']:
                con.execute('UPDATE tareas SET vence = ?, titulo = ? WHERE id = ?',
                            (plazo['fecha'], titulo, fila['id']))
                actualizadas += 1
    return creadas, actualizadas


def tareas(ruta=None, estado='pendiente', tope=400):
    sql = 'SELECT * FROM tareas'
    params = []
    if estado and estado != 'todas':
        sql += ' WHERE estado = ?'; params.append(estado)
    sql += " ORDER BY COALESCE(vence,'9999-99-99') LIMIT ?"
    params.append(tope)
    with abrir(ruta) as con:
        return [dict(f) for f in con.execute(sql, params)]


def marcar_tarea(tarea_id, estado, ruta=None):
    ahora = datetime.now().isoformat(timespec='seconds') if estado == 'hecha' else None
    with abrir(ruta) as con:
        con.execute('UPDATE tareas SET estado = ?, hecha_en = ? WHERE id = ?',
                    (estado, ahora, tarea_id))
    return True


def apuntar_pasada(resumen_pasada, ruta=None):
    with abrir(ruta) as con:
        con.execute('INSERT INTO pasadas (cuando,documentos,nuevos,adjuntos,'
                    'segundos,fallos) VALUES (?,?,?,?,?,?)',
                    (datetime.now().isoformat(timespec='seconds'),
                     resumen_pasada.get('documentos', 0),
                     resumen_pasada.get('nuevos', 0),
                     resumen_pasada.get('adjuntos', 0),
                     resumen_pasada.get('segundos', 0),
                     ' | '.join(resumen_pasada.get('fallos', [])[:20])))
