# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Con qué tiene que ver cada correo.

Un correo suelto dice poco. El que cita «Oficio Nro. DGPG-2050-2026» es parte
de un trámite que ya está en el Quipux con su plazo; el que trae una
invitación es una reunión del calendario; el que dice «hasta el 15 de octubre»
es un compromiso. Este módulo lo apunta, y nada más: no crea actividades ni
citas por su cuenta. Lo que llega por correo lo escribe cualquiera, y meterlo
solo en el cronograma convertiría el semáforo en el buzón de entrada.

Los vínculos:

  quipux      Un número de documento de CuencaDOC. Es `seguro` cuando el
              número está en lo recogido del Quipux —en esta computadora, en la
              cosecha o en el almacén—; si sólo tiene la forma de serlo, y el
              correo habla de oficios o memorandos, se apunta igual: es
              justamente el aviso de que hay un quipux que no se ha recogido.
  cita        La invitación de calendario que trae el correo, por su UID: el
              mismo que usa `app.agenda_entrante` para reconocer la reunión.
  plazo       Una fecha de entrega escrita en el texto, leída con las mismas
              reglas que los plazos de los oficios (`quipux.documentos`). Nunca
              es `seguro`: sale de una frase.
  plataforma  Los avisos que manda esta misma plataforma. Sirve para apartarlos.
