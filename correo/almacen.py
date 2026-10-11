# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los correos, guardados en esta computadora. Un mensaje, una fila.

Lo mismo que con los quipux: un SQLite junto a lo demás, que no hay que
configurar y que funciona el día que internet no. Se crea solo la primera vez.

UN MENSAJE ES UNA FILA, LLEGUE A UNA CUENTA O A TRES
----------------------------------------------------
Las cuentas de la casa se escriben entre ellas y se copian en los mismos
hilos: el correo que manda Atlas al despacho está en el enviado de Atlas Y en
la bandeja del despacho. Es el mismo correo. Se reconoce por su `Message-ID`,
que lo pone quien lo escribe y no cambia al cruzar de buzón, y cada sitio donde
se vio se apunta aparte, en `copias`.

Cuando un mensaje no trae `Message-ID` —pasa con algunos sistemas viejos— se
le da uno hecho con el remitente, la fecha y el asunto. Dos mensajes distintos
con esas tres cosas iguales son, a efectos prácticos, el mismo.
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime

ESQUEMA = """
CREATE TABLE IF NOT EXISTS mensajes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  clave       TEXT UNIQUE NOT NULL,   -- el Message-ID, o uno hecho a falta de él
  fecha       TEXT,                   -- ISO, con zona
  de          TEXT,
  de_correo   TEXT,                   -- sólo la dirección, en minúsculas
  para        TEXT,
  asunto      TEXT,
  texto       TEXT,                   -- el cuerpo en texto plano, recortado
  adjuntos    TEXT,                   -- lista JSON de nombres
  hilo        TEXT,                   -- el Message-ID al que contesta
  masivo      INTEGER DEFAULT 0,      -- boletín, lista o publicidad
  guardado_en TEXT
);
CREATE INDEX IF NOT EXISTS msg_fecha ON mensajes (fecha);
CREATE INDEX IF NOT EXISTS msg_de    ON mensajes (de_correo);

-- Dónde se vio cada mensaje. Es lo que permite volver a él en su buzón.
CREATE TABLE IF NOT EXISTS copias (
  mensaje_id  INTEGER NOT NULL,
  cuenta      TEXT NOT NULL,
  carpeta     TEXT NOT NULL,
  uid         INTEGER NOT NULL,
  sin_leer    INTEGER DEFAULT 0,      -- como está en su buzón, a la última pasada
  marcado     INTEGER DEFAULT 0,      -- la estrella de Gmail, la bandera de Outlook
  contestado  INTEGER DEFAULT 0,      -- lo que el propio buzón marca como respondido
  PRIMARY KEY (cuenta, carpeta, uid)
);
CREATE INDEX IF NOT EXISTS copia_msg ON copias (mensaje_id);

-- Hasta dónde se leyó cada carpeta. El UID sólo vale mientras no cambie el
-- UIDVALIDITY: si el servidor lo cambia, los números son otros y se empieza
-- de nuevo por fecha.
CREATE TABLE IF NOT EXISTS marcas (
  cuenta      TEXT NOT NULL,
  carpeta     TEXT NOT NULL,
  uidvalidity INTEGER,
  ultimo_uid  INTEGER DEFAULT 0,
  PRIMARY KEY (cuenta, carpeta)
);

-- Con qué otra cosa tiene que ver un mensaje. `seguro` separa lo que se
-- comprobó (el número está en lo recogido del Quipux) de lo que sólo tiene la
-- forma de serlo: las dos cosas sirven, y quien mire tiene derecho a saber
-- cuál es cuál.
CREATE TABLE IF NOT EXISTS vinculos (
  mensaje_id  INTEGER NOT NULL,
  tipo        TEXT NOT NULL,          -- quipux | cita | plazo | plataforma
  clave       TEXT NOT NULL,          -- el número, el UID, la fecha…
  detalle     TEXT,
  seguro      INTEGER DEFAULT 0,
  UNIQUE (mensaje_id, tipo, clave)
);
CREATE INDEX IF NOT EXISTS vinc_tipo ON vinculos (tipo, clave);

-- Si cada cuenta entra. `falla_desde` es la primera pasada en que dejó de
-- entrar, y no se mueve mientras siga sin entrar: lo que importa es desde
-- cuándo falta ese buzón, no que hoy también falló.
CREATE TABLE IF NOT EXISTS cuentas (
  cuenta      TEXT PRIMARY KEY,
  ultimo_ok   TEXT,
  falla_desde TEXT,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS pasadas (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  cuando      TEXT,
  nuevos      INTEGER,
  copias      INTEGER,
  segundos    INTEGER,
  fallos      TEXT
);
"""


