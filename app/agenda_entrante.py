# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""La agenda entra y sale por donde haga falta, no sólo hacia afuera.

Lo que había era medio puente. La plataforma escribía en Google y ahí se
acababa: Google era el sitio donde de verdad vivían los eventos y esta
aplicación un emisor que no escuchaba. Mover una cita desde el móvil, aceptar
una convocatoria en Outlook o apuntar una reunión directamente en el
calendario no llegaba aquí nunca. La pantalla decía que el martes estaba libre
y el martes había audiencia.

Este módulo cierra el circuito. Cada pasada mira lo que hay en las nueve
cuentas y lo deja igual aquí:

  * lo que apareció fuera y aquí no está          → se recoge como cita nueva
  * lo que cambió fuera y aquí sigue como estaba  → se actualiza
  * lo que se canceló o se borró fuera            → se marca cancelado
  * lo que cambió aquí                            → no se toca; ya salió por su
                                                    propio camino

Todo va a `appointments`. No a una lista aparte de sólo lectura: una cita que
no se puede tocar desde donde se mira no es una agenda, es una fotografía. Una
reunión recogida de fuera se edita, se mueve y se cancela como cualquier otra,
y esos cambios vuelven a salir hacia la cuenta de la que vino.

De qué lado vino un cambio se decide con la marca de tiempo que Google le pone
al evento (`updated`) contra la última que conocemos (`google_updated`). Si lo
de fuera es más nuevo, el cambio se hizo allí. Si coincide, el cambio salió de
aquí y traerlo sería pisar con su propio eco lo que se acaba de escribir.

UNA REUNIÓN ES UNA FILA, LA VEAN UNA CUENTA O SEIS
--------------------------------------------------
Aquí se miran nueve agendas que se invitan entre ellas. Una reunión del despacho
con Atlas está en la agenda del despacho Y en la de Atlas: es la misma reunión
vista dos veces, no dos reuniones. Reconocerla sólo por la pareja (cuenta,
evento) la hacía nueva cada vez que entraba por otra cuenta, y el día acababa
con la misma audiencia repetida tantas veces como cuentas invitadas tuviera. Lo
mismo por correo: la invitación que esta plataforma manda vuelve a la bandeja de
los invitados que son también cuentas de la casa, y volvía a entrar como si
alguien de fuera acabara de convocarla.

Así que el evento se reconoce por lo que NO cambia al cruzar de agenda:

  * `google_event_id`          Google le pone el mismo identificador a la copia
                               de cada invitado: si ya está aquí bajo cualquier
                               cuenta, es la misma reunión
  * `iCalUID`                  el identificador de calendario, el único que
                               cruza además de Google a Outlook y al correo
  * `cita-<id>@calendario.map` el UID que pone esta plataforma en sus propias
                               invitaciones: si vuelve, es nuestra y ya está

Lo que se reconoce como copia no se escribe: la fila la lleva la cuenta por la
que entró primero, y desde ahí sale hacia fuera como cualquier otra.

De dónde se lee, según lo que admite cada cuenta:

  * Cuentas de Google: por la API de Calendar, con el mismo permiso que ya se
    usa para agendar. Se ve TODO lo que hay en esa agenda, lo hayan puesto por
    invitación o a mano desde el teléfono.

  * Cuentas de Microsoft: por IMAP, leyendo las invitaciones que llegan al
    correo. Alcance honesto: por aquí se ve lo que le INVITAN, no lo que usted
    apunte a mano directamente en el calendario de Outlook. Para eso haría
    falta una aplicación registrada en Entra por la institución.
