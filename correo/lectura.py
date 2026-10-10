# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Lo que se trae de cada carpeta, y cómo se lee un mensaje.

Cada carpeta se lee desde donde se quedó la vez anterior: el servidor numera
los mensajes (UID) en orden de llegada, así que basta pedir los que van
después del último que se vio. La primera vez no hay último, y se empieza por
fecha: `DIAS_PRIMERA_VEZ` hacia atrás.

Del mensaje se trae sólo el principio —`TROZO` bytes—. Ahí están las
cabeceras, el texto y la invitación de calendario, que es lo que hace falta
para relacionarlo. Los adjuntos grandes se quedan en su buzón: de ellos se
apunta el nombre, y quien quiera el archivo lo tiene a un clic en su correo.
Traerlos multiplicaría por cien lo que se baja para no leerlos nunca.
"""
import email
import email.utils
import html as _html
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

from . import almacen, conector

DIAS_PRIMERA_VEZ = 30
TROZO = 250_000
LOTE = 25
LARGO_TEXTO = 20_000

# Por carpeta y pasada. Un buzón de Gmail con años de correo trae decenas de
# miles de mensajes en «Todos»; la primera pasada se queda con los más nuevos y
# las siguientes ya sólo traen lo que llegue.
TOPE_POR_CARPETA = 3000

# Lo marcado se trae aunque sea anterior a la primera pasada: una estrella de
# hace medio año sigue siendo algo pendiente. Con tope, por si alguien marca
# por costumbre todo lo que lee.
TOPE_MARCADOS = 300


# ============================================================
#  UN MENSAJE
# ============================================================
def _texto_cabecera(valor):
    if not valor:
        return ''
    try:
        return str(make_header(decode_header(valor))).strip()
    except Exception:
        return str(valor).strip()


def _fecha(valor):
    try:
        d = email.utils.parsedate_to_datetime(valor)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.isoformat()
    except Exception:
        return None


def _html_a_texto(crudo):
    crudo = re.sub(r'(?is)<(script|style).*?</\1>', ' ', crudo)
    crudo = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>', '\n', crudo)
    crudo = re.sub(r'(?s)<[^>]+>', ' ', crudo)
    crudo = _html.unescape(crudo)
    crudo = re.sub(r'[ \t\xa0]{2,}', ' ', crudo)
    return re.sub(r'\n\s*\n+', '\n\n', crudo).strip()


def _decodificar(parte):
    try:
        crudo = parte.get_payload(decode=True)
        if not crudo:
            return ''
        return crudo.decode(parte.get_content_charset() or 'utf-8', 'replace')
    except Exception:
        return ''


def leer(crudo, sin_leer=False):
    """Bytes de un mensaje → lo que se guarda de él, más la invitación si la trae.

    El texto plano manda; el HTML sólo cuando no hay otra cosa, y desnudo de
    etiquetas. La invitación (`ics`) no se guarda tal cual: sirve para el
    vínculo con el calendario y nada más."""
    msg = email.message_from_bytes(crudo)
    planos, htmls, adjuntos, ics = [], [], [], None
    for parte in msg.walk():
        if parte.is_multipart():
            continue
        tipo = (parte.get_content_type() or '').lower()
        nombre = _texto_cabecera(parte.get_filename())
        if tipo == 'text/calendar' or nombre.lower().endswith('.ics'):
            ics = ics or _decodificar(parte)
            if nombre:
                adjuntos.append(nombre)
            continue
        if nombre:
            adjuntos.append(nombre)
            continue
        if tipo == 'text/plain':
            planos.append(_decodificar(parte))
        elif tipo == 'text/html':
            htmls.append(_decodificar(parte))

    texto = '\n'.join(p for p in planos if p).strip()
    if not texto and htmls:
        texto = _html_a_texto('\n'.join(htmls))
    de = _texto_cabecera(msg.get('From'))
    de_correo = (email.utils.parseaddr(de)[1] or '').lower()
    # Boletines, listas y publicidad. Se reconocen por lo que ponen los que
    # envían en serie, no por quién los manda: un boletín jurídico dice
    # «hasta el 30 de noviembre» de una obligación de todo el mundo, no de un
    # encargo a esta casa.
    masivo = bool(msg.get('List-Unsubscribe') or msg.get('List-Id')
                  or (msg.get('Precedence') or '').strip().lower() in ('bulk', 'list', 'junk'))
    para = ', '.join(filter(None, [_texto_cabecera(msg.get('To')),
                                   _texto_cabecera(msg.get('Cc'))]))
    return {
        'message_id': _texto_cabecera(msg.get('Message-ID')),
        'fecha': _fecha(msg.get('Date')),
        'de': de[:300],
        'de_correo': de_correo,
        'para': para[:2000],
        'asunto': _texto_cabecera(msg.get('Subject'))[:500],
        'texto': texto[:LARGO_TEXTO],
        'adjuntos': adjuntos,
        'hilo': _texto_cabecera(msg.get('In-Reply-To')).strip('<>').lower() or None,
        'sin_leer': sin_leer,
        'masivo': masivo,
        'ics': ics,
    }


# ============================================================
#  UNA CARPETA
# ============================================================
RE_UID = re.compile(rb'UID\s+(\d+)')


def _uidvalidity(con):
    try:
        _, datos = con.response('UIDVALIDITY')
        return int(datos[0]) if datos and datos[0] else None
    except Exception:
        return None


def _por_traer(con, valida_antes, ultimo, valida_ahora, dias):
    """Los UID que faltan, del más viejo al más nuevo."""
    if valida_antes and valida_antes == valida_ahora and ultimo:
        estado, datos = con.uid('SEARCH', None, f'UID {ultimo + 1}:*')
    else:
        desde = (datetime.now() - timedelta(days=dias)).strftime('%d-%b-%Y')
        estado, datos = con.uid('SEARCH', None, f'SINCE {desde}')
        ultimo = 0
    if estado != 'OK' or not datos or not datos[0]:
        return []
    # «N:*» devuelve el último mensaje aunque sea anterior a N: así lo dice el
    # estándar. Sin este filtro, cada pasada volvería a traer el último correo.
    uids = sorted(u for u in (int(x) for x in datos[0].split()) if u > ultimo)
    return uids[-TOPE_POR_CARPETA:]


def _trozos(respuesta):
    """De la respuesta a un FETCH de varios mensajes: [(uid, sin_leer, bytes)]."""
    salida = []
    for i, item in enumerate(respuesta or []):
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta = item[0] or b''
        # Las banderas pueden venir antes o después del cuerpo: se mira en los dos lados.
        siguiente = respuesta[i + 1] if i + 1 < len(respuesta) else b''
        cola = siguiente if isinstance(siguiente, bytes) else b''
        m = RE_UID.search(meta) or RE_UID.search(cola)
        if not m:
            continue
        sin_leer = b'\\Seen' not in (meta + cola)
        salida.append((int(m.group(1)), sin_leer, item[1]))
    return salida


def leer_carpeta(con, db, cuenta, carpeta, alguardar, dias=DIAS_PRIMERA_VEZ):
    """Trae lo nuevo de una carpeta. `alguardar(mensaje, uid)` decide qué se
    hace con cada uno. Devuelve cuántos se leyeron.

    La marca se mueve lote a lote: si la conexión se corta a mitad, la pasada
    siguiente sigue desde el último lote entero, no desde el principio."""
    estado, _ = con.select(conector.entrecomillar(carpeta), readonly=True)
    if estado != 'OK':
        raise conector.ErrorCorreo(f'{cuenta}: no se pudo abrir la carpeta {carpeta}')
    valida_ahora = _uidvalidity(con)
    valida_antes, ultimo = almacen.marca(db, cuenta, carpeta)
    uids = _por_traer(con, valida_antes, ultimo, valida_ahora, dias)
    leidos = 0
    for lote in _lotes(uids):
        leidos += _traer(con, cuenta, lote, alguardar)
        almacen.apuntar_marca(db, cuenta, carpeta, valida_ahora, lote[-1])
        db.commit()
    return leidos


def _lotes(uids):
    for i in range(0, len(uids), LOTE):
        yield uids[i:i + LOTE]


def _traer(con, cuenta, lote, alguardar):
    estado, respuesta = con.uid(
        'FETCH', ','.join(str(u) for u in lote),
        f'(UID FLAGS BODY.PEEK[]<0.{TROZO}>)')
    if estado != 'OK':
        raise conector.ErrorCorreo(f'{cuenta}: el servidor no entregó el lote')
    leidos = 0
    for uid, sin_leer, crudo in _trozos(respuesta):
        try:
            alguardar(leer(crudo, sin_leer), uid)
            leidos += 1
        except Exception as e:
            print(f'[correo] {cuenta} {uid}: no se pudo leer ({str(e)[:100]})')
    return leidos


def _buscar(con, criterio):
    estado, datos = con.uid('SEARCH', None, criterio)
    if estado != 'OK' or not datos or not datos[0]:
        return set()
    return {int(x) for x in datos[0].split()}


def refrescar_banderas(con, db, cuenta, carpeta, alguardar):
    """Cómo están ahora los mensajes de la carpeta —sin leer, marcados,
    contestados— y lo marcado que aún no se tenía. La carpeta ya tiene que
    estar abierta (`leer_carpeta` la deja así).

    Son tres búsquedas que devuelven sólo números: no se baja ningún mensaje
    otra vez. Devuelve cuántos marcados viejos se trajeron."""
    sin_leer = _buscar(con, 'UNSEEN')
    marcados = _buscar(con, 'FLAGGED')
    contestados = _buscar(con, 'ANSWERED')
    faltan = sorted(marcados - almacen.uids_de(db, cuenta, carpeta))[-TOPE_MARCADOS:]
    traidos = 0
    for lote in _lotes(faltan):
        traidos += _traer(con, cuenta, lote, alguardar)
    almacen.apuntar_banderas(db, cuenta, carpeta, sin_leer, marcados, contestados)
    db.commit()
    return traidos