def carpeta_por_defecto():
    return (os.getenv('CORREO_DESTINO')
            or os.path.join(os.path.expanduser('~'), 'Documentos', 'Correo'))


def ruta_por_defecto():
    return os.path.join(carpeta_por_defecto(), 'correo.db')


@contextmanager
def abrir(ruta=None):
    ruta = ruta or ruta_por_defecto()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    con = sqlite3.connect(ruta, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(ESQUEMA)
        _columnas_nuevas(con)
        yield con
        con.commit()
    finally:
        con.close()


def _columnas_nuevas(con):
    """`CREATE TABLE IF NOT EXISTS` no añade columnas a una tabla que ya
    existe: las que llegaron después se ponen aquí, a la base de quien ya
    venía leyendo correo."""
    tiene = {r['name'] for r in con.execute('PRAGMA table_info(copias)')}
    for columna in ('marcado', 'contestado'):
        if columna not in tiene:
            con.execute(f'ALTER TABLE copias ADD COLUMN {columna} INTEGER DEFAULT 0')


def clave_de(mensaje):
    """El Message-ID, o uno hecho con lo que no cambia de un buzón a otro."""
    mid = (mensaje.get('message_id') or '').strip().strip('<>').lower()
    if mid:
        return mid
    base = '|'.join([mensaje.get('de_correo') or '', mensaje.get('fecha') or '',
                     mensaje.get('asunto') or ''])
    return 'sin-id:' + hashlib.sha1(base.encode('utf-8')).hexdigest()


# ============================================================
#  MARCAS
# ============================================================
def marca(con, cuenta, carpeta):
    fila = con.execute('SELECT uidvalidity, ultimo_uid FROM marcas '
                       'WHERE cuenta=? AND carpeta=?', (cuenta, carpeta)).fetchone()
    return (fila['uidvalidity'], fila['ultimo_uid']) if fila else (None, 0)


def apuntar_marca(con, cuenta, carpeta, uidvalidity, ultimo_uid):
    con.execute("""
        INSERT INTO marcas (cuenta, carpeta, uidvalidity, ultimo_uid) VALUES (?,?,?,?)
        ON CONFLICT(cuenta, carpeta) DO UPDATE SET
          uidvalidity=excluded.uidvalidity, ultimo_uid=excluded.ultimo_uid
    """, (cuenta, carpeta, uidvalidity, ultimo_uid))


# ============================================================
#  MENSAJES
# ============================================================
def guardar(con, mensaje, cuenta, carpeta, uid):
    """Guarda el mensaje y dónde se vio. Devuelve (id, ¿era nuevo?)."""
    clave = clave_de(mensaje)
    fila = con.execute('SELECT id FROM mensajes WHERE clave=?', (clave,)).fetchone()
    nuevo = fila is None
    if nuevo:
        cur = con.execute("""
            INSERT INTO mensajes (clave, fecha, de, de_correo, para, asunto, texto,
                                  adjuntos, hilo, masivo, guardado_en)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (clave, mensaje.get('fecha'), mensaje.get('de'), mensaje.get('de_correo'),
              mensaje.get('para'), mensaje.get('asunto'), mensaje.get('texto'),
              json.dumps(mensaje.get('adjuntos') or [], ensure_ascii=False),
              mensaje.get('hilo'), 1 if mensaje.get('masivo') else 0,
              datetime.now().isoformat(timespec='seconds')))
        mensaje_id = cur.lastrowid
    else:
        mensaje_id = fila['id']
    con.execute("""
        INSERT INTO copias (mensaje_id, cuenta, carpeta, uid, sin_leer) VALUES (?,?,?,?,?)
        ON CONFLICT(cuenta, carpeta, uid) DO UPDATE SET
          mensaje_id=excluded.mensaje_id, sin_leer=excluded.sin_leer
    """, (mensaje_id, cuenta, carpeta, int(uid), 1 if mensaje.get('sin_leer') else 0))
    return mensaje_id, nuevo


def uids_de(con, cuenta, carpeta):
    return {r['uid'] for r in con.execute(
        'SELECT uid FROM copias WHERE cuenta=? AND carpeta=?', (cuenta, carpeta))}


def apuntar_banderas(con, cuenta, carpeta, sin_leer, marcados, contestados):
    """Cómo está cada copia de esa carpeta, según lo que acaba de decir el
    servidor. Lo leído en el teléfono o desmarcado en el navegador deja de
    figurar como pendiente en la pasada siguiente."""
    con.executemany(
        'UPDATE copias SET sin_leer=?, marcado=?, contestado=? '
        'WHERE cuenta=? AND carpeta=? AND uid=?',
        [(int(u in sin_leer), int(u in marcados), int(u in contestados), cuenta, carpeta, u)
         for u in uids_de(con, cuenta, carpeta)])


def guardar_vinculos(con, mensaje_id, vinculos, tipos=None):
    """Los vínculos de un mensaje, en sustitución de los que tuviera: se
    vuelven a calcular cuando cambia lo que se sabe del Quipux.

    Con `tipos`, sólo se sustituyen esos. La invitación de calendario no se
    guarda, así que el vínculo `cita` no se puede recalcular desde aquí y se
    deja como quedó al traer el mensaje."""
    if tipos:
        marcas = ','.join('?' * len(tipos))
        con.execute(f'DELETE FROM vinculos WHERE mensaje_id=? AND tipo IN ({marcas})',
                    (mensaje_id, *tipos))
        vinculos = [v for v in vinculos if v['tipo'] in tipos]
    else:
        con.execute('DELETE FROM vinculos WHERE mensaje_id=?', (mensaje_id,))
    con.executemany("""
        INSERT OR IGNORE INTO vinculos (mensaje_id, tipo, clave, detalle, seguro)
        VALUES (?,?,?,?,?)
    """, [(mensaje_id, v['tipo'], v['clave'], v.get('detalle'),
           1 if v.get('seguro') else 0) for v in vinculos])


def apuntar_cuenta(con, cuenta, error=None):
    """Si la cuenta entró en esta pasada (`error` vacío) o no, y por qué."""
    ahora = datetime.now().isoformat(timespec='seconds')
    if not error:
        con.execute("""
            INSERT INTO cuentas (cuenta, ultimo_ok, falla_desde, error) VALUES (?,?,NULL,NULL)
            ON CONFLICT(cuenta) DO UPDATE SET
              ultimo_ok=excluded.ultimo_ok, falla_desde=NULL, error=NULL
        """, (cuenta, ahora))
    else:
        con.execute("""
            INSERT INTO cuentas (cuenta, ultimo_ok, falla_desde, error) VALUES (?,NULL,?,?)
            ON CONFLICT(cuenta) DO UPDATE SET
              falla_desde=COALESCE(cuentas.falla_desde, excluded.falla_desde),
              error=excluded.error
        """, (cuenta, ahora, str(error)[:300]))


def estado_cuentas(ruta=None):
    with abrir(ruta) as con:
        cuentas = {r['cuenta']: dict(r) for r in con.execute('SELECT * FROM cuentas')}
        ultima = con.execute('SELECT cuando FROM pasadas ORDER BY id DESC LIMIT 1').fetchone()
    return cuentas, (ultima[0] if ultima else None)


def apuntar_pasada(con, resumen):
    con.execute('INSERT INTO pasadas (cuando, nuevos, copias, segundos, fallos) '
                'VALUES (?,?,?,?,?)',
                (datetime.now().isoformat(timespec='seconds'), resumen.get('nuevos', 0),
                 resumen.get('copias', 0), resumen.get('segundos', 0),
                 json.dumps(resumen.get('fallos') or [], ensure_ascii=False)))


# ============================================================
#  CONSULTAS
# ============================================================
def _con_cuentas(con, filas):
    """Cada mensaje con las cuentas donde está y sus vínculos."""
    salida = []
    for f in filas:
        d = dict(f)
        d['adjuntos'] = json.loads(d.get('adjuntos') or '[]')
        d['cuentas'] = sorted({r['cuenta'] for r in con.execute(
            'SELECT cuenta FROM copias WHERE mensaje_id=?', (d['id'],))})
        d['vinculos'] = [dict(v) for v in con.execute(
            'SELECT tipo, clave, detalle, seguro FROM vinculos WHERE mensaje_id=? '
            'ORDER BY tipo, clave', (d['id'],))]
        salida.append(d)
    return salida


def buscar(texto='', cuenta='', tope=30, ruta=None):
    """Lo más reciente primero. `texto` busca en asunto, remitente y cuerpo."""
    sql = 'SELECT m.* FROM mensajes m'
    cond, args = [], []
    if cuenta:
        sql += ' JOIN copias c ON c.mensaje_id = m.id'
        cond.append('c.cuenta = ?')
        args.append(cuenta)
    for palabra in (texto or '').split():
        cond.append('(m.asunto LIKE ? OR m.de LIKE ? OR m.texto LIKE ?)')
        args += [f'%{palabra}%'] * 3
    if cond:
        sql += ' WHERE ' + ' AND '.join(cond)
    sql += ' GROUP BY m.id ORDER BY m.fecha DESC LIMIT ?'
    args.append(int(tope))
    with abrir(ruta) as con:
        return _con_cuentas(con, con.execute(sql, args).fetchall())


def mensaje(mensaje_id, ruta=None):
    with abrir(ruta) as con:
        filas = con.execute('SELECT * FROM mensajes WHERE id=?', (int(mensaje_id),)).fetchall()
        lista = _con_cuentas(con, filas)
        if not lista:
            return None
        lista[0]['donde'] = [dict(r) for r in con.execute(
            'SELECT cuenta, carpeta, uid FROM copias WHERE mensaje_id=?', (int(mensaje_id),))]
        return lista[0]


def vinculados(tipo, clave=None, desde=None, tope=200, ruta=None):
    """Los mensajes con un vínculo de ese tipo; de esa clave, si se da."""
    sql = ('SELECT DISTINCT m.* FROM mensajes m JOIN vinculos v ON v.mensaje_id = m.id '
           'WHERE v.tipo = ?')
    args = [tipo]
    if clave:
        sql += ' AND upper(v.clave) = upper(?)'
        args.append(clave)
    if desde:
        sql += ' AND v.clave >= ?'
        args.append(desde)
    sql += ' ORDER BY m.fecha DESC LIMIT ?'
    args.append(int(tope))
    with abrir(ruta) as con:
        return _con_cuentas(con, con.execute(sql, args).fetchall())


# Los avisos que manda una máquina: «noreply», notificaciones, recomendaciones,
# novedades. No traen la cabecera de boletín —por eso `masivo` no los aparta— y
# no esperan nada de nadie. Se miran en la parte de la dirección antes de la
# arroba: `googleplay-noreply@`, `notifications@rambox.app`,
# `recommendations@discover.pinterest.com`, `updates@email.grok.com`.
RE_AUTOMATICO = re.compile(
    r'(no-?reply|no_reply|do-?not-?reply|notification|notificacion|recommendation'
    r'|updates?$|news|marketing|promo|mailer-daemon|postmaster|gemini-notes)', re.I)


def automatico(de_correo):
    return bool(RE_AUTOMATICO.search((de_correo or '').split('@')[0]))


def con_estado(desde, propias=(), ruta=None):
    """Lo que está por atender: lo marcado, sea de cuando sea, y lo que
    llegó sin leer desde `desde`. Nunca boletines, y nunca lo que escribió una
    cuenta de la casa: eso no espera a nadie.

    Cada mensaje lleva en qué cuentas está marcado y en cuáles sin leer, y si
    ya se contestó: porque el buzón lo dice o porque en lo guardado hay una
    respuesta de una cuenta de la casa a ese mensaje.

    Lo sin leer de un remitente automático (`automatico`) no cuenta. Lo
    marcado sí, venga de quien venga: si alguien lo marcó, es que le importa."""
    propias = [p.lower() for p in propias]
    huecos = ','.join('?' * len(propias)) or "''"
    sql = f"""
        SELECT m.*,
          (SELECT group_concat(DISTINCT c.cuenta) FROM copias c
             WHERE c.mensaje_id = m.id AND c.marcado = 1) AS marcado_en,
          (SELECT group_concat(DISTINCT c.cuenta) FROM copias c
             WHERE c.mensaje_id = m.id AND c.sin_leer = 1) AS sin_leer_en,
          (EXISTS (SELECT 1 FROM copias c WHERE c.mensaje_id = m.id AND c.contestado = 1)
           OR EXISTS (SELECT 1 FROM mensajes r WHERE r.hilo = m.clave
                        AND r.de_correo IN ({huecos}))) AS contestado
        FROM mensajes m
        WHERE m.masivo = 0
          AND m.de_correo NOT IN ({huecos})
          AND (EXISTS (SELECT 1 FROM copias c WHERE c.mensaje_id = m.id AND c.marcado = 1)
               OR (m.fecha >= ? AND EXISTS (SELECT 1 FROM copias c
                     WHERE c.mensaje_id = m.id AND c.sin_leer = 1)))
        ORDER BY m.fecha DESC"""
    with abrir(ruta) as con:
        filas = con.execute(sql, [*propias, *propias, desde]).fetchall()
        salida = _con_cuentas(con, filas)
    for d in salida:
        d['marcado_en'] = sorted(filter(None, (d.get('marcado_en') or '').split(',')))
        d['sin_leer_en'] = sorted(filter(None, (d.get('sin_leer_en') or '').split(',')))
        d['contestado'] = bool(d.get('contestado'))
    return [d for d in salida if d['marcado_en'] or not automatico(d.get('de_correo'))]


def todos_para_relacionar(ruta=None):
    """Lo mínimo de cada mensaje para volver a calcular sus vínculos."""
    with abrir(ruta) as con:
        return [dict(f) for f in con.execute(
            'SELECT id, fecha, de_correo, asunto, texto, masivo FROM mensajes')]


def resumen(ruta=None):
    with abrir(ruta) as con:
        total = con.execute('SELECT count(*) FROM mensajes').fetchone()[0]
        por_cuenta = {r['cuenta']: r['n'] for r in con.execute(
            'SELECT cuenta, count(DISTINCT mensaje_id) n FROM copias GROUP BY cuenta')}
        por_tipo = {r['tipo']: r['n'] for r in con.execute(
            'SELECT tipo, count(DISTINCT mensaje_id) n FROM vinculos GROUP BY tipo')}
        ultima = con.execute('SELECT cuando FROM pasadas ORDER BY id DESC LIMIT 1').fetchone()
        return {'total': total, 'por_cuenta': por_cuenta, 'por_tipo': por_tipo,
                'ultima_pasada': ultima[0] if ultima else None}