"""
import re
from datetime import date

# Las formas que tienen los números de CuencaDOC:
#   DGPG-2050-2026 · DF-UR-0170-2019 · CIUDADANO-CIU-2025-7061-TEMP
#   CCPD-C-SE-2026-0975-OF — un tramo de una sola letra también vale, o el
#   número se lee cortado por la mitad.
RE_NUMERO = re.compile(
    r'\b[A-Z]{2,}(?:-[A-Z]+)*-(?:\d{1,5}-\d{4}|\d{4}-\d{1,5})(?:-[A-Z]{1,6})?\b')

# Un número sin comprobar sólo cuenta si el correo habla de documentos
# oficiales. Sin esto, «ISO-9001-2015» sería un quipux.
RE_CONTEXTO_QUIPUX = re.compile(
    r'quipux|cuencadoc|oficio|memorando|memorándum|circular|tr[aá]mite|nro\.', re.I)

# Los asuntos de los avisos de `app/avisos.py`.
RE_PLATAFORMA = re.compile(
    r'tus tareas pendientes:|compromiso\(s\) vencido\(s\)|'
    r'incumplimientos que requieren decisi[oó]n|^📌 .+ una tarea:', re.I)


# ============================================================
#  LO QUE SE SABE DEL QUIPUX
# ============================================================
def quipux_conocidos(registro=print):
    """{NÚMERO: documento} con todo lo recogido en esta computadora.

    Si el paquete `quipux` no se deja importar o no hay nada recogido, se
    devuelve vacío y los números se apuntan sin comprobar: que falte el
    Quipux no puede impedir leer el correo."""
    conocidos = {}
    try:
        from quipux import almacen as q_almacen, cosecha
    except Exception as e:
        registro(f'[correo] el Quipux no está a mano ({str(e)[:100]}); '
                 'los números se apuntan sin comprobar')
        return conocidos
    fuentes = []
    try:
        fuentes.append(cosecha.documentos(ver='todos', tope=100000))
    except Exception:
        pass
    try:
        fuentes.append(q_almacen.documentos(ver='todos', tope=100000))
    except Exception:
        pass
    # Lo del almacén va después y pisa: lo trajo el recolector de la ficha del
    # propio sistema, con su enlace y su plazo. Igual que en `cosecha`.
    for lista in fuentes:
        for d in lista or []:
            numero = (d.get('numero') or '').strip().upper()
            if numero:
                conocidos[numero] = d
    return conocidos


def _detalle_quipux(doc):
    partes = [doc.get('asunto') or '']
    if doc.get('plazo_fecha'):
        partes.append(f"plazo {doc['plazo_fecha']}")
    if doc.get('area'):
        partes.append(doc['area'])
    return ' · '.join(p for p in partes if p)[:300]


# ============================================================
#  LA INVITACIÓN
# ============================================================
def _campo_ics(texto, nombre):
    """El valor del primer campo con ese nombre dentro del VEVENT. Sólo lo
    necesario para nombrar la reunión: la lectura completa, con zonas horarias y
    repeticiones, es la de `app.agenda_entrante`."""
    m = re.search(r'^%s(?:;[^:\r\n]*)?:(.*)$' % nombre, texto, re.M | re.I)
    return m.group(1).strip() if m else ''


def leer_ics(crudo):
    if not crudo or 'BEGIN:VEVENT' not in crudo.upper():
        return None
    texto = re.sub(r'\r?\n[ \t]', '', crudo)
    evento = re.search(r'BEGIN:VEVENT(.*?)END:VEVENT', texto, re.S | re.I)
    cuerpo = evento.group(1) if evento else texto
    uid = _campo_ics(cuerpo, 'UID')
    if not uid:
        return None
    titulo = (_campo_ics(cuerpo, 'SUMMARY').replace('\\,', ',').replace('\\;', ';')
              .replace('\\n', ' '))
    inicio = _campo_ics(cuerpo, 'DTSTART')
    m = re.match(r'(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?', inicio)
    if m:
        inicio = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
        if m.group(4):
            inicio += f' {m.group(4)}:{m.group(5)}'
    metodo = _campo_ics(texto, 'METHOD').upper()
    estado = _campo_ics(cuerpo, 'STATUS').upper()
    return {'uid': uid.lower(), 'titulo': titulo, 'inicio': inicio,
            'cancelada': metodo == 'CANCEL' or estado == 'CANCELLED'}


# ============================================================
#  EL PLAZO
# ============================================================
def _plazo(mensaje):
    """(fecha ISO, de dónde salió) o None. Sólo fechas posteriores al correo:
    «hasta el 3 de marzo» en un correo de octubre habla de otra cosa."""
    try:
        from quipux.documentos import deducir_plazo
    except Exception:
        return None
    dia = (mensaje.get('fecha') or '')[:10]
    fecha, origen, _ = deducir_plazo(
        {'asunto': mensaje.get('asunto') or '', 'fecha_doc': dia or None},
        mensaje.get('texto') or '')
    if not fecha or (dia and fecha.isoformat() < dia):
        return None
    if fecha > date.today().replace(year=date.today().year + 2):
        return None
    return fecha.isoformat(), origen


# ============================================================
#  TODO JUNTO
# ============================================================
def vinculos(mensaje, conocidos=None, propias=()):
    """La lista de vínculos de un mensaje.

    `propias` son las direcciones de la casa: un plazo que escribe uno mismo en
    un correo que envía es un plazo que da, no uno que le dan."""
    salida = []
    asunto = mensaje.get('asunto') or ''
    texto = mensaje.get('texto') or ''
    conocidos = conocidos or {}

    if RE_PLATAFORMA.search(asunto):
        salida.append({'tipo': 'plataforma', 'clave': 'aviso', 'detalle': asunto[:200],
                       'seguro': True})

    # De un boletín sólo se cree lo comprobado: el número que está en lo
    # recogido. Lo demás que diga —plazos, números con forma de oficio— va
    # dirigido a todo el mundo.
    masivo = bool(mensaje.get('masivo'))
    todo = f'{asunto}\n{texto}'
    habla_de_documentos = not masivo and bool(RE_CONTEXTO_QUIPUX.search(todo))
    for numero in sorted({n.upper() for n in RE_NUMERO.findall(todo.upper())}):
        if numero in conocidos:
            salida.append({'tipo': 'quipux', 'clave': numero, 'seguro': True,
                           'detalle': _detalle_quipux(conocidos[numero])})
        elif habla_de_documentos:
            salida.append({'tipo': 'quipux', 'clave': numero, 'seguro': False,
                           'detalle': 'no está en lo recogido del Quipux'})

    cita = leer_ics(mensaje.get('ics'))
    if cita:
        detalle = ' · '.join(p for p in (cita['titulo'], cita['inicio']) if p)
        if cita['cancelada']:
            detalle = 'CANCELADA · ' + detalle
        salida.append({'tipo': 'cita', 'clave': cita['uid'], 'detalle': detalle[:300],
                       'seguro': True})

    propio = (mensaje.get('de_correo') or '').lower() in {p.lower() for p in propias}
    if not propio and not masivo and not any(v['tipo'] == 'plataforma' for v in salida):
        plazo = _plazo(mensaje)
        if plazo:
            salida.append({'tipo': 'plazo', 'clave': plazo[0], 'detalle': plazo[1],
                           'seguro': False})
    return salida
