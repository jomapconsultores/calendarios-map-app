# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""La cita sale de la cuenta que corresponde, aunque esa cuenta no sea Google.

Las cuentas de Gmail agendan con la API de Google Calendar: la plataforma crea
el evento y Google se encarga de avisar. Las de Microsoft —maposligua@hotmail
y marcoposligua@csccue.gob.ec— no pueden hacer eso sin registrar una aplicación
propia en Entra a nombre de la institución, que no es algo que dependa de aquí.

Lo que sí se puede, y es lo que hace este módulo, es lo que lleva haciendo el
correo desde antes de que existieran las APIs: mandar la cita como INVITACIÓN.
Un mensaje con un adjunto `text/calendar` y `METHOD:REQUEST` no es un correo
que hable de una reunión: es la reunión. Outlook, Gmail y el móvil lo entienden
igual, lo meten en el calendario del que lo recibe y le ofrecen aceptar o
rechazar. Y, sobre todo, sale DESDE la dirección que corresponde: quien la
recibe ve al despacho o a la institución, no una cuenta personal.

Lo que este camino no da —y conviene no fingir que sí— es el acuse: por la API
de Google se sabe quién aceptó; por correo, sólo que la invitación salió. La
respuesta llega como un mensaje más a la bandeja, y de leerla se encarga
`agenda_entrante`.

