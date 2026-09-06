# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Calendario de vencimientos: quién respondía, para cuándo, y el aviso cuando
la fecha pasó sin que el trabajo estuviera hecho.

Un proyecto se abre con una fecha de vencimiento y las actividades que cuelgan
de él llevan la suya. Mientras esa fecha esté por delante no hay nada que decir.
El día que se pasa sin que el trabajo esté terminado, el compromiso está
INCUMPLIDO, y eso no puede quedarse esperando a que alguien entre a mirar la
pantalla: sale por correo.

**A quién le sale.** El aviso se le manda A QUIEN NO LO HIZO, no a la dirección
del despacho. Un recordatorio que llega a un tercero obliga a que ese tercero
reenvíe, y entonces el reclamo depende de que él se acuerde: quien tiene el
trabajo pendiente es quien tiene que leerlo. Cada uno recibe UN correo al día y
sólo con lo suyo — un proyecto con doce actividades atrasadas no debe llenar
doce veces la bandeja, y a nadie le sirve la lista de lo que deben los demás.

**Lo que sube a la dirección.** A `AVISO_EMAIL` ya no se le manda todo, porque
un resumen diario completo se deja de leer en una semana. Sube sólo lo que hay
que decidir: lo que lleva más de `AVISO_ESCALADO_DIAS` días de retraso —a esas
alturas el recordatorio automático ya demostró que no basta— y aquello cuyo
responsable no se pudo localizar. Esa segunda lista no es ruido: es un
incumplimiento que HOY no se le está reclamando a nadie, y callarlo sería justo
el fallo que este módulo existe para evitar.

**Cómo se averigua el correo de cada quien.** En cascada: `tasks.assigned_email`
si está escrito; si no, el nombre de `tasks.assigned_to` / `projects.owner`
cruzado contra `users.full_name`. Si el nombre no aparece en el directorio, o
aparece dos veces, NO se adivina: se trata como laguna y sube a la dirección.
Reclamarle a quien no era es peor que no reclamar.

