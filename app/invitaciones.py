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
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr

import requests

# Cliente público de Thunderbird. Es el mismo que usan los clientes de correo
# de escritorio para hablar con Outlook por IMAP/SMTP, y el que ya usa la
# herramienta local de correo. Se puede sustituir por uno propio registrado en
# Entra poniendo MS_CLIENT_ID en el entorno.
MS_CLIENT_ID = os.getenv('MS_CLIENT_ID', '9e5f94bc-e8a4-4e73-b8be-63364c29d753')

# Microsoft emite un token POR RECURSO, y los recursos no se mezclan en una
# misma petición: un token que vale para Graph no vale para SMTP ni para IMAP.
# Por eso hay dos juegos de permisos y no uno.
#
#   graph     mandar el correo por la API (Mail.Send). Es el camino bueno: no
#             pasa por el puerto 587 y por tanto no le afecta el interruptor
#             del tenant que apaga el SMTP autenticado.
#   outlook   IMAP para leer lo que contestan y lo que convocan otros, y SMTP
#             como salida de respaldo donde todavía esté permitida.
#
# `offline_access` es lo que devuelve el refresh_token: sin él habría que
# volver a autorizar a mano cada hora. Ambos juegos son ajustables por entorno
# para poder corregir un permiso sin tocar el código.
MS_SCOPES_GRAPH = os.getenv(
    'MS_SCOPES_GRAPH',
    'offline_access https://graph.microsoft.com/Mail.Send')

MS_SCOPES = os.getenv(
    'MS_SCOPES',
    'offline_access '
    'https://outlook.office.com/SMTP.Send '
    'https://outlook.office.com/IMAP.AccessAsUser.All')

RECURSOS = {'graph': MS_SCOPES_GRAPH, 'outlook': MS_SCOPES}

AUTORIDAD_POR_DEFECTO = 'https://login.microsoftonline.com/organizations'

SMTP_HOST = 'smtp.office365.com'
SMTP_PORT = 587

GRAPH_SENDMAIL = 'https://graph.microsoft.com/v1.0/me/sendMail'
GRAPH_SENDMAIL_BUZON = 'https://graph.microsoft.com/v1.0/users/{}/sendMail'

# ------------------------------------------------------------
#  Mandar sin nadie detrás
# ------------------------------------------------------------
# Todo lo de arriba es permiso DELEGADO: la aplicación actúa en nombre de una
# persona, y ese permiso sólo puede nacer de alguien tecleando un código. Eso
# no se automatiza —es el diseño de OAuth— y significa que cada cuenta hay que
# conectarla a mano y volver a conectarla el día que el permiso se caiga.
#
# Con credenciales de cliente no hay persona: la aplicación se identifica con
# su propio secreto, pide un token para sí misma y manda por el buzón que le
# digan. No hay código que teclear, no hay refresh_token que se caiga, no hay
# nada que reconectar. Es lo que hace que las cuentas del dominio funcionen
# solas desde el primer arranque.
#
# El precio es que ese permiso lo tiene que consentir un administrador del
# dominio, y que `Mail.Send` de aplicación alcanza a TODOS los buzones del
# tenant mientras no se limite con una ApplicationAccessPolicy. Está explicado
# en .env.example, porque es lo que hay que pedirle a quien administra.
#
# Sin estas tres variables no cambia nada: se sigue por el camino delegado.
MS_TENANT_ID = os.getenv('MS_TENANT_ID', '').strip()
MS_CLIENT_SECRET = os.getenv('MS_CLIENT_SECRET', '').strip()

# El permiso de aplicación no se pide por nombre: se piden «los que la
# aplicación tenga consentidos», que es lo que significa /.default.
SCOPE_APLICACION = 'https://graph.microsoft.com/.default'