"""
import email as _email
import imaplib
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from . import invitaciones as _inv

IMAP_HOST = 'outlook.office365.com'

# Cuánto se mira hacia atrás y hacia delante. Atrás poco: lo de la semana
# pasada ya no cambia el día de hoy, pero sirve para enterarse de que algo se
# canceló. Adelante seis meses, que es lo que abarca el planificador.
DIAS_ATRAS = 7
DIAS_ADELANTE = 180

# Los .ics que llegan por correo se buscan en un mes de bandeja: más atrás son
# invitaciones a cosas que ya pasaron.
DIAS_CORREO = 30

# Lo que se trae de fuera y se guarda aquí. Se enumera en un solo sitio porque
# es también la lista de lo que se PISA cuando algo cambió allí: si un campo no
# está aquí, un cambio hecho fuera no lo toca.
CAMPOS_DE_FUERA = ('title', 'start_time', 'end_time', 'lugar', 'direccion',
                   'ciudad', 'notes', 'meeting_link', 'invitados', 'encargado')

SELECT_SINCRONIA = ('id,title,start_time,end_time,status,calendar_id,lugar,direccion,'
                    'ciudad,notes,meeting_link,invitados,encargado,origen,visto,'
                    'google_event_id,google_cal_id,google_account,google_updated,'
                    'external_uid')

# La holgura que se le da a la marca de tiempo antes de dar un cambio por hecho
# fuera. Es para absorber el redondeo de guardar la marca y volver a leerla, no
# para tolerar cambios de verdad.
HOLGURA = timedelta(seconds=1)

# El UID que esta plataforma pone en sus propias invitaciones por correo.
PREFIJO_PROPIO = 'cita-'
SUFIJO_PROPIO = '@calendario.map'


# ============================================================
#  LO QUE YA TENEMOS
# ============================================================
def _citas_conocidas(app):
    """Índice de lo que ya está aquí, por cada nombre con el que puede volver a
    presentarse.

    Se lee de una vez y no cita por cita: nueve cuentas por doscientos eventos
    son mil ochocientas consultas que la base no tiene por qué aguantar cada
    cuarto de hora.

    Cuatro entradas y no una, porque el mismo evento llega llamándose distinto
    según por dónde entre:

      por_cuenta  (cuenta, evento) → la fila que ESA cuenta lleva. Es la que se
                  actualiza cuando el cambio se hizo fuera.
      por_evento  evento → la fila, venga por la cuenta que venga. Google le pone
                  el mismo identificador a la copia de cada invitado, así que
                  esto es lo que distingue «la misma reunión otra vez» de «una
                  reunión nueva».
      por_uid     UID de calendario → la fila. Es lo que cruza de Google a
                  Outlook y al correo, y lo que permite reconocer nuestras
                  propias invitaciones cuando vuelven.
      por_cita    id de aquí → la fila, para cuando lo que vuelve es un UID que
                  pusimos nosotros.
    """
    try:
        # Por páginas, y no de un tirón: PostgREST corta en las primeras mil
        # filas y no dice que cortó. Lo que quedaba fuera de esa página quedaba
        # fuera del índice, y lo que no está en el índice se toma por nuevo: la
        # misma reunión volvía a entrar en cada pasada, cada cuarto de hora, y
        # cada tanda empujaba a más citas fuera de la página.
        # Y `_detallado`, porque aquí hay que saber si la lista vacía es una
        # agenda vacía o una consulta que no se pudo hacer. Un error de
        # PostgREST no levanta excepción: devuelve []. Sin distinguirlo, el
        # índice salía vacío, la pasada daba por nueva cada reunión que leía y
        # volvía a crear la agenda ENTERA — y no hacía falta una caída de la
        # base: bastaba con que faltara una columna del select, es decir con una
        # migración sin aplicar, para que eso pasara cada cuarto de hora.
        filas, motivo = app.supabase.get_todo_detallado(
            'appointments', select=SELECT_SINCRONIA)
        if motivo:
            print(f'[agenda] no se pudieron leer las citas ({motivo}): '
                  f'no se sincroniza, para no duplicarlas')
            return None
    except Exception as e:
        print(f'[agenda] no se pudieron leer las citas: {e}')
        return None
    indice = _indice_vacio()
    for f in filas:
        _apuntar(indice, f)
    return indice


def _indice_vacio():
    return {'por_cuenta': {}, 'por_evento': {}, 'por_uid': {}, 'por_cita': {}}


def _apuntar(indice, cita, cuenta=None):
    """Deja la fila en el índice por todos sus nombres, para que lo que entre
    después en la misma pasada ya la reconozca."""
    if cita.get('id'):
        indice['por_cita'][str(cita['id'])] = cita
    gid = cita.get('google_event_id')
    if gid:
        indice['por_cuenta'][(cuenta or cita.get('google_account'), gid)] = cita
        indice['por_evento'].setdefault(gid, cita)
    if cita.get('external_uid'):
        indice['por_uid'].setdefault(str(cita['external_uid']).strip().lower(), cita)


def identidad(uid, dia=None):
    """El nombre con el que un evento se presenta en CUALQUIER agenda.

    Es el UID de calendario, con una salvedad que importa: las repeticiones de
    una serie comparten todas el mismo UID. Tomarlo tal cual haría que de una
    reunión semanal entrara sólo la primera semana y las demás se descartaran por
    «repetidas», que es el error contrario al que se viene a arreglar. Así que a
    una repetición se le añade el día al que corresponde —el mismo en la copia de
    cada invitado, que es lo que tiene que seguir cuadrando entre cuentas.

    Esta regla la tienen que aplicar TODOS los caminos por los que un evento
    entra o se apunta: Google, el correo y lo que la propia plataforma guarda al
    crear un evento allí. Cuando cada camino escribía lo suyo, la misma reunión
    quedaba archivada bajo dos nombres distintos y volvía a duplicarse, que es
    justamente lo que no puede pasar."""
    uid = (str(uid).strip().lower() if uid else '') or None
    if not uid or not dia:
        return uid
    # El día, siempre escrito igual. Cada camino lo trae a su manera: Google
    # manda `...T15:00:00Z` en un sitio y `...T10:00:00-05:00` en otro, el ICS
    # del correo lo devuelve ya en ISO con `+00:00`, y una serie de día completo
    # es una fecha pelada. Son el mismo instante escrito de cuatro formas, y
    # pegado crudo al UID daba cuatro nombres distintos para la misma
    # repetición: exactamente el duplicado que el UID venía a evitar.
    marca = _instante(dia)
    return '%s#%s' % (uid, marca.strftime('%Y%m%dT%H%M%SZ') if marca else dia)


def _cita_propia(uid, indice):
    """Si este UID lo puso esta plataforma, la cita que nombra.

    Una invitación mandada desde aquí vuelve a la bandeja de los invitados que
    son también cuentas de la casa. Sin esto volvía a entrar como convocatoria
    ajena y la misma reunión quedaba dos veces: la que se creó y la que se
    recibió de sí misma."""
    if not uid:
        return None
    # Sin el día: una invitación nuestra puede ser una repetición de serie, y lo
    # que dice de qué cita es, es el UID; el día sólo distingue una semana de
    # otra.
    texto = str(uid).strip().lower().split('#')[0]
    if not texto.startswith(PREFIJO_PROPIO) or not texto.endswith(SUFIJO_PROPIO):
        return None
    return indice['por_cita'].get(texto[len(PREFIJO_PROPIO):-len(SUFIJO_PROPIO)])


def identidad_de_evento(ev):
    """La identidad de un evento tal como lo devuelve la API de Google."""
    uid = ev.get('iCalUID')
    if not uid or not ev.get('recurringEventId'):
        return identidad(uid)
    cuando = ev.get('originalStartTime') or ev.get('start') or {}
    return identidad(uid, cuando.get('dateTime') or cuando.get('date') or ev.get('id'))


def _copia_de_lo_que_ya_hay(indice, evento_id=None, uid=None):
    """La fila que ya tenemos para este mismo evento, si está: por su
    identificador de Google —que es el mismo en la agenda de cada invitado—, por
    su UID de calendario, o porque el UID lo pusimos nosotros."""
    return (indice['por_evento'].get(evento_id) if evento_id else None) \
        or (indice['por_uid'].get(str(uid).strip().lower()) if uid else None) \
        or _cita_propia(uid, indice)


def _guardar_uid(app, cita, uid, indice):
    """Le pone a una fila el UID con el que el evento se va a presentar la
    próxima vez. Un UID que falta es justamente lo que permite que la misma
    reunión vuelva a entrar por otra cuenta."""
    if not uid or not cita.get('id') or cita.get('external_uid'):
        return
    cita['external_uid'] = uid
    indice['por_uid'].setdefault(uid, cita)
    app.supabase.update('appointments', cita['id'], {'external_uid': uid})


def _instante(valor):
    """Una marca de tiempo, venga como venga, en algo que se pueda comparar."""
    if not valor:
        return None
    texto = str(valor).strip().replace('Z', '+00:00')
    try:
        marca = datetime.fromisoformat(texto)
    except Exception:
        return None
    if marca.tzinfo is None:
        marca = marca.replace(tzinfo=timezone.utc)
    return marca.astimezone(timezone.utc)


def _mas_nuevo_fuera(evento_updated, cita):
    """¿El cambio se hizo allí, o es el eco de lo que escribimos nosotros?

    Ante la duda —no hay marca guardada— se da por bueno lo de fuera: una
    primera pasada tiene que poder ponerse al día. Lo que no puede pasar nunca
    es lo contrario, traer como novedad lo que uno mismo acaba de mandar.

    Las dos marcas se comparan como FECHAS, no como texto. Como texto esto no
    protegía de nada: Google manda `...10:00:00.000Z` y la base devuelve lo mismo
    escrito `...10:00:00+00:00`, y carácter a carácter la `Z` va por delante del
    `+`, así que lo de fuera salía siempre más nuevo. Cada pasada se creía con
    derecho a volver a traer lo que se acababa de escribir aquí."""
    conocido = _instante(cita.get('google_updated'))
    fuera = _instante(evento_updated)
    if conocido is None or fuera is None:
        return True
    return fuera > conocido + HOLGURA


def _cambios(nuevo, cita):
    """Sólo lo que de verdad cambió. Mandar a la base una fila entera idéntica
    cuenta como escritura, dispara los avisos y ensucia el historial."""
    return {k: v for k, v in nuevo.items()
            if k in CAMPOS_DE_FUERA and (v or '') != (cita.get(k) or '')}


# ============================================================
#  GOOGLE
# ============================================================
def _hora(valor):
    """{'dateTime': ...} | {'date': ...} → (iso, es_todo_el_dia)."""
    if not valor:
        return None, False
    if valor.get('dateTime'):
        return valor['dateTime'], False
    if valor.get('date'):
        return valor['date'] + 'T00:00:00+00:00', True
    return None, False


def _enlace(evento):
    if evento.get('hangoutLink'):
        return evento['hangoutLink']
    for punto in ((evento.get('conferenceData') or {}).get('entryPoints') or []):
        if punto.get('uri'):
            return punto['uri']
    return None


def _quien_convoca(evento):
    org = evento.get('organizer') or {}
    return (org.get('displayName') or org.get('email') or '')[:100]


def _cita_desde_evento(evento, cuenta, calendar_id):
    """Un evento de Google contado como cita de aquí. None si no se puede
    situar en el calendario —sin fecha no hay nada que enseñar."""
    inicio, todo_el_dia = _hora(evento.get('start'))
    fin, _ = _hora(evento.get('end'))
    if not inicio:
        return None
    invitados = ', '.join(a.get('email', '') for a in (evento.get('attendees') or [])
                          if a.get('email') and not a.get('self'))
    return {
        'title': (evento.get('summary') or '(sin título)')[:200],
        'start_time': inicio,
        'end_time': fin or inicio,
        'calendar_id': calendar_id,
        'encargado': _quien_convoca(evento),
        'lugar': (evento.get('location') or '')[:150],
        'direccion': '',
        'ciudad': '',
        'notes': (evento.get('description') or '')[:1000],
        'meeting_link': (_enlace(evento) or '')[:500],
        'invitados': invitados[:1000],
        'todo_el_dia': todo_el_dia,
    }


def _eventos_de(service, gcal_id, desde, hasta):
    """Todos los eventos de una agenda en la ventana que nos importa.

    Google contesta por páginas. Antes se leía sólo la primera —doscientos
    cincuenta eventos— y el resto del semestre no existía para esta plataforma:
    una agenda cargada se quedaba a medio traer sin que nada lo dijera."""
    eventos, pagina, vueltas = [], None, 0
    while vueltas < 20:
        lote = service.events().list(
            calendarId=gcal_id,
            timeMin=desde.isoformat(), timeMax=hasta.isoformat(),
            singleEvents=True, orderBy='startTime',
            showDeleted=True, maxResults=250, pageToken=pagina).execute()
        eventos.extend(lote.get('items', []))
        pagina = lote.get('nextPageToken')
        vueltas += 1
        if not pagina:
            break
    return eventos


def sincronizar_google(app, cuentas, cal_por_cuenta=None, gcals_por_cuenta=None):
    """Pone al día lo que hay en las agendas de Google. Resumen por cuenta."""
    indice = _citas_conocidas(app)
    if indice is None:
        return {c: {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0, 'copias': 0,
                    'error': 'no se pudo leer la agenda de aquí'} for c in cuentas}

    desde = datetime.now(timezone.utc) - timedelta(days=DIAS_ATRAS)
    hasta = datetime.now(timezone.utc) + timedelta(days=DIAS_ADELANTE)
    resumen = {}

    for cuenta in cuentas:
        cuenta_res = {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0,
                      'copias': 0, 'error': None}
        creds = app.obtener_creds_google(cuenta)
        if creds is None:
            cuenta_res['error'] = 'sin conectar'
            resumen[cuenta] = cuenta_res
            continue
        calendar_id = (cal_por_cuenta or {}).get(cuenta)
        if not calendar_id:
            # Sin un calendario de esta plataforma al que atar lo que entra, la
            # cita no tendría dueño ni permisos: no se recoge, y se dice.
            cuenta_res['error'] = 'esa cuenta no tiene ningún calendario asignado'
            resumen[cuenta] = cuenta_res
            continue
        # La agenda principal y además aquellas en las que esta plataforma
        # escribe. Leer sólo `primary` dejaba fuera del camino de vuelta
        # justamente los calendarios donde la plataforma pone sus citas: lo que
        # se anotaba o se movía allí a mano no llegaba aquí nunca.
        agendas = ['primary'] + [g for g in (gcals_por_cuenta or {}).get(cuenta, ())
                                 if g and g != 'primary']
        try:
            service = build('calendar', 'v3', credentials=creds)
            eventos, vistos = [], set()
            for gcal_id in agendas:
                for ev in _eventos_de(service, gcal_id, desde, hasta):
                    # La misma agenda puede aparecer con dos nombres ('primary' y
                    # su dirección): el evento se atiende una sola vez.
                    if ev.get('id') in vistos:
                        continue
                    vistos.add(ev.get('id'))
                    eventos.append((ev, gcal_id))
        except Exception as e:
            cuenta_res['error'] = str(e)[:150]
            resumen[cuenta] = cuenta_res
            continue

        for ev, gcal_id in eventos:
            try:
                _aplicar_evento(app, ev, cuenta, calendar_id, indice, cuenta_res,
                                gcal_id=gcal_id)
            except Exception as e:
                print(f'[agenda] {cuenta} evento {ev.get("id")}: {str(e)[:150]}')
        resumen[cuenta] = cuenta_res
    return resumen


def _aplicar_evento(app, ev, cuenta, calendar_id, indice, res, gcal_id='primary'):
    """Deja aquí este evento como está allí."""
    uid = identidad_de_evento(ev)
    cita = indice['por_cuenta'].get((cuenta, ev.get('id')))
    cancelado_fuera = ev.get('status') == 'cancelled'

    # ---- Lo mismo, visto desde otra de nuestras cuentas ----
    #
    # Una reunión entre dos cuentas de la casa está en las dos agendas. Es un
    # solo compromiso y le toca una sola fila: la lleva la cuenta por la que
    # entró primero, y es esa la que la mantiene al día. Sin esto la misma
    # audiencia aparecía tantas veces como cuentas invitadas tuviera.
    if cita is None:
        copia = _copia_de_lo_que_ya_hay(indice, ev.get('id'), uid)
        if copia is not None:
            res['copias'] += 1
            # No se le cambia el dueño: la fila sigue siendo de la cuenta que la
            # trajo, y es esa la que la mantiene al día. Lo único que se hace es
            # guardarle el UID si le faltaba, que es lo que la hace reconocible
            # la próxima vez sin depender del identificador del evento.
            _guardar_uid(app, copia, uid, indice)
            return

    # ---- Ya lo teníamos ----
    if cita:
        _guardar_uid(app, cita, uid, indice)
        if not _mas_nuevo_fuera(ev.get('updated'), cita):
            return                          # el cambio salió de aquí
        if cancelado_fuera:
            # Se canceló allí. Aquí NO se borra: una cita que desaparece sin
            # dejar rastro es la forma más limpia de que nadie sepa que existió.
            if cita.get('status') != 'cancelled':
                app.supabase.update('appointments', cita['id'], {
                    'status': 'cancelled', 'google_updated': ev.get('updated'),
                    'sincronizado_en': datetime.now(timezone.utc).isoformat(),
                    'visto': False})
                res['canceladas'] += 1
            return
        nuevo = _cita_desde_evento(ev, cuenta, cita.get('calendar_id') or calendar_id)
        if not nuevo:
            return
        cambios = _cambios(nuevo, cita)
        marca = {'google_updated': ev.get('updated'),
                 'sincronizado_en': datetime.now(timezone.utc).isoformat()}
        if cambios:
            # Que cambie fuera es una novedad que alguien tiene que mirar,
            # igual que lo sería una llamada avisando de que la hora cambió.
            cambios['visto'] = False
            res['actualizadas'] += 1
        app.supabase.update('appointments', cita['id'], {**cambios, **marca})
        return

    # ---- Es nuevo para nosotros ----
    if cancelado_fuera:
        return                              # cancelado y nunca lo tuvimos: nada que hacer
    nuevo = _cita_desde_evento(ev, cuenta, calendar_id)
    if not nuevo:
        return
    nuevo.pop('todo_el_dia', None)
    nuevo.update({
        # Confirmada, no pendiente: el compromiso ya lo adquirió alguien. Pasarla
        # por la aprobación del despacho sería pedir permiso para algo que ya
        # está puesto en la agenda de otro.
        'status': 'confirmed',
        'origen': 'externo',
        'visto': False,
        'google_event_id': ev.get('id'),
        # La agenda de la que se leyó, no 'primary' a ciegas: si el evento vive
        # en un calendario secundario, apuntar 'primary' deja la ficha señalando
        # a un sitio donde ese evento no está, y luego no se puede ni mover ni
        # borrar desde aquí.
        'google_cal_id': gcal_id,
        'google_account': cuenta,
        'google_updated': ev.get('updated'),
        'sincronizado_en': datetime.now(timezone.utc).isoformat(),
    })
    if uid:
        # El identificador que el evento conserva al cruzar a otra agenda: es lo
        # que impide que la misma reunión vuelva a entrar por otra cuenta.
        nuevo['external_uid'] = uid
    creada = app.supabase.insert('appointments', nuevo)
    if creada:
        res['nuevas'] += 1
        _apuntar(indice, {**nuevo, 'id': creada[0].get('id')}, cuenta)


# ============================================================
#  CORREO (IMAP) — las cuentas de Microsoft
# ============================================================
def _desplegar(texto):
    """Deshace el plegado del RFC 5545: una línea partida con un espacio al
    principio de la siguiente es la misma línea."""
    return re.sub(r'\r?\n[ \t]', '', texto)


def _campos_ics(texto):
    """Parser mínimo del primer VEVENT. No pretende ser una implementación
    completa del formato: sólo saca lo que hace falta para poder situar el
    evento en el calendario, y si algo no encaja se descarta ese evento en vez
    de inventarse un dato."""
    texto = _desplegar(texto)
    trozo = re.search(r'BEGIN:VEVENT(.*?)END:VEVENT', texto, re.S)
    if not trozo:
        return None
    campos = {'METHOD': None}
    m = re.search(r'^METHOD:(.+)$', texto, re.M)
    if m:
        campos['METHOD'] = m.group(1).strip()
    for linea in trozo.group(1).splitlines():
        if ':' not in linea:
            continue
        clave, valor = linea.split(':', 1)
        nombre = clave.split(';')[0].strip().upper()
        params = clave.split(';')[1:]
        campos.setdefault(nombre, (valor.strip(), params))
    return campos


def _valor(campos, nombre, defecto=None):
    v = campos.get(nombre)
    return v[0] if isinstance(v, tuple) else defecto


def _desescapar(texto):
    return (str(texto or '').replace('\\n', '\n').replace('\\,', ',')
            .replace('\\;', ';').replace('\\\\', '\\'))


def _fecha_ics(campos, nombre):
    """DTSTART en cualquiera de sus formas → ISO, y si es de día completo lo dice."""
    v = campos.get(nombre)
    if not isinstance(v, tuple):
        return None, False
    crudo, params = v
    todo_el_dia = any('VALUE=DATE' in p.upper() for p in params)
    crudo = crudo.strip()
    try:
        if todo_el_dia or (len(crudo) == 8 and crudo.isdigit()):
            return datetime.strptime(crudo, '%Y%m%d').replace(
                tzinfo=timezone.utc).isoformat(), True
        if crudo.endswith('Z'):
            return datetime.strptime(crudo, '%Y%m%dT%H%M%SZ').replace(
                tzinfo=timezone.utc).isoformat(), False
        # Sin Z: hora local del que convoca. Se interpreta en la del despacho,
        # que es donde se va a mirar el calendario.
        import pytz
        local = pytz.timezone(_inv.TZ_NOMBRE)
        return local.localize(datetime.strptime(crudo, '%Y%m%dT%H%M%S')).isoformat(), False
    except Exception:
        return None, todo_el_dia


def _correo_de(valor):
    if not valor:
        return None
    m = re.search(r'mailto:([^\s;:]+)', valor, re.I)
    return (m.group(1) if m else valor).strip().lower() or None


def _conectar_imap(app, cuenta):
    token, error = _inv.token_de_acceso(app, cuenta)
    if not token:
        return None, error
    # En crudo, sin base64: `authenticate` codifica por su cuenta lo que
    # devuelva esta función. Codificarlo aquí lo mandaba dos veces codificado y
    # Outlook contestaba «AUTHENTICATE command error: BAD Command Argument
    # Error», que suena a buzón sin permiso —y a que hay que pedirle algo al
    # administrador del dominio— cuando el permiso estaba bien y lo que iba mal
    # era la cadena. En SMTP sí se codifica a mano, porque allí se escribe el
    # argumento del comando AUTH; son dos caminos distintos y no se parecen.
    cadena = f'user={cuenta}\x01auth=Bearer {token}\x01\x01'
    try:
        conexion = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        conexion.authenticate('XOAUTH2', lambda _: cadena.encode())
        return conexion, None
    except Exception as e:
        return None, f'no se pudo abrir la bandeja ({str(e)[:150]})'


def sincronizar_correo(app, cuentas, cal_por_cuenta=None, dias=None):
    """Lee las invitaciones que llegaron por correo a las cuentas de Microsoft."""
    indice = _citas_conocidas(app)
    if indice is None:
        return {c: {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0, 'copias': 0,
                    'error': 'no se pudo leer la agenda de aquí'} for c in cuentas}

    resumen = {}
    desde = datetime.now(timezone.utc) - timedelta(days=dias or DIAS_CORREO)
    criterio = desde.strftime('%d-%b-%Y')

    for cuenta in cuentas:
        res = {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0, 'copias': 0,
               'error': None}
        calendar_id = (cal_por_cuenta or {}).get(cuenta)
        if not calendar_id:
            res['error'] = 'esa cuenta no tiene ningún calendario asignado'
            resumen[cuenta] = res
            continue
        conexion, error = _conectar_imap(app, cuenta)
        if error:
            res['error'] = error
            resumen[cuenta] = res
            continue
        try:
            conexion.select('INBOX', readonly=True)
            estado, datos = conexion.search(None, f'(SINCE {criterio})')
            uids = datos[0].split() if estado == 'OK' and datos and datos[0] else []
            # De los más nuevos hacia atrás, y con tope: una bandeja de un mes
            # puede traer miles de mensajes y esto corre en segundo plano.
            for uid in list(reversed(uids))[:400]:
                estado, cuerpo = conexion.fetch(uid, '(RFC822)')
                if estado != 'OK' or not cuerpo or not isinstance(cuerpo[0], tuple):
                    continue
                lectura = leer_invitacion(
                    _email.message_from_bytes(cuerpo[0][1]), cuenta, calendar_id)
                if lectura:
                    _aplicar_invitacion(app, lectura, cuenta, indice, res)
        except Exception as e:
            res['error'] = str(e)[:150]
        try:
            conexion.close(); conexion.logout()
        except Exception:
            pass
        resumen[cuenta] = res
    return resumen


def leer_invitacion(mensaje, cuenta, calendar_id):
    """Saca la cita de un correo, si es que lo lleva. Devuelve None cuando no
    es una invitación: la inmensa mayoría de la bandeja no lo es."""
    crudo = None
    for parte in mensaje.walk():
        tipo = (parte.get_content_type() or '').lower()
        nombre = (parte.get_filename() or '').lower()
        if tipo == 'text/calendar' or nombre.endswith('.ics'):
            try:
                crudo = parte.get_payload(decode=True).decode(
                    parte.get_content_charset() or 'utf-8', 'ignore')
            except Exception:
                crudo = None
            if crudo:
                break
    if not crudo:
        return None
    campos = _campos_ics(crudo)
    if not campos:
        return None

    uid = _valor(campos, 'UID')
    inicio, todo_el_dia = _fecha_ics(campos, 'DTSTART')
    if not uid or not inicio:
        return None
    fin, _ = _fecha_ics(campos, 'DTEND')

    # Igual que en Google: una repetición de serie lleva el mismo UID que todas
    # sus hermanas, y lo que dice de cuál se trata es el `RECURRENCE-ID`. Sin
    # esto, la invitación de la semana que viene se tomaba por la de esta —misma
    # cita, misma fila— y le cambiaba la hora en vez de entrar como otra reunión;
    # y la serie que además se veía por Google, archivada allí con el día pegado
    # al UID, no se reconocía como la misma y se duplicaba.
    recurrencia, _dia_entero = _fecha_ics(campos, 'RECURRENCE-ID')
    uid = identidad(uid, recurrencia)

    metodo = (campos.get('METHOD') or '').upper()
    estado_ics = (_valor(campos, 'STATUS') or '').upper()
    cancelado = metodo == 'CANCEL' or estado_ics == 'CANCELLED'

    organizador = _correo_de(_valor(campos, 'ORGANIZER'))
    # Lo que uno mismo convoca desde su Outlook vuelve a su propia bandeja como
    # copia, y lo que sale de esta plataforma vuelve igual. Ni lo uno ni lo otro
    # es «lo que me agendan»: ya está en la agenda por su propio camino.
    if organizador and organizador == cuenta.lower() and not cancelado:
        return None

    try:
        secuencia = int(_valor(campos, 'SEQUENCE') or 0)
    except Exception:
        secuencia = 0

    return {
        'uid': uid,
        'cancelado': cancelado,
        'secuencia': secuencia,
        'todo_el_dia': todo_el_dia,
        'cita': {
            'title': (_desescapar(_valor(campos, 'SUMMARY')) or '(sin título)')[:200],
            'start_time': inicio,
            'end_time': fin or inicio,
            'calendar_id': calendar_id,
            'encargado': (organizador or '')[:100],
            'lugar': (_desescapar(_valor(campos, 'LOCATION')) or '')[:150],
            'notes': (_desescapar(_valor(campos, 'DESCRIPTION')) or '')[:1000],
            'invitados': (organizador or '')[:1000],
            'meeting_link': '',
            'direccion': '', 'ciudad': '',
        },
    }


def _aplicar_invitacion(app, lectura, cuenta, indice, res):
    """Deja aquí lo que dice esta invitación."""
    uid = str(lectura['uid']).strip().lower()
    cita = indice['por_uid'].get(uid)
    ahora = datetime.now(timezone.utc).isoformat()

    # La invitación de una reunión que ya está aquí por otro camino: porque la
    # mandó esta plataforma y vuelve a la bandeja de un invitado de la casa, o
    # porque el mismo evento ya entró por la agenda de Google de otra cuenta. Se
    # reconoce y no se repite; como mucho se le guarda el UID a la fila que ya
    # existe, para que la siguiente vez el reconocimiento sea inmediato.
    if cita is None:
        copia = _cita_propia(uid, indice)
        if copia is not None:
            res['copias'] += 1
            _guardar_uid(app, copia, uid, indice)
            return

    # La misma reunión, pero la lleva la API de Google: el correo es entonces la
    # copia y no la fuente. Dejarle escribir aquí ponía a los dos caminos a
    # corregirse el uno al otro cada cuarto de hora —uno pone lo que dice el
    # .ics, el otro lo que dice el evento— y a pedir cada vez que alguien lo
    # mirara. La cancelación tampoco se pierde: el evento también se cancela allí
    # y la pasada de Google la trae.
    if cita and cita.get('google_event_id'):
        res['copias'] += 1
        return

    if cita:
        if lectura['cancelado']:
            if cita.get('status') != 'cancelled':
                app.supabase.update('appointments', cita['id'], {
                    'status': 'cancelled', 'visto': False, 'sincronizado_en': ahora})
                res['canceladas'] += 1
            return
        cambios = _cambios(lectura['cita'], cita)
        if cambios:
            cambios['visto'] = False
            app.supabase.update('appointments', cita['id'],
                                {**cambios, 'sincronizado_en': ahora,
                                 'ics_sequence': lectura['secuencia']})
            res['actualizadas'] += 1
        return

    if lectura['cancelado']:
        return
    nueva = dict(lectura['cita'])
    nueva.update({
        'status': 'confirmed', 'origen': 'externo', 'visto': False,
        'external_uid': uid, 'google_account': cuenta,
        'ics_sequence': lectura['secuencia'], 'sincronizado_en': ahora,
    })
    creada = app.supabase.insert('appointments', nueva)
    if creada:
        res['nuevas'] += 1
        _apuntar(indice, {**nueva, 'id': creada[0].get('id')}, cuenta)


# ============================================================
#  RESUMEN
# ============================================================
def resumen(app):
    """Cuánto ha entrado de fuera y cuánto está sin mirar. Es lo que se enseña
    en la pantalla de cuentas para saber si el puente está vivo."""
    salida = {'total': 0, 'sin_ver': 0, 'proximos': 0, 'por_cuenta': {}}
    try:
        filas = app.supabase.get_q('appointments', {'origen': 'eq.externo'},
                                   select='google_account,visto,status,start_time') or []
    except Exception:
        return salida
    ahora = datetime.now(timezone.utc).isoformat()
    for f in filas:
        if f.get('status') == 'cancelled':
            continue
        salida['total'] += 1
        cuenta = f.get('google_account') or '?'
        salida['por_cuenta'][cuenta] = salida['por_cuenta'].get(cuenta, 0) + 1
        if not f.get('visto'):
            salida['sin_ver'] += 1
        if (f.get('start_time') or '') >= ahora:
            salida['proximos'] += 1
    return salida


# ============================================================
#  LA PASADA COMPLETA
# ============================================================
def sincronizar(app, cuentas_google, cuentas_microsoft, cal_por_cuenta=None,
                gcals_por_cuenta=None):
    """Una pasada por todas las cuentas. Lo que falle en una no detiene el resto:
    que csccue no conteste no puede dejar sin agenda a las otras ocho."""
    resultado = {'google': {}, 'correo': {}}
    if cuentas_google:
        resultado['google'] = sincronizar_google(app, cuentas_google, cal_por_cuenta,
                                                 gcals_por_cuenta)
    if cuentas_microsoft:
        resultado['correo'] = sincronizar_correo(app, cuentas_microsoft, cal_por_cuenta)
    todas = list(resultado['google'].values()) + list(resultado['correo'].values())
    resultado['nuevas'] = sum(v.get('nuevas', 0) for v in todas)
    resultado['actualizadas'] = sum(v.get('actualizadas', 0) for v in todas)
    resultado['canceladas'] = sum(v.get('canceladas', 0) for v in todas)
    # Las que se reconocieron como la misma reunión vista desde otra cuenta. No
    # es un error ni un aviso: es el número de duplicados que NO se crearon.
    resultado['copias'] = sum(v.get('copias', 0) for v in todas)
    resultado['errores'] = {c: v['error']
                            for grupo in (resultado['google'], resultado['correo'])
                            for c, v in grupo.items() if v.get('error')}
    return resultado


def arrancar_autosync(app, interval_min=15):
    """Revisa las agendas en segundo plano.

    Cada cuarto de hora, no cada dos minutos: esto sale a la red por nueve
    cuentas y lo que se gana bajando el intervalo es enterarse antes de algo
    que, si es de hoy mismo, ya llegó además por correo. Como el resto de
    trabajos de fondo, un solo worker se lo queda con flock."""
    try:
        import fcntl
    except Exception:
        print('[agenda] fcntl no disponible (dev local): sincronización desactivada')
        return
    try:
        import os
        import tempfile
        ruta = os.path.join(tempfile.gettempdir(), 'agenda_entrante.lock')
        archivo = open(ruta, 'w')
        fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        app._agenda_entrante_lock = archivo
    except Exception:
        return          # otro worker ya lo tiene

    def _bucle():
        time.sleep(180)     # que termine de levantar el despliegue
        while True:
            try:
                r = app.sincronizar_agenda_entrante()
                if r.get('nuevas') or r.get('actualizadas') or r.get('canceladas'):
                    print(f"[agenda] entraron {r['nuevas']}, cambiaron "
                          f"{r['actualizadas']}, se cancelaron {r['canceladas']}")
            except Exception as e:
                print(f'[agenda] {e}')
            time.sleep(interval_min * 60)

    threading.Thread(target=_bucle, name='agenda-entrante', daemon=True).start()
    print(f'[agenda] sincronización en los dos sentidos activa (cada {interval_min} min)')