La tabla `vencimiento_avisos` guarda qué se avisó cada día Y A QUIÉN. Sirve para
dos cosas: que dos procesos (el hilo de fondo y el cron externo) no manden el
mismo aviso dos veces, y que quede constancia de cuándo se avisó de qué y a qué
dirección, que es lo que convierte un recordatorio en algo que se puede
reclamar después.
"""
import os
import re
import html as _html
import smtplib
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytz

# Zona horaria del despacho: el «día» de un vencimiento es el de aquí, no el
# UTC del servidor. Sin esto, entre las 19:00 y la medianoche el servidor ya
# estaría en el día siguiente y daría por incumplido lo que todavía tiene horas.
TZ = pytz.timezone('America/Guayaquil')

DESTINO_POR_DEFECTO = 'jomapconsultores@gmail.com'

# Días de retraso a partir de los cuales el incumplimiento deja de ser sólo cosa
# de quien lo tiene y sube a la dirección. Si el recordatorio diario no ha
# funcionado en una semana, no va a funcionar al octavo día: hace falta que
# alguien decida.
ESCALADO_DIAS_POR_DEFECTO = 7

# Un proyecto cerrado ya no incumple nada, esté como esté su fecha.
ESTADOS_PROYECTO_CERRADO = {'completed', 'cancelled'}

# Evita que dos peticiones simultáneas al cron manden el aviso por duplicado
# dentro del mismo proceso. Entre procesos lo impide la tabla.
_LOCK = threading.Lock()

_RE_CORREO = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')


# ============================================================
#  CONFIGURACIÓN DE CORREO
# ============================================================
def _conf(app):
    """Datos del servidor de correo. Sin SMTP_HOST el aviso queda desactivado."""
    c = app.config
    usuario = c.get('SMTP_USER') or os.getenv('SMTP_USER', '')

    def _entero(clave, defecto):
        # `is None` y no `or`: un 0 configurado a propósito es un valor, no un
        # hueco que haya que rellenar con el valor por defecto.
        valor = c.get(clave)
        if valor is None or valor == '':
            valor = os.getenv(clave, str(defecto))
        try:
            return int(valor)
        except (TypeError, ValueError):
            return defecto

    def _bandera(clave, defecto='1'):
        valor = c.get(clave)
        if valor is None:
            valor = os.getenv(clave, defecto)
        return str(valor).strip().lower() not in ('0', 'false', 'no', '')

    return {
        'host':     c.get('SMTP_HOST') or os.getenv('SMTP_HOST', ''),
        'port':     _entero('SMTP_PORT', 587),
        'user':     usuario,
        'password': c.get('SMTP_PASSWORD') or os.getenv('SMTP_PASSWORD', ''),
        'remitente': (c.get('SMTP_FROM') or os.getenv('SMTP_FROM', '') or usuario),
        'ssl':      bool(c.get('SMTP_SSL')),
        'destino':  c.get('AVISO_EMAIL') or os.getenv('AVISO_EMAIL', DESTINO_POR_DEFECTO),
        'hora':     _entero('AVISO_HORA', 8),
        # Reparto por responsable. Apagarlo (AVISO_PERSONAL=0) devuelve el
        # módulo a lo que hacía antes: un único correo a la dirección con todo.
        'personal': _bandera('AVISO_PERSONAL', '1'),
        'escalado_dias': _entero('AVISO_ESCALADO_DIAS', ESCALADO_DIAS_POR_DEFECTO),
        # La agenda de pendientes sale también todos los días, a la misma hora y
        # detrás del aviso de incumplimiento. Con AGENDA_DIARIA=0 vuelve a salir
        # sólo cuando alguien la pide desde la pantalla.
        'agenda_diaria': _bandera('AGENDA_DIARIA', '1'),
    }


def correo_configurado(app):
    cf = _conf(app)
    return bool(cf['host'] and cf['remitente'])


def enviar_correo(app, asunto, cuerpo_html, cuerpo_texto, destinatarios=None):
    """Manda un correo. Devuelve (ok, error).

    No lanza excepción: un fallo del servidor de correo no puede tumbar ni el
    hilo de fondo ni la petición que lo disparó."""
    cf = _conf(app)
    if not cf['host'] or not cf['remitente']:
        return False, 'Correo no configurado (falta SMTP_HOST o SMTP_FROM)'
    destinos = destinatarios or [cf['destino']]
    destinos = [d for d in destinos if d]
    if not destinos:
        return False, 'Sin destinatario'

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = cf['remitente']
    msg['To'] = ', '.join(destinos)
    msg.set_content(cuerpo_texto)
    msg.add_alternative(cuerpo_html, subtype='html')

    try:
        if cf['ssl']:
            servidor = smtplib.SMTP_SSL(cf['host'], cf['port'], timeout=20)
        else:
            servidor = smtplib.SMTP(cf['host'], cf['port'], timeout=20)
        with servidor:
            if not cf['ssl']:
                servidor.starttls()
            if cf['user']:
                servidor.login(cf['user'], cf['password'])
            servidor.send_message(msg)
        return True, None
    except Exception as e:
        print(f'[avisos] no se pudo enviar el correo: {e}')
        return False, str(e)[:300]


# ============================================================
#  QUÉ ESTÁ INCUMPLIDO
# ============================================================
def hoy_local():
    return datetime.now(TZ).date()


def _fmt(d):
    """2026-08-17 -> 17/08/2026. Devuelve el original si no se puede parsear."""
    try:
        return datetime.strptime(str(d)[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return str(d or '')


def _dias_retraso(due, hoy):
    try:
        return (hoy - datetime.strptime(str(due)[:10], '%Y-%m-%d').date()).days
    except Exception:
        return 0


def _es_correo(v):
    return bool(v and _RE_CORREO.match(str(v).strip()))


def _normalizar(nombre):
    """«Ana María  Pérez » -> «ana maria perez».

    Se compara sin tildes, sin mayúsculas y sin espacios de más porque el
    responsable se teclea a mano en la ficha y el directorio se llenó por otro
    lado: exigir que coincidan carácter a carácter dejaría fuera a media
    plantilla por un acento."""
    texto = str(nombre or '').strip()
    if not texto:
        return ''
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return ' '.join(texto.lower().split())


def _directorio(app):
    """Nombre normalizado -> correo, e id -> nombre, sacados de `users`.

    Los nombres repetidos se DESCARTAN a propósito. Si hay dos personas que se
    llaman igual, no hay forma de saber a cuál se le reclama, y mandarle el
    aviso a la primera que salga de la consulta es exactamente el error que
    convierte un recordatorio en un problema con alguien."""
    filas = app.supabase.get('users', select='id,full_name,email') or []
    por_nombre, ambiguos, nombres_por_id = {}, set(), {}
    for f in filas:
        correo = (f.get('email') or '').strip()
        if f.get('id'):
            nombres_por_id[str(f['id'])] = f.get('full_name') or correo or ''
        clave = _normalizar(f.get('full_name'))
        if not clave or not _es_correo(correo):
            continue
        anterior = por_nombre.get(clave)
        if anterior and anterior.lower() != correo.lower():
            ambiguos.add(clave)
        por_nombre[clave] = correo
    for clave in ambiguos:
        por_nombre.pop(clave, None)
    return por_nombre, nombres_por_id, ambiguos


def _resolver_destino(declarado, correo_escrito, por_nombre, ambiguos):
    """(correo, motivo_de_la_laguna). Cuando hay correo, el motivo es None."""
    if _es_correo(correo_escrito):
        return str(correo_escrito).strip(), None
    clave = _normalizar(declarado)
    if not clave:
        return None, 'la ficha no dice quién es el responsable'
    if clave in ambiguos:
        return None, f'hay más de un usuario llamado «{declarado}»'
    correo = por_nombre.get(clave)
    if correo:
        return correo, None
    return None, f'«{declarado}» no tiene correo en el directorio de usuarios'


class ConsultaFallida(Exception):
    """La base no contestó.

    Es distinto de «no hay nada», y confundirlos aquí es el peor fallo que
    puede tener este módulo: si la consulta caduca y se devuelve una lista
    vacía, el sistema entiende que no se incumple nada, no manda el correo y
    nadie se entera de que dejó de mirar. Un aviso que calla cuando falla es
    peor que no tener aviso, porque además tranquiliza."""


def _consultar(app, tabla, filtros, select, intentos=3):
    """Consulta que, o trae los datos, o dice claramente que no pudo.

    Plazo más largo y tres intentos separados porque esto corre en segundo
    plano una vez al día: aquí esperar cinco segundos más no le hace daño a
    nadie, y en cambio rendirse a la primera sí."""
    url = f'{app.supabase.url}/rest/v1/{tabla}?select={select}'
    for k, v in (filtros or {}).items():
        url += f'&{k}={v}'
    ultimo = 'sin respuesta'
    for intento in range(1, intentos + 1):
        try:
            r = app.supabase._session.get(url, timeout=(5, 30))
            if r.status_code == 200:
                return r.json()
            ultimo = f'HTTP {r.status_code} {r.text[:140]}'
        except Exception as e:
            ultimo = str(e)[:140]
        if intento < intentos:
            time.sleep(2 * intento)          # 2 s, 4 s
    raise ConsultaFallida(f'{tabla}: {ultimo}')


def _es_compromiso(t):
    """¿Es esto un compromiso del despacho o ruido del buzón?

    En la base quedan las filas que en su día bajaron del To-Do de Microsoft:
    correos marcados de Outlook y pendientes personales. Reclamarlos como
    incumplimiento llenaría el correo diario de decenas de líneas que nadie
    pactó con nadie. Se vigila lo que sí es un compromiso: lo que cuelga de un
    proyecto, y lo que se creó a mano en la planificación."""
    if t.get('project_id'):
        return True
    return t.get('source') != 'ms_todo' and t.get('source_app') != 'Outlook'


def incumplidos(app, hoy=None):
    """Proyectos y actividades cuya fecha ya pasó sin estar terminados.

    Cada uno sale con `_responsable` (para enseñarlo), `_correo` (a quién se le
    reclama) y, cuando no hay correo, `_laguna` con el motivo."""
    hoy = hoy or hoy_local()
    limite = hoy.isoformat()

    proyectos = _consultar(
        app, 'projects', {'due_date': f'lt.{limite}'},
        'id,name,due_date,status,owner,created_by')
    proyectos = [p for p in proyectos
                 if (p.get('status') or 'active') not in ESTADOS_PROYECTO_CERRADO]

    tareas = _consultar(
        app, 'tasks', {'due_date': f'lt.{limite}', 'status': 'neq.done'},
        'id,title,due_date,status,assigned_to,assigned_email,'
        'project_id,created_by,progress_pct,source,source_app')
    tareas = [t for t in tareas if _es_compromiso(t)]

    por_nombre, nombres_por_id, ambiguos = _directorio(app)
    nombre_proyecto = {p['id']: p.get('name') for p in
                       (app.supabase.get('projects', select='id,name') or [])}

    # El responsable que se ENSEÑA puede caer en quien lo creó, porque un aviso
    # que no dice de quién es no sirve para reclamar. Pero a quien se le MANDA
    # sale sólo de lo declarado en la ficha: que alguien creara la tarea no
    # significa que le tocara hacerla.
    for p in proyectos:
        declarado = p.get('owner')
        p['_responsable'] = (declarado
                             or nombres_por_id.get(str(p.get('created_by') or '')) or '—')
        p['_correo'], p['_laguna'] = _resolver_destino(
            declarado, None, por_nombre, ambiguos)
        p['_dias'] = _dias_retraso(p.get('due_date'), hoy)
    for t in tareas:
        declarado = t.get('assigned_to')
        t['_responsable'] = (declarado or t.get('assigned_email')
                             or nombres_por_id.get(str(t.get('created_by') or '')) or '—')
        t['_correo'], t['_laguna'] = _resolver_destino(
            declarado, t.get('assigned_email'), por_nombre, ambiguos)
        t['_dias'] = _dias_retraso(t.get('due_date'), hoy)
        t['_proyecto'] = nombre_proyecto.get(t.get('project_id')) or '—'

    proyectos.sort(key=lambda x: -x['_dias'])
    tareas.sort(key=lambda x: -x['_dias'])
    return {'proyectos': proyectos, 'tareas': tareas}


# ============================================================
#  REGISTRO DE LO YA AVISADO
# ============================================================
# Respaldo en memoria por si la tabla `vencimiento_avisos` todavía no está
# creada (migración 030 sin aplicar): al menos dentro de este proceso no se
# repite el aviso el mismo día.
_avisado_en_memoria = {'fecha': None, 'claves': set()}


def _clave(tipo, ref_id, destino):
    return (tipo, ref_id, (destino or '').strip().lower())


def _ya_avisado(app, hoy):
    """Qué se avisó hoy y A QUIÉN.

    La dirección forma parte de la clave: el mismo incumplimiento se le puede
    recordar a quien lo tiene y, si lleva mucho, subirlo a la dirección. Son
    dos avisos distintos y ninguno debe tapar al otro."""
    filas = app.supabase.get('vencimiento_avisos', {'fecha': hoy.isoformat()},
                             select='tipo,ref_id,destinatario')
    if filas:
        return {_clave(f.get('tipo'), f.get('ref_id'), f.get('destinatario'))
                for f in filas}
    # Sin filas puede ser «no hay nada» o «la tabla no existe»: en ambos casos
    # el respaldo en memoria es lo único que queda.
    if _avisado_en_memoria['fecha'] == hoy:
        return set(_avisado_en_memoria['claves'])
    return set()


def _registrar_avisos(app, hoy, items):
    """`items`: lista de (tipo, objeto, título, destinatario)."""
    if _avisado_en_memoria['fecha'] != hoy:
        _avisado_en_memoria['fecha'] = hoy
        _avisado_en_memoria['claves'] = set()
    filas = []
    for tipo, obj, titulo, destino in items:
        _avisado_en_memoria['claves'].add(_clave(tipo, obj['id'], destino))
        filas.append({
            'tipo': tipo, 'ref_id': obj['id'], 'fecha': hoy.isoformat(),
            'destinatario': destino, 'titulo': (titulo or '')[:300],
            'due_date': obj.get('due_date'), 'dias_incumplido': obj.get('_dias'),
            'responsable': (obj.get('_responsable') or '')[:200],
        })
    if filas:
        app.supabase.insert_ignore('vencimiento_avisos', filas)


# ============================================================
#  EL CORREO DE INCUMPLIMIENTO
# ============================================================
def _tabla_html(titulo, cabeceras, filas):
    if not filas:
        return ''
    ths = ''.join(
        f'<th style="text-align:left;padding:8px 10px;background:#f1f5f9;'
        f'border-bottom:2px solid #cbd5e1;font-size:12px;color:#475569">{c}</th>'
        for c in cabeceras)
    trs = ''
    for fila in filas:
        tds = ''.join(
            f'<td style="padding:8px 10px;border-bottom:1px solid #e2e8f0;'
            f'font-size:13px;color:#1e293b">{c}</td>' for c in fila)
        trs += f'<tr>{tds}</tr>'
    return (f'<h3 style="font-family:Arial,sans-serif;color:#0f172a;'
            f'margin:22px 0 8px">{titulo}</h3>'
            f'<table style="border-collapse:collapse;width:100%;'
            f'font-family:Arial,sans-serif"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{trs}</tbody></table>')


def _retraso(dias):
    return f"<strong style='color:#b91c1c'>{dias} día(s)</strong>"


def _sobre(titulo_cabecera, subtitulo, introduccion, cuerpo, pie, color='#b91c1c'):
    """El marco común de todos estos correos.

    El color no es decoración: el rojo es para el incumplimiento. La agenda de
    pendientes lleva otro, porque la mayoría de lo que enseña está en plazo y
    pintarla de rojo enseñaría a ignorar el rojo."""
    return f"""<div style="font-family:Arial,sans-serif;max-width:760px;margin:0 auto;color:#0f172a">
  <div style="background:{color};color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">
    <div style="font-size:19px;font-weight:bold">{titulo_cabecera}</div>
    <div style="font-size:13px;opacity:.9">{subtitulo}</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:0;border-radius:0 0 12px 12px;padding:8px 20px 24px">
    <p style="font-size:14px;color:#334155">{introduccion}</p>
    {cuerpo}
    <p style="font-size:12px;color:#64748b;margin-top:26px;border-top:1px solid #e2e8f0;padding-top:12px">
      {pie}
    </p>
  </div>