# A qué cuentas alcanza. Vacío = a todas las de organización. Las personales
# quedan fuera SIEMPRE: hotmail, outlook.com y compañía no viven en ningún
# tenant, y contra ellas las credenciales de cliente no existen.
MS_DOMINIOS_SOLOS = [x.strip().lower() for x
                     in os.getenv('MS_DOMINIOS_SOLOS', '').split(',') if x.strip()]

# El final de línea del correo, que no es el del sistema donde corra esto.
CRLF = '\r\n'

# Lo que contesta Exchange Online cuando el tenant tiene apagado el SMTP
# autenticado. Conviene reconocerlo porque NO es un problema de permiso ni de
# contraseña —el token es correcto— sino una política del dominio, y decir
# «autenticación fallida» manda a quien lo lee a revisar lo que está bien.
SMTP_APAGADO = ('smtpclientauthentication is disabled', '5.7.139')

TZ_NOMBRE = 'America/Guayaquil'


# ============================================================
#  TOKENS DE MICROSOFT
# ============================================================
def _fila_token(app, email):
    filas = app.supabase.get('ms_tokens', {'email': email},
                             select='id,email,refresh_token,access_token,token_expiry,authority')
    return filas[0] if filas else None


def _guardar_token(app, email, datos, authority=None, recurso='outlook'):
    """Guarda lo que devolvió Microsoft. Conserva el refresh_token anterior si
    la respuesta no trae uno nuevo: Microsoft no siempre lo rota, y machacarlo
    con un vacío dejaría la cuenta sin poder renovarse.

    El access_token que se guarda es SIEMPRE el de Outlook, porque es el que
    lee el IMAP entrante. El de Graph dura una hora y vive en memoria: ponerlo
    aquí dejaría la lectura de la bandeja entrando con un token de otro
    recurso, que Outlook rechaza sin decir por qué."""
    fila = _fila_token(app, email)
    ahora = datetime.now(timezone.utc)
    expiry = None
    if datos.get('expires_in'):
        expiry = ahora.timestamp() + int(datos['expires_in']) - 60
        expiry = datetime.fromtimestamp(expiry, timezone.utc).isoformat()
    nuevo = {
        'email': email,
        'access_token': datos.get('access_token'),
        'refresh_token': datos.get('refresh_token') or (fila or {}).get('refresh_token'),
        'token_expiry': expiry,
        'authority': authority or (fila or {}).get('authority') or AUTORIDAD_POR_DEFECTO,
        'actualizado_en': ahora.isoformat(),
    }
    if recurso == 'graph':
        dura = int(datos.get('expires_in') or 3600) - 120
        _TOKENS_GRAPH[email] = (datos.get('access_token'),
                                ahora + timedelta(seconds=max(dura, 60)))
        nuevo['access_token'] = (fila or {}).get('access_token')
        nuevo['token_expiry'] = (fila or {}).get('token_expiry')
    if fila:
        app.supabase.update('ms_tokens', fila['id'], nuevo)
    else:
        app.supabase.insert('ms_tokens', nuevo)
    # Y se vuelve a leer. Guardar «sin error» no es lo mismo que haber guardado:
    # si la escritura no llega —permisos, una columna que falta, la tabla que no
    # existe— quien autorizó veía «cuenta autorizada», la pantalla seguía
    # diciendo SIN AUTORIZAR, y no había forma de saber cuál de las dos mentía.
    # Lo que decide es que la fila esté y tenga con qué renovarse.
    guardada = _fila_token(app, email)
    return bool(guardada and guardada.get('refresh_token'))


# Lo que Microsoft contesta cuando el permiso ya no sirve. Son varios códigos y
# ninguno se explica solo:
#
#   invalid_grant   el permiso se retiró, o caducó por no usarse
#   AADSTS70000     el token no vale para esta aplicación — es lo que pasa con
#                   un refresh_token traído de otro sitio, emitido con otro
#                   `client_id`: no hay forma de convertirlo, hay que autorizar
#                   otra vez desde aquí
#   AADSTS50173     cambió la contraseña de la cuenta y los permisos se cayeron
#   AADSTS700082    llevaba tanto sin usarse que Microsoft lo dio por muerto
#
# Todos acaban en lo mismo, y eso es lo único que hace falta decir.
CODIGOS_DE_RECONECTAR = ('invalid_grant', 'aadsts70000', 'aadsts50173',
                         'aadsts700082', 'aadsts700084')


