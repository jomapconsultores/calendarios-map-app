# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Lo que le agendan a uno también ocupa el día.

Hasta ahora el calendario de la plataforma enseñaba sólo la mitad de la
agenda: las citas que salieron de aquí. La otra mitad —la convocatoria del
CSCCUE, la reunión que propone un cliente, lo que uno mismo apuntó desde el
móvil— vivía en las cuentas de correo y no existía para el sistema. Con esa
mitad fuera, el calendario decía que el martes estaba libre cuando el martes
había audiencia, y la plataforma agendaba encima.

Este módulo trae esa otra mitad. No la convierte en citas del despacho: son
cosas distintas y mezclarlas sería mentir sobre quién manda en cada una. Una
cita de `appointments` se pidió, se aprobó y se responde de ella; esto se
recibe. Va a su propia tabla, `agenda_externa`, se pinta en el calendario con
otro aspecto y se puede convertir en cita si merece serlo.

De dónde se lee, según lo que admite cada cuenta:

  * Cuentas de Google (7): por la API de Calendar, con el mismo permiso que ya
    se usa para agendar. Se ve TODO lo que hay en esa agenda, lo hayan puesto
    por invitación o a mano desde el teléfono.

  * Cuentas de Microsoft (hotmail, csccue): por IMAP, leyendo las invitaciones
    que llegan al correo. Alcance honesto: por aquí se ve lo que le INVITAN,
    no lo que usted apunte a mano directamente en el calendario de Outlook.
    Para eso haría falta una aplicación registrada en Entra por la institución.
"""
import email as _email
import imaplib
import re
import threading
import time
from base64 import b64encode
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


# ============================================================
#  GUARDAR
# ============================================================
def _guardar(app, filas):
    """Inserta o actualiza en bloque. El mismo evento visto otra vez tiene que
    ACTUALIZAR: la sincronización pasa cada cuarto de hora y acumular copias
    llenaría el calendario del mismo acto repetido."""
    if not filas:
        return 0
    guardadas = 0
    for i in range(0, len(filas), 100):
        lote = filas[i:i + 100]
        if app.supabase.upsert('agenda_externa', lote, 'cuenta_email,event_id'):
            guardadas += len(lote)
    return guardadas


# ============================================================
#  GOOGLE
# ============================================================
def _ids_de_la_plataforma(app):
    """Los eventos que creó la propia plataforma. No se traen de vuelta: ya
    están en el calendario como citas, y volverlos a pintar como «agenda
    externa» los enseñaría dos veces."""
    try:
        filas = app.supabase.get('appointments', select='google_event_id') or []
    except Exception:
        return set()
    return {f['google_event_id'] for f in filas if f.get('google_event_id')}


def _hora(valor):
    """{'dateTime': ...} | {'date': ...} → (iso, es_todo_el_dia)."""
    if not valor:
        return None, False
    if valor.get('dateTime'):
        return valor['dateTime'], False
    if valor.get('date'):
        return valor['date'] + 'T00:00:00+00:00', True
    return None, False


def _mi_respuesta(evento):
    for a in (evento.get('attendees') or []):
        if a.get('self'):
            return a.get('responseStatus')
    return None


def _enlace(evento):
    if evento.get('hangoutLink'):
        return evento['hangoutLink']
    for punto in ((evento.get('conferenceData') or {}).get('entryPoints') or []):
        if punto.get('uri'):
            return punto['uri']
    return None


def sincronizar_google(app, cuentas, cal_por_cuenta=None, ventana=None):
    """Trae lo que hay en las agendas de Google. Devuelve un resumen por cuenta."""
    desde = (datetime.now(timezone.utc) - timedelta(days=(ventana or DIAS_ATRAS)))
    hasta = (datetime.now(timezone.utc) + timedelta(days=DIAS_ADELANTE))
    nuestros = _ids_de_la_plataforma(app)
    resumen = {}

    for cuenta in cuentas:
        creds = app.obtener_creds_google(cuenta) if hasattr(app, 'obtener_creds_google') else None
        if creds is None:
            resumen[cuenta] = {'traidos': 0, 'error': 'sin conectar'}
            continue
        try:
            service = build('calendar', 'v3', credentials=creds)
            respuesta = service.events().list(
                calendarId='primary',
                timeMin=desde.isoformat(), timeMax=hasta.isoformat(),
                singleEvents=True, orderBy='startTime',
                showDeleted=True, maxResults=250).execute()
        except Exception as e:
            resumen[cuenta] = {'traidos': 0, 'error': str(e)[:150]}
            continue

        filas = []
        for ev in respuesta.get('items', []):
            if ev.get('id') in nuestros:
                continue                    # esto salió de aquí; ya está en el calendario
            inicio, todo_el_dia = _hora(ev.get('start'))
            fin, _ = _hora(ev.get('end'))
            if not inicio:
                continue
            organizador = (ev.get('organizer') or {})
            filas.append({
                'cuenta_email': cuenta,
                'origen': 'google',
                'event_id': ev['id'],
                'gcal_id': 'primary',
                'calendar_id': (cal_por_cuenta or {}).get(cuenta),
                'titulo': ev.get('summary') or '(sin título)',
                'descripcion': (ev.get('description') or '')[:2000],
                'start_time': inicio,
                'end_time': fin,
                'todo_el_dia': todo_el_dia,
                'lugar': ev.get('location'),
                'enlace': _enlace(ev),
                'organizador': organizador.get('email'),
                'organizador_nombre': organizador.get('displayName'),
                'invitados': ', '.join(a.get('email', '') for a in (ev.get('attendees') or [])
                                       if a.get('email'))[:1000] or None,
                'mi_respuesta': _mi_respuesta(ev),
                'estado': 'cancelado' if ev.get('status') == 'cancelled' else 'activo',
                'actualizado_en': datetime.now(timezone.utc).isoformat(),
            })
        resumen[cuenta] = {'traidos': _guardar(app, filas), 'error': None}
    return resumen


# ============================================================
#  CORREO (IMAP) — las cuentas de Microsoft
# ============================================================
def _desplegar(texto):
    """Deshace el plegado del RFC 5545: una línea partida con un espacio al
    principio de la siguiente es la misma línea."""
    return re.sub(r'\r?\n[ \t]', '', texto)


def _campos_ics(texto):
    """Parser mínimo del primer VEVENT. No pretende ser una implementación
    completa del formato: sólo saca lo que hace falta para poder enseñar el
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
    cadena = b64encode(f'user={cuenta}\x01auth=Bearer {token}\x01\x01'.encode()).decode()
    try:
        conexion = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        conexion.authenticate('XOAUTH2', lambda _: cadena.encode())
        return conexion, None
    except Exception as e:
        return None, f'{cuenta}: no se pudo abrir la bandeja ({str(e)[:150]})'