Autorización de las cuentas de Microsoft: OAuth por código de dispositivo. La
contraseña de aplicación ya no existe para estas cuentas, y el flujo normal de
navegador exige una URL de retorno registrada. El código de dispositivo no: se
enseña un código en pantalla, una persona lo teclea una vez en microsoft.com y
a partir de ahí el `refresh_token` se renueva solo.
"""
import os
import smtplib
import ssl
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

import requests

# Cliente público de Thunderbird. Es el mismo que usan los clientes de correo
# de escritorio para hablar con Outlook por IMAP/SMTP, y el que ya usa la
# herramienta local de correo. Se puede sustituir por uno propio registrado en
# Entra poniendo MS_CLIENT_ID en el entorno.
MS_CLIENT_ID = os.getenv('MS_CLIENT_ID', '9e5f94bc-e8a4-4e73-b8be-63364c29d753')

# SMTP.Send para mandar la invitación, IMAP para leer lo que contestan y lo que
# convocan otros. `offline_access` es lo que devuelve el refresh_token: sin él
# habría que volver a autorizar a mano cada hora.
MS_SCOPES = ('offline_access '
             'https://outlook.office.com/SMTP.Send '
             'https://outlook.office.com/IMAP.AccessAsUser.All')

AUTORIDAD_POR_DEFECTO = 'https://login.microsoftonline.com/organizations'

SMTP_HOST = 'smtp.office365.com'
SMTP_PORT = 587

TZ_NOMBRE = 'America/Guayaquil'


# ============================================================
#  TOKENS DE MICROSOFT
# ============================================================
def _fila_token(app, email):
    filas = app.supabase.get('ms_tokens', {'email': email},
                             select='id,email,refresh_token,access_token,token_expiry,authority')
    return filas[0] if filas else None


def _guardar_token(app, email, datos, authority=None):
    """Guarda lo que devolvió Microsoft. Conserva el refresh_token anterior si
    la respuesta no trae uno nuevo: Microsoft no siempre lo rota, y machacarlo
    con un vacío dejaría la cuenta sin poder renovarse."""
    fila = _fila_token(app, email)
    expiry = None
    if datos.get('expires_in'):
        expiry = datetime.now(timezone.utc).timestamp() + int(datos['expires_in']) - 60
        expiry = datetime.fromtimestamp(expiry, timezone.utc).isoformat()
    nuevo = {
        'email': email,
        'access_token': datos.get('access_token'),
        'refresh_token': datos.get('refresh_token') or (fila or {}).get('refresh_token'),
        'token_expiry': expiry,
        'authority': authority or (fila or {}).get('authority') or AUTORIDAD_POR_DEFECTO,
        'actualizado_en': datetime.now(timezone.utc).isoformat(),
    }
    if fila:
        return app.supabase.update('ms_tokens', fila['id'], nuevo)
    return bool(app.supabase.insert('ms_tokens', nuevo))


def token_de_acceso(app, email):
    """Un access_token válido para esa cuenta, o (None, motivo).

    Se renueva cuando quedan menos de dos minutos: pedirlo justo en el límite
    llevaba a que caducara entre que se pide y que el servidor SMTP lo valida.
    """
    fila = _fila_token(app, email)
    if not fila:
        return None, f'{email} no está autorizada todavía'
    if not fila.get('refresh_token'):
        return None, f'{email} está autorizada sin permiso de renovación; vuelve a conectarla'

    vigente = fila.get('access_token')
    if vigente and fila.get('token_expiry'):
        try:
            caduca = datetime.fromisoformat(fila['token_expiry'].replace('Z', '+00:00'))
            if caduca > datetime.now(timezone.utc):
                return vigente, None
        except Exception:
            pass

    autoridad = fila.get('authority') or AUTORIDAD_POR_DEFECTO
    try:
        r = requests.post(f'{autoridad}/oauth2/v2.0/token', timeout=20, data={
            'client_id': MS_CLIENT_ID,
            'grant_type': 'refresh_token',
            'refresh_token': fila['refresh_token'],
            'scope': MS_SCOPES,
        })
    except Exception as e:
        return None, f'no se pudo hablar con Microsoft: {str(e)[:150]}'

    datos = {}
    try:
        datos = r.json()
    except Exception:
        pass
    if r.status_code != 200 or not datos.get('access_token'):
        detalle = datos.get('error_description') or r.text[:200]
        # invalid_grant aquí significa que el permiso ya no vale: reintentarlo
        # no lo arregla, hace falta que una persona vuelva a autorizar.
        return None, f'{email}: {detalle[:200]}'
    _guardar_token(app, email, datos, autoridad)
    return datos['access_token'], None


def iniciar_autorizacion(app, email, authority=None):
    """Arranca el código de dispositivo. Devuelve lo que hay que enseñar en
    pantalla: el código, la dirección donde se teclea y cuánto dura."""
    autoridad = authority or _autoridad_sugerida(email)
    try:
        r = requests.post(f'{autoridad}/oauth2/v2.0/devicecode', timeout=20,
                          data={'client_id': MS_CLIENT_ID, 'scope': MS_SCOPES})
        datos = r.json()
    except Exception as e:
        return None, f'no se pudo pedir el código a Microsoft: {str(e)[:150]}'
    if not datos.get('device_code'):
        return None, datos.get('error_description', r.text[:200])
    return {
        'device_code': datos['device_code'],
        'user_code': datos.get('user_code'),
        'verification_uri': datos.get('verification_uri'),
        'expires_in': datos.get('expires_in', 900),
        'interval': datos.get('interval', 5),
        'authority': autoridad,
    }, None


def completar_autorizacion(app, email, device_code, authority=None):
    """Pregunta si ya tecleó el código. NO espera: devuelve 'pendiente' y quien
    llama vuelve a preguntar. Bloquear aquí dejaría colgado un worker de
    gunicorn durante los quince minutos que dura el código."""
    autoridad = authority or _autoridad_sugerida(email)
    try:
        r = requests.post(f'{autoridad}/oauth2/v2.0/token', timeout=20, data={
            'client_id': MS_CLIENT_ID,
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            'device_code': device_code,
        })
        datos = r.json()
    except Exception as e:
        return 'error', f'no se pudo hablar con Microsoft: {str(e)[:150]}'
    if datos.get('access_token'):
        _guardar_token(app, email, datos, autoridad)
        return 'ok', None
    error = datos.get('error', '')
    if error in ('authorization_pending', 'slow_down'):
        return 'pendiente', None
    return 'error', datos.get('error_description', error)[:200]


def _autoridad_sugerida(email):
    """Las cuentas personales (hotmail, outlook.com, live) viven en /consumers;
    las de organización, en /organizations. Acertar de primeras ahorra el error
    más común al conectar."""
    dominio = (email or '').split('@')[-1].lower()
    if dominio in ('hotmail.com', 'outlook.com', 'live.com', 'msn.com', 'hotmail.es'):
        return 'https://login.microsoftonline.com/consumers'
    return AUTORIDAD_POR_DEFECTO


# ============================================================
#  EL ARCHIVO DE CALENDARIO
# ============================================================
def _escapar(texto):
    """Un punto y coma sin escapar parte el campo en dos y el que recibe la
    invitación ve la mitad del asunto."""
    return (str(texto or '')
            .replace('\\', '\\\\').replace(';', r'\;')
            .replace(',', r'\,').replace('\r\n', r'\n').replace('\n', r'\n'))


def _plegar(linea):
    """Corta a 75 octetos con continuación por espacio, como manda el RFC 5545.

    Se cuenta en BYTES, no en caracteres: partir un acento por la mitad deja el
    archivo ilegible para el que lo abre."""
    crudo = linea.encode('utf-8')
    if len(crudo) <= 75:
        return linea
    trozos, actual = [], b''
    for byte in [crudo[i:i + 1] for i in range(len(crudo))]:
        if len(actual) >= 74:
            trozos.append(actual)
            actual = b' '
        actual += byte
    trozos.append(actual)
    return '\r\n'.join(t.decode('utf-8', 'ignore') for t in trozos)


def _utc(valor):
    """ISO de Supabase → 20260910T140000Z. En UTC a propósito: evita tener que
    embarcar una definición de zona horaria que cada cliente interpreta a su
    manera."""
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(str(valor).replace('Z', '+00:00'))
    except Exception:
        return None
    if d.tzinfo is None:
        import pytz
        d = pytz.timezone(TZ_NOMBRE).localize(d)
    return d.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def uid_de_la_cita(apt):
    """El mismo UID toda la vida de la cita. Es lo que hace que una
    actualización ACTUALICE en el calendario del otro en vez de aparecer como
    una segunda reunión a la misma hora."""
    return f"cita-{apt.get('id') or uuid.uuid4()}@calendario.map"


def construir_ics(apt, organizador, destinatarios, metodo='REQUEST', secuencia=0):
    """El .ics de la cita. `metodo` es REQUEST para convocar y actualizar, y
    CANCEL para retirarla."""
    inicio, fin = _utc(apt.get('start_time')), _utc(apt.get('end_time'))
    descripcion = _descripcion(apt)
    lugar = apt.get('meeting_link') or _lugar(apt)

    lineas = [
        'BEGIN:VCALENDAR',
        'PRODID:-//Calendario MAP//Agenda//ES',
        'VERSION:2.0',
        'CALSCALE:GREGORIAN',
        f'METHOD:{metodo}',
        'BEGIN:VEVENT',
        f'UID:{uid_de_la_cita(apt)}',
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f'SEQUENCE:{int(secuencia or 0)}',
    ]
    if inicio:
        lineas.append(f'DTSTART:{inicio}')
    if fin:
        lineas.append(f'DTEND:{fin}')
    lineas.append(f"SUMMARY:{_escapar(_asunto(apt))}")
    if descripcion:
        lineas.append(f'DESCRIPTION:{_escapar(descripcion)}')
    if lugar:
        lineas.append(f'LOCATION:{_escapar(lugar)}')
    lineas.append(f'ORGANIZER;CN={_escapar(organizador)}:mailto:{organizador}')
    for correo in destinatarios:
        lineas.append(
            'ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;'
            f'PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{correo}')
    lineas.append('STATUS:' + ('CANCELLED' if metodo == 'CANCEL' else 'CONFIRMED'))
    if metodo != 'CANCEL':
        lineas += ['BEGIN:VALARM', 'TRIGGER:-PT30M', 'ACTION:DISPLAY',
                   'DESCRIPTION:Recordatorio', 'END:VALARM']
    lineas += ['END:VEVENT', 'END:VCALENDAR']
    return '\r\n'.join(_plegar(l) for l in lineas) + '\r\n'


def _asunto(apt):
    encargado = apt.get('encargado') or ''
    titulo = apt.get('title') or 'Cita'
    return f'{titulo} - {encargado}' if encargado else titulo


def _lugar(apt):
    partes = [apt.get('lugar'), apt.get('direccion'), apt.get('ciudad')]
    return ', '.join(p for p in partes if p)


def _descripcion(apt):
    lineas = [f"Titulo: {apt.get('title', '')}",
              f"Encargado: {apt.get('encargado', '')}",
              f"Tema: {apt.get('tema', '')}"]
    if apt.get('client_name'):
        lineas.append(f"Cliente: {apt['client_name']}")
    if apt.get('meeting_link'):
        lineas.append(f"Enlace de reunion: {apt['meeting_link']}")
    else:
        if apt.get('lugar'):     lineas.append(f"Lugar: {apt['lugar']}")
        if apt.get('direccion'): lineas.append(f"Direccion: {apt['direccion']}")
        if apt.get('ciudad'):    lineas.append(f"Ciudad: {apt['ciudad']}, Ecuador")
        if apt.get('mapa'):      lineas.append(f"Mapa: {apt['mapa']}")
    if apt.get('notes'):
        lineas.append(f"Notas: {apt['notes']}")
    return '\n'.join(lineas)


def _cuerpo_html(apt, metodo):
    import html as _h
    cabecera = ('Se ha CANCELADO la siguiente cita' if metodo == 'CANCEL'
                else 'Se le convoca a la siguiente cita')
    filas = []
    for etiqueta, valor in (
            ('Asunto',   _asunto(apt)),
            ('Tema',     apt.get('tema')),
            ('Cuándo',   _cuando_legible(apt)),
            ('Dónde',    apt.get('meeting_link') or _lugar(apt)),
            ('Cliente',  apt.get('client_name')),
            ('Notas',    apt.get('notes'))):
        if valor:
            filas.append(f'<tr><td style="padding:4px 12px 4px 0;color:#6b7280;">'
                         f'{_h.escape(etiqueta)}</td>'
                         f'<td style="padding:4px 0;">{_h.escape(str(valor))}</td></tr>')
    return (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:14px;">'
            f'<p>{cabecera}:</p><table>{"".join(filas)}</table>'
            f'<p style="color:#6b7280;font-size:12px;">Esta invitación se puede aceptar '
            f'desde el propio calendario del correo.</p></div>')


def _cuando_legible(apt):
    try:
        import pytz
        tz = pytz.timezone(TZ_NOMBRE)
        d = datetime.fromisoformat(str(apt.get('start_time')).replace('Z', '+00:00'))
        h = datetime.fromisoformat(str(apt.get('end_time')).replace('Z', '+00:00'))
        return (f"{d.astimezone(tz).strftime('%d/%m/%Y %H:%M')} a "
                f"{h.astimezone(tz).strftime('%H:%M')}")
    except Exception:
        return str(apt.get('start_time') or '')


# ============================================================
#  MANDARLA
# ============================================================
def destinatarios_de(apt, email_map, organizador):
    """A quién va la invitación: el contacto del calendario y los invitados de
    la ficha, menos la propia cuenta que convoca."""
    vistos = {(organizador or '').strip().lower()}
    salida = []
    def _add(correo):
        c = (correo or '').strip()
        if c and '@' in c and c.lower() not in vistos:
            vistos.add(c.lower())
            salida.append(c)
    _add(email_map.get(apt.get('calendar_id', '')))
    for inv in (apt.get('invitados') or '').split(','):
        _add(inv)
    _add(apt.get('client_email'))
    return salida


def _conectar_smtp(app, cuenta):
    token, error = token_de_acceso(app, cuenta)
    if not token:
        return None, error
    cadena = b64encode(f'user={cuenta}\x01auth=Bearer {token}\x01\x01'.encode()).decode()
    try:
        servidor = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25)
        servidor.starttls(context=ssl.create_default_context())
        servidor.ehlo()
        servidor.docmd('AUTH', 'XOAUTH2 ' + cadena)
        return servidor, None
    except Exception as e:
        return None, f'no se pudo entrar en {cuenta}: {str(e)[:180]}'


def enviar_invitacion(app, apt, cuenta, email_map, metodo='REQUEST', secuencia=0):
    """Manda la cita como invitación desde `cuenta`. Devuelve (nº destinatarios, error).

    Nunca lanza excepción: que falle el correo no puede tumbar la aprobación de
    la cita, que ya está guardada. Lo que sí hace es DECIR que falló, para que
    quien aprobó sepa que el otro no se ha enterado."""
    destinos = destinatarios_de(apt, email_map, cuenta)
    if not destinos:
        return 0, 'la cita no tiene a quién invitar'

    ics = construir_ics(apt, cuenta, destinos, metodo=metodo, secuencia=secuencia)
    asunto = ('Cancelada: ' if metodo == 'CANCEL' else '') + _asunto(apt)

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = formataddr((apt.get('encargado') or 'Agenda', cuenta))
    msg['To'] = ', '.join(destinos)
    msg.set_content(_descripcion(apt))
    msg.add_alternative(_cuerpo_html(apt, metodo), subtype='html')
    # El text/calendar va como ALTERNATIVA, no sólo como adjunto: así el cliente
    # de correo lo reconoce como una invitación y ofrece aceptar o rechazar, en
    # lugar de enseñar un archivo que hay que abrir a mano.
    msg.add_alternative(ics, subtype='calendar',
                        params={'method': metodo, 'charset': 'UTF-8',
                                'component': 'VEVENT'})
    msg.add_attachment(ics.encode('utf-8'), maintype='application',
                       subtype='ics', filename='invite.ics')

    servidor, error = _conectar_smtp(app, cuenta)
    if error:
        return 0, error
    try:
        with servidor:
            servidor.send_message(msg)
        return len(destinos), None
    except Exception as e:
        print(f'[invitaciones] {cuenta}: {e}')
        return 0, str(e)[:200]


def enviar_cancelacion(app, apt, cuenta, email_map, secuencia=1):
    """La otra mitad: retirar del calendario ajeno una cita que ya no existe."""
    return enviar_invitacion(app, apt, cuenta, email_map,
                             metodo='CANCEL', secuencia=secuencia)


def cuentas_microsoft(app):
    """Las cuentas de Microsoft que agendan, con su estado de autorización."""
    try:
        filas = app.supabase.get('ms_tokens', select='email,refresh_token,token_expiry') or []
    except Exception:
        return []
    return [{'email': f['email'], 'conectada': bool(f.get('refresh_token')),
             'expiry': f.get('token_expiry')} for f in filas]
