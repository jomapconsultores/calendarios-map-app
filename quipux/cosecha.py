# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""La recolección que ya estaba hecha, leída donde está y sin moverla.

Junto a este proyecto, en la carpeta hermana `06_DESARROLLO/quipux`, hay una
cosecha de CuencaDOC anterior a esta plataforma: unos cientos de documentos en
su propio SQLite, con los PDF que se alcanzaron a bajar en una carpeta por
documento. Es trabajo real y sigue creciendo por su cuenta.

La tentación es importarla: traducir sus filas, meterlas en el almacén de aquí
y seguir. Eso deja dos copias de lo mismo —con ids inventados para distinguir
cuál vino de dónde, y una reconciliación detrás para arreglarlo cuando no
cuadren—, y obliga a repetir la importación cada vez que allá cambie algo.

Así que no se importa nada. Se lee donde está, en el momento en que hace falta,
y se traduce al vuelo al formato del almacén. Si mañana se añade un documento
allá, aquí se ve sin hacer nada; si la carpeta no está —el servidor, otro
equipo—, este módulo contesta que no hay nada y la pantalla sigue su camino de
siempre, que es lo que hacía antes de existir esto.

Lo que esta cosecha NO trae, y conviene tener presente al mirarla:

  * el enlace al documento dentro de CuencaDOC, que nunca se guardó;
  * la fecha de vencimiento cuando el sistema no la dio: donde el campo
    `fecha_max_respuesta` viene vacío, aquí no hay plazo que enseñar. No se
    deduce del texto —para eso está `quipux.carpeta`—, porque inventar un plazo
    es peor que no tenerlo.