def hay_que_volver_a_conectar(datos, detalle=''):
    """Si lo que contestó Microsoft significa «esta cuenta hay que autorizarla
    otra vez», y no «vuelve a intentarlo en un rato»."""
    texto = ('%s %s' % ((datos or {}).get('error') or '', detalle or '')).lower()
    return any(c in texto for c in CODIGOS_DE_RECONECTAR)


# Lo que contesta Microsoft cuando la cuenta está autorizada pero NO para lo
# que se le está pidiendo: el permiso existe en la aplicación y nadie lo ha
# consentido todavía. Le pasa a toda cuenta conectada antes de que existiera el
# envío por Graph: tiene dado el permiso de SMTP e IMAP, y ninguno de Graph.
CODIGOS_DE_FALTA_PERMISO = ('aadsts65001', 'consent_required', 'invalid_scope',
                            'aadsts70011', 'aadsts900144')


def falta_consentir(datos, detalle=''):
    """Si lo que contestó Microsoft significa «este permiso no está dado», y no
    «el permiso se cayó»: uno se arregla autorizando lo que falta, el otro
    volviendo a conectar la cuenta entera."""
    texto = ('%s %s' % ((datos or {}).get('error') or '', detalle or '')).lower()
    return any(c in texto for c in CODIGOS_DE_FALTA_PERMISO)


# El token de Graph no cabe en `ms_tokens`: esa fila guarda UN access_token y
# es el de Outlook, el que usa el IMAP entrante. Escribir encima el de Graph
# dejaría la lectura de la bandeja intentando entrar con un token que no vale
# para ella. Como dura una hora y los envíos son sueltos, vive en memoria del
# proceso y basta: lo peor que pasa al reiniciar es pedir uno nuevo.
_TOKENS_GRAPH = {}