def sincronizar_correo(app, cuentas, cal_por_cuenta=None, dias=None):
    """Lee las invitaciones que llegaron por correo a las cuentas de Microsoft."""
    resumen = {}
    desde = (datetime.now(timezone.utc) - timedelta(days=dias or DIAS_CORREO))
    criterio = desde.strftime('%d-%b-%Y')

    for cuenta in cuentas:
        conexion, error = _conectar_imap(app, cuenta)
        if error:
            resumen[cuenta] = {'traidos': 0, 'error': error}
            continue
        filas = []
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
                fila = _fila_desde_mensaje(
                    _email.message_from_bytes(cuerpo[0][1]), cuenta,
                    (cal_por_cuenta or {}).get(cuenta))
                if fila:
                    filas.append(fila)
        except Exception as e:
            resumen[cuenta] = {'traidos': _guardar(app, filas), 'error': str(e)[:150]}
            try: conexion.logout()
            except Exception: pass
            continue
        try:
            conexion.close(); conexion.logout()
        except Exception:
            pass
        resumen[cuenta] = {'traidos': _guardar(app, filas), 'error': None}
    return resumen


def _fila_desde_mensaje(mensaje, cuenta, calendar_id):
    """Saca el evento de un correo, si es que lo lleva. Devuelve None cuando no
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

    metodo = (campos.get('METHOD') or '').upper()
    estado_ics = (_valor(campos, 'STATUS') or '').upper()
    cancelado = metodo == 'CANCEL' or estado_ics == 'CANCELLED'

    organizador = _correo_de(_valor(campos, 'ORGANIZER'))
    # Lo que uno mismo convoca desde su Outlook vuelve a su propia bandeja como
    # copia. No es «lo que me agendan» y no se guarda como tal.
    if organizador and organizador == cuenta.lower() and not cancelado:
        return None

    return {
        'cuenta_email': cuenta,
        'origen': 'correo',
        'event_id': uid,
        'calendar_id': calendar_id,
        'titulo': _desescapar(_valor(campos, 'SUMMARY')) or '(sin título)',
        'descripcion': _desescapar(_valor(campos, 'DESCRIPTION'))[:2000] or None,
        'start_time': inicio,
        'end_time': fin,
        'todo_el_dia': todo_el_dia,
        'lugar': _desescapar(_valor(campos, 'LOCATION')) or None,
        'organizador': organizador,
        'organizador_nombre': (mensaje.get('From') or '')[:200] or None,
        'estado': 'cancelado' if cancelado else 'activo',
        'actualizado_en': datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
#  LO QUE VE LA PLATAFORMA
# ============================================================
def eventos(app, cuentas=None, desde=None, hasta=None, incluir_cancelados=False):
    """Lo agendado por otros, listo para pintar en el calendario."""
    filtros = {}
    if desde:
        filtros['start_time'] = f'gte.{desde}'
    if hasta:
        filtros['end_time'] = f'lte.{hasta}'
    if not incluir_cancelados:
        filtros['estado'] = 'eq.activo'
    filtros['order'] = 'start_time.asc'
    try:
        filas = app.supabase.get_q('agenda_externa', filtros,
            select='id,cuenta_email,calendar_id,titulo,descripcion,start_time,end_time,'
                   'todo_el_dia,lugar,enlace,organizador,organizador_nombre,invitados,'
                   'mi_respuesta,estado,visto,appointment_id,origen') or []
    except Exception as e:
        print(f'[agenda-entrante] no se pudo leer: {e}')
        return []
    if cuentas is None:
        return filas
    permitidas = {c.lower() for c in cuentas}
    return [f for f in filas if (f.get('cuenta_email') or '').lower() in permitidas]


def resumen(app):
    """Cuántas cosas han entrado y cuántas están sin mirar. Es lo que se enseña
    en la pantalla de cuentas para saber si el puente está vivo."""
    salida = {'total': 0, 'sin_ver': 0, 'proximos': 0, 'por_cuenta': {}}
    try:
        filas = app.supabase.get('agenda_externa',
            select='cuenta_email,visto,estado,start_time') or []
    except Exception:
        return salida
    ahora = datetime.now(timezone.utc).isoformat()
    for f in filas:
        if f.get('estado') != 'activo':
            continue
        salida['total'] += 1
        cuenta = f.get('cuenta_email') or '?'
        salida['por_cuenta'][cuenta] = salida['por_cuenta'].get(cuenta, 0) + 1
        if not f.get('visto'):
            salida['sin_ver'] += 1
        if (f.get('start_time') or '') >= ahora:
            salida['proximos'] += 1
    return salida


# ============================================================
#  LA PASADA COMPLETA
# ============================================================
def sincronizar(app, cuentas_google, cuentas_microsoft, cal_por_cuenta=None):
    """Una pasada por todas las cuentas. Lo que falle en una no detiene el resto:
    que csccue no conteste no puede dejar sin agenda a las otras ocho."""
    resultado = {'google': {}, 'correo': {}}
    if cuentas_google:
        resultado['google'] = sincronizar_google(app, cuentas_google, cal_por_cuenta)
    if cuentas_microsoft:
        resultado['correo'] = sincronizar_correo(app, cuentas_microsoft, cal_por_cuenta)
    traidos = sum(v.get('traidos', 0) for v in
                  list(resultado['google'].values()) + list(resultado['correo'].values()))
    errores = {c: v['error'] for v in (resultado['google'], resultado['correo'])
               for c, v in v.items() if v.get('error')}
    resultado['traidos'] = traidos
    resultado['errores'] = errores
    return resultado


def arrancar_autosync(app, interval_min=15):
    """Revisa las agendas ajenas en segundo plano.

    Cada cuarto de hora, no cada dos minutos: esto sale a la red por nueve
    cuentas y lo que se gana bajando el intervalo es enterarse antes de algo
    que, si es de hoy mismo, ya llegó además por correo. Como el resto de
    trabajos de fondo, un solo worker se lo queda con flock."""
    try:
        import fcntl
    except Exception:
        print('[agenda-entrante] fcntl no disponible (dev local): sincronización desactivada')
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
                app.sincronizar_agenda_entrante()
            except Exception as e:
                print(f'[agenda-entrante] {e}')
            time.sleep(interval_min * 60)

    threading.Thread(target=_bucle, name='agenda-entrante', daemon=True).start()
    print(f'[agenda-entrante] activo (cada {interval_min} min)')