Por eso, cuando un documento está en las dos partes, manda el del almacén: lo
trajo el recolector de la ficha del propio sistema, con su enlace y su plazo.
Esta cosecha COMPLETA, no sustituye.
"""
import os
import sqlite3
from datetime import date

# La carpeta hermana del proyecto. No es una ruta personal: este repositorio
# vive en `06_DESARROLLO/calendario` y aquélla en `06_DESARROLLO/quipux`, así
# que se encuentra sola en la computadora donde están las dos, y no existe en
# el servidor, que es exactamente el comportamiento que se quiere.
_AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA_HERMANA = os.path.join(os.path.dirname(_AQUI), 'quipux')

# Las subcarpetas que no son documentos.
NO_SON_DOCUMENTOS = {'data', 'documentos', 'node_modules', '.git'}

# El perfil de allá es el área de aquí. CAPSA no estaba previsto en el esquema
# —que habla de Observatorio y Planificación—; entra con su nombre tal cual,
# antes que dejarlo fuera o meterlo en un cajón al que no pertenece.
AREAS = {
    'PLANIFICACION': 'Planificación',
    'OBSERVATORIO': 'Observatorio',
    'CAPSA': 'CAPSA',
}

# Un documento enviado, archivado, anulado o eliminado ya no es trabajo
# pendiente. La pantalla enseña lo que falta por hacer.
ESTADOS_CERRADOS = {'Archivado', 'Anulado', 'Eliminado', 'Enviado'}


def carpeta():
    """Dónde está la cosecha, o None si no hay ninguna que leer.

    `QUIPUX_COSECHA` manda cuando está puesta —sirve para apuntar a otro sitio,
    y para apagar esto del todo poniéndola en blanco a propósito—. Si no, la
    carpeta hermana, y sólo si existe de verdad."""
    puesta = os.getenv('QUIPUX_COSECHA')
    if puesta is not None:
        puesta = puesta.strip()
        return puesta if puesta and os.path.isdir(puesta) else None
    return CARPETA_HERMANA if os.path.isdir(CARPETA_HERMANA) else None


def base():
    """El SQLite de la cosecha, o None."""
    donde = carpeta()
    if not donde:
        return None
    ruta = os.path.join(donde, 'data', 'quipux.db')
    return ruta if os.path.exists(ruta) else None


def hay():
    return base() is not None


def _carpetas_por_numero(donde):
    """Las carpetas de documento que existen de verdad, por número.

    Sólo se rellena la ruta de las que están: antes en blanco que apuntando a
    un sitio que quien lo lea no va a poder abrir."""
    try:
        return {n: os.path.join(donde, n) for n in os.listdir(donde)
                if n not in NO_SON_DOCUMENTOS and os.path.isdir(os.path.join(donde, n))}
    except OSError:
        return {}


def _cuantos_adjuntos(ruta):
    try:
        return len(os.listdir(ruta))
    except OSError:
        return 0


def _estado(fila):
    if (fila['estado'] or '') in ESTADOS_CERRADOS or fila['respondido']:
        return 'cerrado'
    return 'abierto'


def _traducir(fila, carpetas):
    """Una fila de allá con las columnas que espera el almacén de aquí."""
    numero = fila['numero_documento']
    donde = carpetas.get(numero)
    plazo = (fila['fecha_max_respuesta'] or '').strip()
    return {
        # El id de allá numera 1, 2, 3…, y aquí la clave es el identificador
        # real de CuencaDOC. Se marca de dónde vino para que nadie lo confunda
        # con uno recogido por el propio recolector.
        'id': 'cosecha:%s:%s' % ((fila['perfil'] or 'SIN')[:3], numero),
        'numero': numero,
        'asunto': fila['asunto'],
        'remitente': fila['remitente'],
        'tipo': fila['tipo_documento'],
        'fecha_doc': (fila['fecha_documento'] or '')[:10] or None,
        'tramite': fila['nro_tramite'],
        'referencia': fila['no_referencia'],
        'categoria': fila['categoria'],
        'area': AREAS.get(fila['perfil'], fila['perfil']),
        'bandeja': fila['bandeja'],
        'estado': _estado(fila),
        'carpeta': donde,
        'enlace': None,                  # nunca se guardó
        'n_adjuntos': _cuantos_adjuntos(donde) if donde else 0,
        'plazo_fecha': plazo or None,
        # Vino del campo «fecha máxima de respuesta» del propio sistema, no de
        # leer el texto de un PDF: es de los seguros.
        'plazo_origen': 'sistema (recolección previa)' if plazo else None,
        'plazo_seguro': 1 if plazo else 0,
        'visto': 0,
        'actualizado': fila['extraido_en'],
        'origen': 'recolección previa',
    }


def documentos(ver='pendientes', area='', bandeja='', busca='', tope=800):
    """Los documentos de la cosecha, con los mismos filtros y el mismo orden
    que aplica el almacén, para que las dos fuentes se comporten igual.

    Devuelve lista vacía si no hay carpeta, si el archivo no se deja abrir o si
    su esquema no es el esperado. Que falte esto no puede tumbar la pantalla:
    es un complemento, no la fuente."""
    ruta = base()
    if not ruta:
        return []
    try:
        con = sqlite3.connect('file:%s?mode=ro' % ruta.replace('?', '%3f'), uri=True,
                              timeout=5)
        con.row_factory = sqlite3.Row
        try:
            filas = con.execute("""
                SELECT * FROM documentos
                 WHERE numero_documento IS NOT NULL AND numero_documento <> ''
                 ORDER BY extraido_en, id
            """).fetchall()
        finally:
            con.close()
    except Exception as e:
        print('[quipux] no se pudo leer la cosecha previa: %s' % str(e)[:150])
        return []

    # Cada pasada de allá AÑADÍA filas en vez de actualizar, así que el mismo
    # oficio aparece repetido. Se agrupa por documento y perfil —uno que llega
    # a Planificación y al Observatorio son dos entradas legítimas, no un
    # duplicado— y gana el más reciente, que es el último del ORDER BY.
    carpetas = _carpetas_por_numero(carpeta())
    unicos = {}
    for f in filas:
        unicos[(f['numero_documento'], f['perfil'])] = _traducir(f, carpetas)
    docs = list(unicos.values())

    hoy = date.today().isoformat()

    def abierto(d):
        return (d.get('estado') or 'abierto') != 'cerrado'

    if ver == 'vencidos':
        docs = [d for d in docs if d['plazo_fecha'] and d['plazo_fecha'] < hoy and abierto(d)]
    elif ver == 'con_plazo':
        docs = [d for d in docs if d['plazo_fecha']]
    elif ver == 'pendientes':
        docs = [d for d in docs if abierto(d)]
    elif ver == 'realizados':
        docs = [d for d in docs if not abierto(d)]
    if area:
        docs = [d for d in docs if d['area'] == area]
    if bandeja:
        docs = [d for d in docs if d['bandeja'] == bandeja]
    if busca:
        b = busca.lower()
        docs = [d for d in docs
                if any(b in str(d.get(c) or '').lower()
                       for c in ('asunto', 'numero', 'remitente', 'tramite', 'referencia'))]

    docs.sort(key=lambda d: (d['plazo_fecha'] or '9999-99-99',
                             '' if d['fecha_doc'] is None else str(d['fecha_doc'])))
    return docs[:tope]


def resumen():
    """El marcador de la cosecha, contado sobre lo abierto, como el del almacén."""
    docs = documentos(ver='todos', tope=100000)
    if not docs:
        return {}
    hoy = date.today().isoformat()
    abiertos = [d for d in docs if d['estado'] != 'cerrado']
    fechas = [d['actualizado'] for d in docs if d['actualizado']]
    return {
        'total': len(docs),
        'abiertos': len(abiertos),
        'con_plazo': len([d for d in abiertos if d['plazo_fecha']]),
        'vencidos': len([d for d in abiertos
                         if d['plazo_fecha'] and d['plazo_fecha'] < hoy]),
        'realizados': len(docs) - len(abiertos),
        'deducidos': 0,               # esta cosecha no deduce plazos: o están o no
        'adjuntos': sum(d['n_adjuntos'] for d in docs),
        'areas': sorted({d['area'] for d in docs if d['area']}),
        'ultima_pasada': max(fechas) if fechas else None,
        'origen': 'recolección previa',
    }