</div>"""


def _componer_personal(hoy, nombre, proyectos, tareas):
    """El recordatorio que recibe quien tiene el trabajo sin hacer.

    Lleva sólo lo suyo y no lleva la columna «Responsable»: sabe de sobra que es
    él, y enseñarle la lista de los demás convierte un recordatorio en un tablón
    de incumplimientos ajenos."""
    e = _html.escape
    total = len(proyectos) + len(tareas)
    asunto = f'⚠️ Tienes {total} compromiso(s) vencido(s) — {_fmt(hoy)}'

    tabla_p = _tabla_html(
        f'📁 Proyectos a tu cargo ({len(proyectos)})',
        ['Proyecto', 'Vencía el', 'Retraso'],
        [[e(p.get('name') or '(sin nombre)'), _fmt(p.get('due_date')),
          _retraso(p['_dias'])] for p in proyectos])

    tabla_t = _tabla_html(
        f'📋 Actividades a tu cargo ({len(tareas)})',
        ['Actividad', 'Proyecto', 'Vencía el', 'Retraso', 'Avance'],
        [[e(t.get('title') or '(sin título)'), e(t['_proyecto']),
          _fmt(t.get('due_date')), _retraso(t['_dias']),
          f"{t.get('progress_pct') or 0}%"] for t in tareas])

    saludo = f'{e(nombre)}: los' if nombre and nombre != '—' else 'Los'
    cuerpo_html = _sobre(
        '⚠️ Tienes compromisos vencidos',
        f'Revisión del {_fmt(hoy)} · {total} pendiente(s) fuera de plazo a tu nombre',
        f'{saludo} siguientes compromisos están a tu cargo y pasaron su fecha de '
        f'vencimiento sin completarse. Constan como <strong>INCUMPLIDOS</strong>. '
        f'Ciérralos en el calendario, o cambia la fecha dejando dicho el motivo.',
        tabla_p + tabla_t,
        'Aviso automático del calendario de vencimientos · CalendarioMAP. '
        'Mientras el trabajo siga sin cerrarse, este correo se repite una vez al día.')

    lineas = [f'TUS COMPROMISOS INCUMPLIDOS — {_fmt(hoy)}', '']
    if proyectos:
        lineas.append(f'PROYECTOS ({len(proyectos)}):')
        lineas += [f"  - {p.get('name')} · vencía {_fmt(p.get('due_date'))} · "
                   f"{p['_dias']} día(s) de retraso" for p in proyectos]
        lineas.append('')
    if tareas:
        lineas.append(f'ACTIVIDADES ({len(tareas)}):')
        lineas += [f"  - {t.get('title')} [{t['_proyecto']}] · vencía "
                   f"{_fmt(t.get('due_date'))} · {t['_dias']} día(s) de retraso"
                   for t in tareas]
    return asunto, cuerpo_html, '\n'.join(lineas)


def _titulo_de(tipo, obj):
    return (obj.get('name') if tipo == 'proyecto' else obj.get('title')) or '(sin título)'


def _componer_direccion(hoy, escalado, lagunas, umbral):
    """Lo que sube a la dirección: lo que se enquistó y lo que no tiene dueño.

    `escalado` y `lagunas` son listas de (tipo, objeto)."""
    e = _html.escape
    total = len(escalado) + len(lagunas)
    asunto = f'🔺 Incumplimientos que requieren decisión: {total} — {_fmt(hoy)}'

    tabla_e = _tabla_html(
        f'🔺 Con más de {umbral} día(s) de retraso ({len(escalado)}) — ya se les avisó a diario',
        ['Compromiso', 'Proyecto', 'Responsable', 'Vencía el', 'Retraso'],
        [[e(_titulo_de(tp, o)),
          e('(proyecto)' if tp == 'proyecto' else o.get('_proyecto') or '—'),
          e(o.get('_responsable') or '—'), _fmt(o.get('due_date')),
          _retraso(o['_dias'])] for tp, o in escalado])

    tabla_l = _tabla_html(
        f'❓ Sin responsable localizable ({len(lagunas)}) — hoy no se le reclama a nadie',
        ['Compromiso', 'Proyecto', 'Responsable en la ficha', 'Vencía el',
         'Retraso', 'Por qué no salió el aviso'],
        [[e(_titulo_de(tp, o)),
          e('(proyecto)' if tp == 'proyecto' else o.get('_proyecto') or '—'),
          e(o.get('_responsable') or '—'), _fmt(o.get('due_date')),
          _retraso(o['_dias']), e(o.get('_laguna') or '')] for tp, o in lagunas])

    cuerpo_html = _sobre(
        '🔺 Incumplimientos que requieren decisión',
        f'Revisión del {_fmt(hoy)} · {total} caso(s)',
        'A cada responsable ya le llegó su recordatorio diario. Aquí sube sólo lo '
        f'que lleva más de <strong>{umbral} día(s)</strong> de retraso —donde el '
        'recordatorio automático ya demostró que no basta— y lo que <strong>no se '
        'le pudo reclamar a nadie</strong> porque falta el correo del responsable. '
        'Esto último se arregla completando la ficha o el directorio de usuarios.',
        tabla_e + tabla_l,
        'Aviso automático del calendario de vencimientos · CalendarioMAP.')

    lineas = [f'INCUMPLIMIENTOS QUE REQUIEREN DECISIÓN — {_fmt(hoy)}', '']
    if escalado:
        lineas.append(f'MÁS DE {umbral} DÍA(S) DE RETRASO ({len(escalado)}):')
        lineas += [f"  - {_titulo_de(tp, o)} · {o.get('_responsable')} · vencía "
                   f"{_fmt(o.get('due_date'))} · {o['_dias']} día(s)"
                   for tp, o in escalado]
        lineas.append('')
    if lagunas:
        lineas.append(f'SIN RESPONSABLE LOCALIZABLE ({len(lagunas)}):')
        lineas += [f"  - {_titulo_de(tp, o)} · {o.get('_responsable')} · vencía "
                   f"{_fmt(o.get('due_date'))} · {o['_dias']} día(s) · "
                   f"{o.get('_laguna')}" for tp, o in lagunas]
    return asunto, cuerpo_html, '\n'.join(lineas)


def _componer(hoy, proyectos, tareas):
    """El correo único con todo, para cuando el reparto está apagado
    (AVISO_PERSONAL=0). Es lo que hacía el módulo antes del reparto."""
    e = _html.escape
    total = len(proyectos) + len(tareas)
    asunto = f'⚠️ Incumplimiento: {total} compromiso(s) vencido(s) — {_fmt(hoy)}'

    tabla_p = _tabla_html(
        f'📁 Proyectos incumplidos ({len(proyectos)})',
        ['Proyecto', 'Responsable', 'Vencía el', 'Retraso'],
        [[e(p.get('name') or '(sin nombre)'), e(p['_responsable']),
          _fmt(p.get('due_date')), _retraso(p['_dias'])] for p in proyectos])

    tabla_t = _tabla_html(
        f'📋 Actividades incumplidas ({len(tareas)})',
        ['Actividad', 'Proyecto', 'Responsable', 'Vencía el', 'Retraso', 'Avance'],
        [[e(t.get('title') or '(sin título)'), e(t['_proyecto']),
          e(t['_responsable']), _fmt(t.get('due_date')), _retraso(t['_dias']),
          f"{t.get('progress_pct') or 0}%"] for t in tareas])

    cuerpo_html = _sobre(
        '⚠️ Compromisos incumplidos',
        f'Revisión del {_fmt(hoy)} · {total} pendiente(s) fuera de plazo',
        'Los siguientes compromisos pasaron su fecha de vencimiento sin haberse '
        'completado. Constan como <strong>INCUMPLIDOS</strong>.',
        tabla_p + tabla_t,
        'Aviso automático del calendario de vencimientos · CalendarioMAP. '
        'Mientras el trabajo siga sin cerrarse, este correo se repite una vez al día.')

    lineas = [f'COMPROMISOS INCUMPLIDOS — {_fmt(hoy)}', '']
    if proyectos:
        lineas.append(f'PROYECTOS ({len(proyectos)}):')
        lineas += [f"  - {p.get('name')} · {p['_responsable']} · vencía "
                   f"{_fmt(p.get('due_date'))} · {p['_dias']} día(s) de retraso"
                   for p in proyectos]
        lineas.append('')
    if tareas:
        lineas.append(f'ACTIVIDADES ({len(tareas)}):')
        lineas += [f"  - {t.get('title')} [{t['_proyecto']}] · {t['_responsable']} · "
                   f"vencía {_fmt(t.get('due_date'))} · {t['_dias']} día(s) de retraso"
                   for t in tareas]
    return asunto, cuerpo_html, '\n'.join(lineas)


# ============================================================
#  LA AGENDA DE PENDIENTES
#
#  Distinta del aviso de incumplimiento y a propósito. Aquél reclama lo que ya
#  se pasó de fecha; ésta enseña TODO lo que cada quien tiene abierto —lo
#  vencido y lo que todavía está en plazo—, que es lo que hace falta para
#  organizarse la semana.
#
#  Sale también todos los días, detrás del aviso de incumplimiento, por decisión
#  de MAP (2026-09-06). Se levantó la objeción de que dos correos diarios a la
#  misma persona acaban leyéndose menos, y la respuesta fue que se manden los
#  dos: el reclamo de lo incumplido tiene que verse aparte del listado completo.
#  Queda `AGENDA_DIARIA=0` para volver a dejarla bajo demanda sin tocar código.
#
#  El envío diario NO repite si ya salió hoy; el botón de la pantalla sí, porque
#  ahí hay alguien que lo está pidiendo a conciencia.
# ============================================================
def pendientes(app, hoy=None):
    """Todo lo que sigue abierto, con o sin plazo vencido, por responsable."""
    hoy = hoy or hoy_local()
    tareas = _consultar(
        app, 'tasks', {'status': 'neq.done'},
        'id,title,due_date,status,assigned_to,assigned_email,'
        'project_id,created_by,progress_pct,source,source_app')
    tareas = [t for t in tareas if _es_compromiso(t)]

    por_nombre, nombres_por_id, ambiguos = _directorio(app)
    nombre_proyecto = {p['id']: p.get('name') for p in
                       (app.supabase.get('projects', select='id,name') or [])}
    for t in tareas:
        declarado = t.get('assigned_to')
        t['_responsable'] = (declarado or t.get('assigned_email')
                             or nombres_por_id.get(str(t.get('created_by') or '')) or '—')
        t['_correo'], t['_laguna'] = _resolver_destino(
            declarado, t.get('assigned_email'), por_nombre, ambiguos)
        t['_dias'] = _dias_retraso(t.get('due_date'), hoy) if t.get('due_date') else None
        t['_proyecto'] = nombre_proyecto.get(t.get('project_id')) or '—'
    # Lo más urgente primero: lo vencido por antigüedad, después lo que vence
    # antes, y al final lo que no tiene fecha.
    tareas.sort(key=lambda t: (t['_dias'] is None, -(t['_dias'] if t['_dias'] is not None else 0)))
    return tareas


def _componer_agenda(hoy, nombre, tareas):
    """La lista de pendientes de una persona, separada por lo que apremia."""
    e = _html.escape
    vencidas = [t for t in tareas if t['_dias'] is not None and t['_dias'] > 0]
    hoy_mismo = [t for t in tareas if t['_dias'] == 0]
    futuras = [t for t in tareas if t['_dias'] is not None and t['_dias'] < 0]
    sin_fecha = [t for t in tareas if t['_dias'] is None]
    total = len(tareas)
    asunto = f'📋 Tus tareas pendientes: {total} — {_fmt(hoy)}'

    def _tabla(titulo, filas_datos, columna_plazo):
        return _tabla_html(
            titulo, ['Tarea', 'Proyecto', 'Fecha', columna_plazo, 'Avance'],
            [[e(t.get('title') or '(sin título)'), e(t['_proyecto']),
              _fmt(t.get('due_date')) or '—', plazo,
              f"{t.get('progress_pct') or 0}%"]
             for t, plazo in filas_datos])

    cuerpo = _tabla(f'🔴 Vencidas ({len(vencidas)})',
                    [(t, _retraso(t['_dias'])) for t in vencidas], 'Retraso')
    cuerpo += _tabla(f'🟠 Vencen hoy ({len(hoy_mismo)})',
                     [(t, '<strong>hoy</strong>') for t in hoy_mismo], 'Plazo')
    cuerpo += _tabla(
        f'🟢 En plazo ({len(futuras)})',
        [(t, f"faltan {abs(t['_dias'])} día(s)") for t in futuras], 'Plazo')
    cuerpo += _tabla(f'⚪ Sin fecha ({len(sin_fecha)})',
                     [(t, '—') for t in sin_fecha], 'Plazo')

    saludo = f'{e(nombre)}: esto' if nombre and nombre != '—' else 'Esto'
    cuerpo_html = _sobre(
        '📋 Tus tareas pendientes',
        f'Al {_fmt(hoy)} · {total} sin cerrar'
        + (f' · {len(vencidas)} ya vencida(s)' if vencidas else ''),
        f'{saludo} es todo lo que tienes abierto a tu nombre, esté o no fuera de '
        'plazo. Lo de arriba es lo que ya se pasó de fecha; lo de abajo, lo que '
        'todavía está a tiempo.',
        cuerpo,
        'Enviado desde el calendario de vencimientos · CalendarioMAP.',
        color='#1d4ed8')

    lineas = [f'TUS TAREAS PENDIENTES — {_fmt(hoy)} ({total})', '']
    for etiqueta, grupo in (('VENCIDAS', vencidas), ('VENCEN HOY', hoy_mismo),
                            ('EN PLAZO', futuras), ('SIN FECHA', sin_fecha)):
        if not grupo:
            continue
        lineas.append(f'{etiqueta} ({len(grupo)}):')
        for t in grupo:
            if t['_dias'] is None:
                plazo = 'sin fecha'
            elif t['_dias'] > 0:
                plazo = f"{t['_dias']} día(s) de retraso"
            elif t['_dias'] == 0:
                plazo = 'vence hoy'
            else:
                plazo = f"faltan {abs(t['_dias'])} día(s)"
            lineas.append(f"  - {t.get('title')} [{t['_proyecto']}] · "
                          f"{_fmt(t.get('due_date')) or 'sin fecha'} · {plazo}")
        lineas.append('')
    return asunto, cuerpo_html, '\n'.join(lineas)


def enviar_pendientes(app, simulacro=False, evitar_repetir=False):
    """Le manda a cada responsable su lista completa de pendientes.

    `evitar_repetir` salta a quien ya recibió su agenda hoy. Lo usa el envío
    diario: si el proceso se reinicia cerca de la hora, el hilo vuelve a correr
    y nadie tiene por qué recibir la misma lista dos veces. El botón de la
    pantalla lo deja en falso a propósito — ahí hay una persona pidiéndolo, y
    negarle el envío porque «ya salió esta mañana» sería desobedecer una orden
    explícita.

    Queda constancia con tipo `agenda`, separada del `actividad`/`proyecto` del
    incumplimiento, para poder decir después qué se le mandó a quién y cuándo."""
    if not app.supabase:
        return {'success': False, 'error': 'Sin base de datos'}
    hoy = hoy_local()
    cf = _conf(app)
    direccion = cf['destino']
    try:
        tareas = pendientes(app, hoy)
    except ConsultaFallida as e:
        return {'success': False, 'enviado': False,
                'error': f'No se pudo consultar la base ({e}). No se manda nada '
                         'porque no se ha podido mirar.'}
    if not tareas:
        return {'success': True, 'detectados': 0, 'enviado': False,
                'destino': direccion, 'mensaje': 'No hay nada pendiente'}
    if not correo_configurado(app):
        return {'success': False, 'detectados': len(tareas), 'enviado': False,
                'error': 'Correo no configurado: define SMTP_HOST, SMTP_USER, '
                         'SMTP_PASSWORD y SMTP_FROM en el entorno.'}

    por_persona, lagunas = {}, []
    for t in tareas:
        correo = t.get('_correo')
        if not correo:
            lagunas.append(('actividad', t))
            continue
        caja = por_persona.setdefault(
            correo.lower(), {'correo': correo, 'nombre': t.get('_responsable') or '',
                             'tareas': []})
        caja['tareas'].append(t)

    ya_hoy = set()
    if evitar_repetir and not simulacro:
        filas = app.supabase.get('vencimiento_avisos', {'fecha': hoy.isoformat()},
                                 select='tipo,destinatario') or []
        ya_hoy = {(f.get('destinatario') or '').strip().lower()
                  for f in filas if f.get('tipo') == 'agenda'}

    registrados, fallos, enviados_a = [], [], []
    mandadas, saltadas = 0, 0
    for caja in por_persona.values():
        if caja['correo'].lower() in ya_hoy:
            saltadas += 1
            continue
        asunto, html, texto = _componer_agenda(hoy, caja['nombre'], caja['tareas'])
        destino_envio = caja['correo']
        if simulacro:
            asunto, html, texto = _marca_simulacro(destino_envio, asunto, html, texto)
            destino_envio = direccion
        ok, error = enviar_correo(app, asunto, html, texto, [destino_envio])
        if not ok:
            fallos.append(f"{caja['correo']}: {error}")
            continue
        enviados_a.append(caja['correo'])
        mandadas += len(caja['tareas'])
        registrados += [('agenda', t, t.get('title') or '', caja['correo'])
                        for t in caja['tareas']]

    # Lo que no tiene responsable localizable no se queda sin decir. Y cuenta
    # como «lo de hoy» igual que las agendas: si no se registrara, el envío
    # diario se saltaría a las personas pero volvería a mandar esta lista cada
    # vez que el hilo corriera de más.
    if lagunas and direccion.lower() not in ya_hoy:
        asunto, html, texto = _componer_direccion(hoy, [], lagunas, cf['escalado_dias'])
        if simulacro:
            asunto, html, texto = _marca_simulacro(direccion, asunto, html, texto)
        ok, error = enviar_correo(app, asunto, html, texto, [direccion])
        if ok:
            enviados_a.append(direccion)
            registrados += [('agenda', t, t.get('title') or '', direccion)
                            for tp, t in lagunas]
        else:
            fallos.append(f'{direccion}: {error}')
    elif lagunas:
        saltadas += 1

    if registrados and not simulacro:
        _registrar_avisos(app, hoy, registrados)

    personas = len({c.lower() for c in enviados_a if c.lower() != direccion.lower()})
    print(f'[avisos] agenda de pendientes: {mandadas} tarea(s) a {personas} persona(s)'
          + (' (simulacro)' if simulacro else ''))
    resultado = {
        'success': not fallos, 'detectados': len(tareas), 'enviadas': mandadas,
        'enviado': bool(enviados_a), 'personas_avisadas': personas,
        'destinatarios': sorted(set(enviados_a)), 'destino': direccion,
        'sin_responsable': len(lagunas), 'simulacro': simulacro,
        'ya_tenian_la_suya': saltadas,
    }
    if fallos:
        resultado['error'] = 'No se pudo enviar a: ' + '; '.join(fallos[:5])
    elif not enviados_a and saltadas:
        resultado['mensaje'] = 'Todos habían recibido ya su agenda de hoy'
    return resultado


def _marca_simulacro(destino_real, asunto, cuerpo_html, cuerpo_texto):
    """Convierte un correo en su ensayo: mismo contenido, pero dice a quién
    habría ido y se manda a la dirección del despacho."""
    aviso = (f'<div style="background:#fef3c7;border:1px solid #f59e0b;color:#78350f;'
             f'padding:10px 14px;border-radius:8px;font-family:Arial,sans-serif;'
             f'font-size:13px;margin-bottom:14px"><strong>SIMULACRO.</strong> '
             f'Este correo NO se envió a su destinatario. Iba dirigido a '
             f'<strong>{_html.escape(destino_real)}</strong>.</div>')
    return (f'[SIMULACRO → {destino_real}] {asunto}',
            aviso + cuerpo_html,
            f'*** SIMULACRO — este correo iba dirigido a {destino_real} '
            f'y NO se le envió ***\n\n{cuerpo_texto}')


# ============================================================
#  LA REVISIÓN
# ============================================================
def revisar_vencimientos(app, forzar=False, simulacro=False):
    """Un ciclo completo: mirar qué está incumplido, avisar a cada quien y dejar
    constancia.

    `forzar` reenvía aunque ya se hubiera avisado hoy (sirve para probar que el
    correo sale de verdad, sin esperar al día siguiente).

    `simulacro` manda TODOS los correos a la dirección del despacho, cada uno
    diciendo a quién habría ido, y no registra nada. Es la forma de ver qué le
    llegaría a cada persona antes de que le llegue de verdad — y con un aviso
    que escribe a terceros, esa comprobación no es un lujo."""
    if not app.supabase:
        return {'success': False, 'error': 'Sin base de datos'}
    with _LOCK:
        hoy = hoy_local()
        cf = _conf(app)
        try:
            datos = incumplidos(app, hoy)
        except ConsultaFallida as e:
            # No se calla ni se da por bueno: se dice que HOY NO SE PUDO MIRAR.
            print(f'[avisos] no se pudo comprobar los vencimientos: {e}')
            return {'success': False, 'enviado': False, 'avisados': 0,
                    'error': f'No se pudo consultar la base ({e}). No se avisa '
                             'nada porque no se ha podido mirar, no porque no '
                             'haya incumplimientos.'}

        todos = ([('proyecto', p) for p in datos['proyectos']] +
                 [('actividad', t) for t in datos['tareas']])
        total_detectado = len(todos)
        direccion = cf['destino']
        if not total_detectado:
            return {'success': True, 'detectados': 0, 'avisados': 0,
                    'enviado': False, 'destino': direccion,
                    'proyectos': 0, 'actividades': 0,
                    'mensaje': 'Sin incumplimientos'}

        if not correo_configurado(app):
            return {'success': False, 'detectados': total_detectado, 'avisados': 0,
                    'enviado': False, 'proyectos': 0, 'actividades': 0,
                    'destino': direccion,
                    'error': 'Correo no configurado: define SMTP_HOST, SMTP_USER, '
                             'SMTP_PASSWORD y SMTP_FROM en el entorno.'}

        ya = set() if (forzar or simulacro) else _ya_avisado(app, hoy)
        umbral = cf['escalado_dias']

        # ── Reparto apagado: un solo correo con todo, como antes ────
        if not cf['personal']:
            proyectos = [o for tp, o in todos if tp == 'proyecto'
                         and _clave(tp, o['id'], direccion) not in ya]
            tareas = [o for tp, o in todos if tp == 'actividad'
                      and _clave(tp, o['id'], direccion) not in ya]
            if not proyectos and not tareas:
                return {'success': True, 'detectados': total_detectado, 'avisados': 0,
                        'enviado': False, 'destino': direccion,
                        'proyectos': 0, 'actividades': 0,
                        'mensaje': 'Nada nuevo que avisar'}
            asunto, html, texto = _componer(hoy, proyectos, tareas)
            if simulacro:
                asunto, html, texto = _marca_simulacro(direccion, asunto, html, texto)
            ok, error = enviar_correo(app, asunto, html, texto, [direccion])
            if not ok:
                return {'success': False, 'detectados': total_detectado, 'avisados': 0,
                        'enviado': False, 'destino': direccion, 'proyectos': 0,
                        'actividades': 0, 'error': error}
            if not simulacro:
                _registrar_avisos(
                    app, hoy,
                    [('proyecto', p, p.get('name') or '', direccion) for p in proyectos] +
                    [('actividad', t, t.get('title') or '', direccion) for t in tareas])
            return {'success': True, 'detectados': total_detectado,
                    'avisados': len(proyectos) + len(tareas),
                    'proyectos': len(proyectos), 'actividades': len(tareas),
                    'enviado': True, 'destino': direccion, 'personal': False,
                    'simulacro': simulacro}

        # ── El reparto ──────────────────────────────────────────────
        # Cada uno con lo suyo; a la dirección lo enquistado y lo huérfano.
        por_persona = {}          # correo en minúsculas -> qué le toca
        escalado, lagunas = [], []
        for tipo, obj in todos:
            correo = obj.get('_correo')
            if not correo:
                lagunas.append((tipo, obj))
                continue
            caja = por_persona.setdefault(
                correo.lower(),
                {'correo': correo, 'nombre': obj.get('_responsable') or '',
                 'proyectos': [], 'tareas': []})
            if _clave(tipo, obj['id'], correo) not in ya:
                caja['proyectos' if tipo == 'proyecto' else 'tareas'].append(obj)
            if obj['_dias'] > umbral:
                escalado.append((tipo, obj))

        # ── Los recordatorios personales ────────────────────────────
        registrados, fallos, enviados_a = [], [], []
        avisados_p = avisados_t = 0
        for caja in por_persona.values():
            ps, ts = caja['proyectos'], caja['tareas']
            if not ps and not ts:
                continue          # ya se le avisó hoy de todo lo suyo
            asunto, html, texto = _componer_personal(hoy, caja['nombre'], ps, ts)
            destino_envio = caja['correo']
            if simulacro:
                asunto, html, texto = _marca_simulacro(destino_envio, asunto, html, texto)
                destino_envio = direccion
            ok, error = enviar_correo(app, asunto, html, texto, [destino_envio])
            if not ok:
                # El fallo con un destinatario no puede cancelar a los demás:
                # cada correo es un reclamo independiente.
                fallos.append(f"{caja['correo']}: {error}")
                continue
            enviados_a.append(caja['correo'])
            avisados_p += len(ps)
            avisados_t += len(ts)
            registrados += (
                [('proyecto', p, p.get('name') or '', caja['correo']) for p in ps] +
                [('actividad', t, t.get('title') or '', caja['correo']) for t in ts])

        # ── Lo que sube a la dirección ──────────────────────────────
        escalado_nuevo = [(tp, o) for tp, o in escalado
                          if _clave(tp, o['id'], direccion) not in ya]
        lagunas_nuevas = [(tp, o) for tp, o in lagunas
                          if _clave(tp, o['id'], direccion) not in ya]
        enviado_direccion = False
        if escalado_nuevo or lagunas_nuevas:
            asunto, html, texto = _componer_direccion(
                hoy, escalado_nuevo, lagunas_nuevas, umbral)
            if simulacro:
                asunto, html, texto = _marca_simulacro(direccion, asunto, html, texto)
            ok, error = enviar_correo(app, asunto, html, texto, [direccion])
            if ok:
                enviado_direccion = True
                enviados_a.append(direccion)
                registrados += [(tp, o, _titulo_de(tp, o), direccion)
                                for tp, o in escalado_nuevo + lagunas_nuevas]
            else:
                fallos.append(f'{direccion}: {error}')

        if registrados and not simulacro:
            _registrar_avisos(app, hoy, registrados)

        personas = len({c.lower() for c in enviados_a if c.lower() != direccion.lower()})
        print(f'[avisos] {avisados_p + avisados_t} compromiso(s) reclamados a '
              f'{personas} responsable(s); {len(escalado_nuevo)} escalado(s) y '
              f'{len(lagunas_nuevas)} sin responsable a {direccion}')
        resultado = {
            'success': not fallos,
            'detectados': total_detectado,
            'avisados': avisados_p + avisados_t,
            'proyectos': avisados_p,
            'actividades': avisados_t,
            'enviado': bool(enviados_a),
            'destino': direccion,
            'personal': True,
            'simulacro': simulacro,
            'destinatarios': sorted({c for c in enviados_a}),
            'personas_avisadas': personas,
            'escalados': len(escalado_nuevo),
            'sin_responsable': len(lagunas_nuevas),
            'escalado_dias': umbral,
            'aviso_direccion': enviado_direccion,
        }
        if fallos:
            resultado['error'] = 'No se pudo avisar a: ' + '; '.join(fallos[:5])
        elif not enviados_a:
            resultado['mensaje'] = 'Nada nuevo que avisar'
        return resultado


def estado(app):
    """Resumen para la pantalla de administración."""
    hoy = hoy_local()
    cf = _conf(app)
    resumen = {
        'correo_configurado': correo_configurado(app),
        'destino': cf['destino'],
        'hora_revision': cf['hora'],
        'reparto_personal': cf['personal'],
        'escalado_dias': cf['escalado_dias'],
        'agenda_diaria': cf['agenda_diaria'],
        'proyectos_incumplidos': 0,
        'actividades_incumplidas': 0,
        'con_responsable': 0,
        'sin_responsable': 0,
        'personas': 0,
        'avisado_hoy': False,
        'base_ok': True,
    }
    try:
        datos = incumplidos(app, hoy)
    except ConsultaFallida as e:
        # Enseñar dos ceros aquí sería mentir con cara de buena noticia.
        resumen['base_ok'] = False
        resumen['error_base'] = str(e)
        return resumen
    resumen['proyectos_incumplidos'] = len(datos['proyectos'])
    resumen['actividades_incumplidas'] = len(datos['tareas'])
    todos = datos['proyectos'] + datos['tareas']
    resumen['con_responsable'] = len([o for o in todos if o.get('_correo')])
    resumen['sin_responsable'] = len([o for o in todos if not o.get('_correo')])
    resumen['personas'] = len({o['_correo'].lower() for o in todos if o.get('_correo')})
    ultimos = app.supabase.get('vencimiento_avisos', {'fecha': hoy.isoformat()},
                               select='enviado_en') if app.supabase else []
    resumen['avisado_hoy'] = bool(ultimos)
    return resumen


# ============================================================
#  HILO DE FONDO
# ============================================================
def _segundos_hasta(hora):
    ahora = datetime.now(TZ)
    objetivo = ahora.replace(hour=hora, minute=0, second=0, microsecond=0)
    if objetivo <= ahora:
        objetivo += timedelta(days=1)
    return max(60.0, (objetivo - ahora).total_seconds())


def start_avisos_vencimiento(app):
    """Revisa los vencimientos una vez al día, a la hora configurada.

    Mismo patrón que los otros trabajos de fondo: con gunicorn sólo un worker
    toma el flock; en Windows (sin fcntl) no arranca y queda la ruta de cron o
    el botón de la pantalla de proyectos."""
    try:
        import fcntl
    except Exception:
        print('[avisos] fcntl no disponible (dev local): revisión diaria desactivada')
        return
    try:
        import tempfile
        ruta = os.path.join(tempfile.gettempdir(), 'avisos_vencimiento.lock')
        archivo = open(ruta, 'w')
        fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        app._avisos_lock = archivo
    except Exception:
        return          # otro worker ya lo tiene

    hora = _conf(app)['hora']

    def _bucle():
        while True:
            time.sleep(_segundos_hasta(hora))
            # El reclamo de lo incumplido va primero y aparte del listado
            # completo: son dos correos y por eso se ven como dos cosas.
            try:
                revisar_vencimientos(app)
            except Exception as e:
                print(f'[avisos] error en la revisión diaria: {e}')
            # Y su fallo no puede llevarse por delante a la agenda: cada envío
            # responde de lo suyo.
            try:
                if _conf(app)['agenda_diaria']:
                    enviar_pendientes(app, evitar_repetir=True)
            except Exception as e:
                print(f'[avisos] error en la agenda diaria: {e}')

    threading.Thread(target=_bucle, name='avisos-vencimiento', daemon=True).start()
    agenda = 'con agenda de pendientes' if _conf(app)['agenda_diaria'] else 'sin agenda'
    print(f'[avisos] revisión diaria de vencimientos activa '
          f'(a las {hora:02d}:00, {agenda})')