def _canjear(email, fila, recurso):
    """Cambia el refresh_token por un access_token del recurso que pidan.

    El refresh_token no está atado a un recurso: el mismo sirve para Graph y
    para Outlook, siempre que la cuenta haya consentido el permiso de cada uno.
    """
    autoridad = fila.get('authority') or AUTORIDAD_POR_DEFECTO
    try:
        r = requests.post(f'{autoridad}/oauth2/v2.0/token', timeout=20, data={
            'client_id': MS_CLIENT_ID,
            'grant_type': 'refresh_token',
            'refresh_token': fila['refresh_token'],
            'scope': RECURSOS.get(recurso, MS_SCOPES),
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
        # Primero lo que falta por consentir, y sólo después lo que se cayó.
        # Los dos llegan como `invalid_grant` y decir «el permiso ya no vale»
        # cuando lo que pasa es que ese permiso NUNCA se pidió manda a revisar
        # una cuenta que está perfectamente conectada para lo demás.
        if falta_consentir(datos, detalle):
            if recurso == 'graph':
                return None, (f'{email}: falta autorizar el envío por Graph. '
                              'Entra en Cuentas y pulsa Conectar (son dos '
                              'códigos; el primero es ése).')
            return None, (f'{email}: falta autorizar la lectura del correo. '
                          'Entra en Cuentas y pulsa Conectar.')
        # Cuando el permiso ya no vale, reintentarlo no lo arregla: hace falta
        # que una persona vuelva a autorizar. Se dice así, y no con el código de
        # Microsoft, que estaba saliendo tal cual en la pantalla de cuentas:
        # «AADSTS70000: The provided value for the input parameter
        # 'refresh_token' or 'assertion' is not valid» no le dice a nadie que lo
        # que tiene que hacer es pulsar Conectar.
        if hay_que_volver_a_conectar(datos, detalle):
            return None, (f'{email}: hay que volver a conectarla — el permiso '
                          f'ya no vale. Entra en Cuentas y pulsa Conectar.')
        return None, f'{email}: {detalle[:200]}'
    return datos, None


def token_de_acceso(app, email, recurso='outlook'):
    """Un access_token válido para esa cuenta y ese recurso, o (None, motivo).

    `recurso` es 'outlook' —SMTP e IMAP, que es lo que espera quien llama sin
    decir nada— o 'graph', para mandar el correo por la API.

    Se renueva cuando quedan menos de dos minutos: pedirlo justo en el límite
    llevaba a que caducara entre que se pide y que el servidor SMTP lo valida.
    """
    fila = _fila_token(app, email)
    if not fila:
        return None, f'{email} no está autorizada todavía'
    if not fila.get('refresh_token'):
        return None, f'{email} está autorizada sin permiso de renovación; vuelve a conectarla'

    ahora = datetime.now(timezone.utc)
    if recurso == 'graph':
        vivo, hasta = _TOKENS_GRAPH.get(email, (None, None))
        if vivo and hasta and hasta > ahora:
            return vivo, None
    else:
        vigente = fila.get('access_token')
        if vigente and fila.get('token_expiry'):
            try:
                caduca = datetime.fromisoformat(fila['token_expiry'].replace('Z', '+00:00'))
                if caduca > ahora:
                    return vigente, None
            except Exception:
                pass

    datos, error = _canjear(email, fila, recurso)
    if error:
        return None, error

    if recurso == 'graph':
        dura = int(datos.get('expires_in') or 3600) - 120
        _TOKENS_GRAPH[email] = (datos['access_token'],
                                ahora + timedelta(seconds=max(dura, 60)))
        # El access_token de Graph no se escribe en la fila, pero el
        # refresh_token sí: Microsoft lo rota, y quedarse con el viejo es
        # empezar a contar los días hasta que la cuenta se caiga sola.
        if datos.get('refresh_token'):
            try:
                app.supabase.update('ms_tokens', fila['id'], {
                    'refresh_token': datos['refresh_token'],
                    'actualizado_en': ahora.isoformat(),
                })
            except Exception as e:
                print(f'[invitaciones] no se pudo guardar el refresh de {email}: {e}')
    else:
        _guardar_token(app, email, datos, fila.get('authority') or AUTORIDAD_POR_DEFECTO)
    return datos['access_token'], None


# ------------------------------------------------------------
#  El trámite a medio hacer
# ------------------------------------------------------------
# El código de dispositivo vivía en la sesión del navegador de quien lo pedía, y
# eso ataba el trámite a una pestaña: recargarla o cerrarla borraba el único
# apunte de qué se estaba autorizando, así que nadie volvía a preguntarle a
# Microsoft si ya lo habían aprobado. Visto desde fuera, la cuenta seguía sin
# autorizar por mucho que Microsoft dijera «ya puede cerrar esta ventana».
def apuntar_pendiente(app, email, device_code, authority, expira_en_segundos,
                      recurso='outlook'):
    caduca = datetime.now(timezone.utc) + timedelta(seconds=int(expira_en_segundos or 900))
    return app.supabase.upsert('ms_autorizaciones', {
        'email': (email or '').strip().lower(),
        'device_code': device_code,
        'authority': authority,
        'recurso': recurso,
        'pedida_en': datetime.now(timezone.utc).isoformat(),
        'expira_en': caduca.isoformat(),
    }, on_conflict='email')


def pendiente(app, email=None):
    """El trámite en curso: el de esa cuenta, o el único que haya vivo.

    Sin `email` se elige el que no haya caducado; si hubiera varios —dos cuentas
    a medio autorizar a la vez— se devuelve el más reciente, que es el que la
    persona tiene delante."""
    filas = app.supabase.get('ms_autorizaciones',
                             {'email': email.strip().lower()} if email else None,
                             select='email,device_code,authority,recurso,expira_en') or []
    ahora = datetime.now(timezone.utc)
    vivas = []
    for f in filas:
        try:
            caduca = datetime.fromisoformat((f.get('expira_en') or '').replace('Z', '+00:00'))
        except Exception:
            caduca = ahora
        if caduca > ahora:
            vivas.append((caduca, f))
    if not vivas:
        return None
    vivas.sort(key=lambda x: x[0], reverse=True)
    return vivas[0][1]


def olvidar_pendiente(app, email):
    return app.supabase.delete('ms_autorizaciones', (email or '').strip().lower(),
                               id_col='email')


def iniciar_autorizacion(app, email, authority=None, recurso='outlook'):
    """Arranca el código de dispositivo. Devuelve lo que hay que enseñar en
    pantalla: el código, la dirección donde se teclea y cuánto dura.

    Se autoriza un recurso cada vez porque Microsoft no deja pedir permisos de
    dos en el mismo trámite. Conectar una cuenta del todo son, por tanto, dos
    códigos seguidos: 'graph' para poder mandar y 'outlook' para poder leer."""
    autoridad = authority or _autoridad_sugerida(email)
    try:
        r = requests.post(f'{autoridad}/oauth2/v2.0/devicecode', timeout=20,
                          data={'client_id': MS_CLIENT_ID,
                                'scope': RECURSOS.get(recurso, MS_SCOPES)})
        datos = r.json()
    except Exception as e:
        return None, f'no se pudo pedir el código a Microsoft: {str(e)[:150]}'
    if not datos.get('device_code'):
        detalle = datos.get('error_description', r.text[:200])
        # Un permiso que la aplicación registrada en Entra no tiene declarado
        # se rechaza aquí, antes de enseñar código alguno. Con el cliente
        # público de Thunderbird —el de por defecto— pasa con Graph: sirve para
        # IMAP y SMTP y no para mandar por la API.
        if recurso == 'graph' and falta_consentir({}, detalle):
            return None, (
                'la aplicación de Microsoft que usa el calendario no tiene el '
                'permiso Mail.Send de Graph. Hay que registrar una propia en '
                'Entra con ese permiso y ponerla en MS_CLIENT_ID. '
                f'({detalle[:120]})')
        return None, detalle
    return {
        'device_code': datos['device_code'],
        'user_code': datos.get('user_code'),
        'verification_uri': datos.get('verification_uri'),
        'expires_in': datos.get('expires_in', 900),
        'interval': datos.get('interval', 5),
        'authority': autoridad,
        'recurso': recurso,
    }, None


def completar_autorizacion(app, email, device_code, authority=None,
                           recurso='outlook'):
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
        if not _guardar_token(app, email, datos, autoridad, recurso):
            return 'error', (
                f'{email}: Microsoft dio el permiso, pero no se pudo guardar en '
                'ms_tokens. Revisa que la tabla exista (migración 033) y que el '
                'servidor pueda escribir en ella; hay que volver a autorizar.')
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
def destinatarios_de(apt, email_map, organizador, incluir_organizador=False):
    """A quién va la invitación: el contacto del calendario y los invitados de
    la ficha, menos la propia cuenta que convoca.

    Salvo en Microsoft, donde el correo es el ÚNICO canal de entrega. En Google
    el evento nace dentro de la cuenta por la API y mandárselo sería duplicarlo;
    pero hotmail y csccue no agendan por API, así que excluir al organizador
    dejaba las citas sin invitados con CERO destinatarios: se aprobaban y no
    llegaban a ninguna parte, ni siquiera al calendario de su propia cuenta."""
    vistos = set() if incluir_organizador else {(organizador or '').strip().lower()}
    salida = []
    if incluir_organizador:
        org = (organizador or '').strip()
        if org and '@' in org:
            vistos.add(org.lower())
            salida.append(org)
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


# ------------------------------------------------------------
#  El permiso que no hay que conectar
# ------------------------------------------------------------
# Un solo token para todo el tenant, no uno por cuenta: la aplicación se
# identifica a sí misma, y el buzón se elige al mandar. Dura una hora y se pide
# otro; no hay refresh_token porque no hay nada que renovar.
_TOKEN_APP = {}


def manda_sola(email):
    """Si esa cuenta puede mandar sin que nadie la haya autorizado."""
    if not (MS_CLIENT_ID and MS_TENANT_ID and MS_CLIENT_SECRET):
        return False
    dominio = (email or '').split('@')[-1].lower()
    if not dominio:
        return False
    # Las personales no: no pertenecen a ningún tenant, y el permiso de
    # aplicación no llega a ellas por mucho que esté bien configurado.
    if _autoridad_sugerida(email).endswith('/consumers'):
        return False
    return (not MS_DOMINIOS_SOLOS) or dominio in MS_DOMINIOS_SOLOS


def token_de_aplicacion():
    """El token de la aplicación, o (None, motivo). Se reaprovecha hasta que
    le quedan dos minutos, como el de las cuentas."""
    ahora = datetime.now(timezone.utc)
    vivo, hasta = _TOKEN_APP.get('graph', (None, None))
    if vivo and hasta and hasta > ahora:
        return vivo, None
    try:
        r = requests.post(
            f'https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token',
            timeout=20,
            data={'client_id': MS_CLIENT_ID,
                  'client_secret': MS_CLIENT_SECRET,
                  'grant_type': 'client_credentials',
                  'scope': SCOPE_APLICACION})
    except Exception as e:
        return None, f'no se pudo hablar con Microsoft: {str(e)[:150]}'
    datos = {}
    try:
        datos = r.json()
    except Exception:
        pass
    if r.status_code != 200 or not datos.get('access_token'):
        detalle = datos.get('error_description') or r.text[:200]
        # Los tres tropiezos de la configuración inicial, dichos por su nombre.
        # El código de Microsoft no explica ninguno y los tres se arreglan en
        # sitios distintos.
        texto = detalle.lower()
        if 'aadsts7000215' in texto or 'invalid client secret' in texto:
            return None, ('el secreto de la aplicación no vale o caducó: '
                          'genera otro en Entra y ponlo en MS_CLIENT_SECRET.')
        if 'aadsts700016' in texto or 'aadsts900023' in texto:
            return None, ('MS_CLIENT_ID o MS_TENANT_ID no corresponden a una '
                          'aplicación de ese dominio.')
        return None, f'la aplicación no pudo identificarse: {detalle[:160]}'
    dura = int(datos.get('expires_in') or 3600) - 120
    _TOKEN_APP['graph'] = (datos['access_token'],
                           ahora + timedelta(seconds=max(dura, 60)))
    return datos['access_token'], None


def _enviar_sin_persona(cuenta, msg):
    """Manda por el buzón de `cuenta` con el permiso de la aplicación."""
    token, error = token_de_aplicacion()
    if not token:
        return False, error
    return _postear_a_graph(GRAPH_SENDMAIL_BUZON.format(cuenta), token, msg,
                            cuenta)


# ------------------------------------------------------------
#  Las tres salidas
# ------------------------------------------------------------
# Graph es la buena y SMTP la de siempre. No es cuestión de gusto: Exchange
# Online trae un interruptor de dominio —SmtpClientAuthentication— que cierra
# el puerto 587 para TODAS las cuentas del tenant, y lo cierra también para
# quien llega con un token OAuth correcto. Cuando en csccue.gob.ec lo apagaron,
# las citas se guardaban y los invitados no se enteraban de nada. La API no
# pasa por ese puerto y no le afecta ese interruptor.
#
# SMTP se queda como respaldo porque sigue siendo el único camino donde el
# permiso de Graph no está consentido —las cuentas conectadas antes de esto— y
# porque en las cuentas personales de Microsoft funciona sin más.
def _enviar_por_graph(app, cuenta, msg):
    """Manda el mensaje ya armado por la API. Devuelve (se mandó, motivo).

    Va el MIME ENTERO en base64, no el JSON de Graph con asunto y cuerpo: el
    JSON no sabe expresar un `text/calendar` con METHOD:REQUEST como
    alternativa, y sin eso lo que llega es un correo con un archivo adjunto en
    vez de una invitación que el calendario del otro reconoce. Mandando el MIME
    crudo se entrega exactamente lo mismo que salía por SMTP."""
    token, error = token_de_acceso(app, cuenta, recurso='graph')
    if not token:
        return False, error
    return _postear_a_graph(GRAPH_SENDMAIL, token, msg, cuenta)


def _postear_a_graph(url, token, msg, cuenta):
    """El envío en sí, que es el mismo con permiso de persona y de aplicación:
    sólo cambian la dirección y de dónde salió el token."""
    try:
        # Con final de línea CRLF, que es el que manda el RFC 5322.
        # `as_bytes` a secas lo deja en LF, y un MIME con las cabeceras
        # separadas a la manera de Unix es lo que hace que un servidor lo
        # acepte y otro lo rechace por malformado. Por SMTP de esto se
        # encargaba smtplib por su cuenta; aquí hay que decirlo.
        crudo = msg.as_bytes(policy=msg.policy.clone(linesep=CRLF))
        r = requests.post(url, timeout=30, data=b64encode(crudo),
                          headers={'Authorization': f'Bearer {token}',
                                   'Content-Type': 'text/plain'})
    except Exception as e:
        return False, f'no se pudo hablar con Graph: {str(e)[:150]}'
    if r.status_code in (200, 202):
        return True, None
    detalle = ''
    try:
        detalle = ((r.json() or {}).get('error') or {}).get('message') or ''
    except Exception:
        pass
    # Un 403 con el permiso de aplicación casi siempre es una de dos, y ninguna
    # se adivina leyendo «Access is denied»: o nadie ha consentido el permiso,
    # o hay una política que limita a qué buzones alcanza y éste no está.
    if r.status_code == 403:
        return False, (f'el dominio no deja mandar por {cuenta}: falta que un '
                       'administrador consienta Mail.Send, o el buzón está '
                       'fuera de la ApplicationAccessPolicy.')
    return False, f'Graph contestó {r.status_code}: {(detalle or r.text)[:180]}'


def _por_que_no_entra(cuenta, respuesta):
    """Traduce el portazo de Exchange a algo sobre lo que se pueda actuar.

    El 5.7.139 dice «Authentication unsuccessful», y eso manda a revisar la
    contraseña y el permiso, que es justo lo que está bien. Lo que pasa es que
    el dominio tiene apagado el SMTP autenticado, y eso no se arregla desde
    aquí ni volviendo a conectar la cuenta."""
    texto = (respuesta or '').lower()
    if any(m in texto for m in SMTP_APAGADO):
        return (f'{cuenta}: el dominio tiene apagado el SMTP autenticado '
                '(SmtpClientAuthentication) y eso cierra el puerto 587 aunque '
                'el permiso sea correcto.')
    return f'no se pudo entrar en {cuenta}: {(respuesta or "")[:180]}'


def _enviar_por_smtp(app, cuenta, msg):
    """La salida de siempre: puerto 587 con el token por XOAUTH2."""
    token, error = token_de_acceso(app, cuenta)
    if not token:
        return False, error
    cadena = b64encode(f'user={cuenta}\x01auth=Bearer {token}\x01\x01'.encode()).decode()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as servidor:
            servidor.starttls(context=ssl.create_default_context())
            servidor.ehlo()
            # Se mira lo que contesta el AUTH. Antes no se miraba, y el rechazo
            # salía más tarde como una excepción del envío: el motivo real
            # llegaba envuelto en una traza sobre el destinatario, que no tenía
            # nada que ver.
            codigo, respuesta = servidor.docmd('AUTH', 'XOAUTH2 ' + cadena)
            if codigo != 235:
                return False, _por_que_no_entra(
                    cuenta, (respuesta or b'').decode('utf-8', 'ignore'))
            servidor.send_message(msg)
        return True, None
    except Exception as e:
        return False, _por_que_no_entra(cuenta, str(e))


def _entregar(app, cuenta, msg):
    """Saca el mensaje por donde se pueda. Devuelve (se mandó, motivo).

    En orden: el permiso de la aplicación, que no depende de que nadie haya
    conectado nada; el de la persona, para las cuentas que no alcanza el
    primero; y SMTP, donde siga abierto.

    Si falla todo se dicen TODAS las razones. Con una sola, quien lo lee
    arregla ese lado y se encuentra con que sigue sin salir."""
    caminos = []
    if manda_sola(cuenta):
        caminos.append(('el permiso de la aplicación',
                        lambda: _enviar_sin_persona(cuenta, msg)))
    caminos.append(('Graph', lambda: _enviar_por_graph(app, cuenta, msg)))
    caminos.append(('SMTP', lambda: _enviar_por_smtp(app, cuenta, msg)))

    vistos, motivos = set(), []
    for nombre, intentar in caminos:
        ok, fallo = intentar()
        if ok:
            return True, None
        # El mismo motivo dos veces no informa: cuando la cuenta ni siquiera
        # está autorizada, Graph y SMTP fallan por lo mismo y decirlo dos
        # veces sólo hace el aviso más largo.
        if fallo and fallo not in vistos:
            vistos.add(fallo)
            motivos.append(f'por {nombre}, {fallo}')
    return False, ' — '.join(motivos)


def enviar_invitacion(app, apt, cuenta, email_map, metodo='REQUEST', secuencia=0,
                      incluir_organizador=True):
    """Manda la cita como invitación desde `cuenta`. Devuelve (nº destinatarios, error).

    Nunca lanza excepción: que falle el correo no puede tumbar la aprobación de
    la cita, que ya está guardada. Lo que sí hace es DECIR que falló, para que
    quien aprobó sepa que el otro no se ha enterado."""
    # Esta vía es la de las cuentas que NO agendan por API (Microsoft): el
    # correo es su único canal, así que la cuenta que convoca va también como
    # destinataria — es la forma de que la cita entre en SU calendario.
    destinos = destinatarios_de(apt, email_map, cuenta,
                                incluir_organizador=incluir_organizador)
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

    ok, error = _entregar(app, cuenta, msg)
    if not ok:
        print(f'[invitaciones] {cuenta}: {error}')
        return 0, (error or 'no se pudo mandar la invitación')[:400]
    return len(destinos), None


def enviar_cancelacion(app, apt, cuenta, email_map, secuencia=1):
    """La otra mitad: retirar del calendario ajeno una cita que ya no existe."""
    return enviar_invitacion(app, apt, cuenta, email_map,
                             metodo='CANCEL', secuencia=secuencia)


def cuentas_microsoft(app):
    """Las cuentas de Microsoft que agendan, con su estado de autorización.

    `sola` es la que manda con el permiso de la aplicación: no hay nada que
    conectarle para que salga la invitación, aunque siga sin permiso para leer
    lo que le contesten."""
    try:
        filas = app.supabase.get('ms_tokens', select='email,refresh_token,token_expiry') or []
    except Exception:
        return []
    return [{'email': f['email'], 'conectada': bool(f.get('refresh_token')),
             'sola': manda_sola(f['email']),
             'expiry': f.get('token_expiry')} for f in filas]
