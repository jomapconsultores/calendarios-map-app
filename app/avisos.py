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

Se manda UN correo al día con todo lo incumplido, no uno por cada cosa. Un
proyecto con doce actividades atrasadas no debe llenar doce veces la bandeja:
lo que hace falta saber es qué se debía, quién respondía y cuánto lleva de
retraso, y eso cabe entero en un solo mensaje.

La tabla `vencimiento_avisos` guarda qué se avisó cada día. Sirve para dos
cosas: que dos procesos (el hilo de fondo y el cron externo) no manden el mismo
aviso dos veces, y que quede constancia de cuándo se avisó de qué.
"""
import os
import html as _html
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytz

# Zona horaria del despacho: el «día» de un vencimiento es el de aquí, no el
# UTC del servidor. Sin esto, entre las 19:00 y la medianoche el servidor ya
# estaría en el día siguiente y daría por incumplido lo que todavía tiene horas.
TZ = pytz.timezone('America/Guayaquil')

DESTINO_POR_DEFECTO = 'jomapconsultores@gmail.com'

# Un proyecto cerrado ya no incumple nada, esté como esté su fecha.
ESTADOS_PROYECTO_CERRADO = {'completed', 'cancelled'}

# Evita que dos peticiones simultáneas al cron manden el aviso por duplicado
# dentro del mismo proceso. Entre procesos lo impide la tabla.
_LOCK = threading.Lock()


# ============================================================
#  CONFIGURACIÓN DE CORREO
# ============================================================
def _conf(app):
    """Datos del servidor de correo. Sin SMTP_HOST el aviso queda desactivado."""
    c = app.config
    usuario = c.get('SMTP_USER') or os.getenv('SMTP_USER', '')
    return {
        'host':     c.get('SMTP_HOST') or os.getenv('SMTP_HOST', ''),
        'port':     int(c.get('SMTP_PORT') or os.getenv('SMTP_PORT', '587')),
        'user':     usuario,
        'password': c.get('SMTP_PASSWORD') or os.getenv('SMTP_PASSWORD', ''),
        'remitente': (c.get('SMTP_FROM') or os.getenv('SMTP_FROM', '') or usuario),
        'ssl':      bool(c.get('SMTP_SSL')),
        'destino':  c.get('AVISO_EMAIL') or os.getenv('AVISO_EMAIL', DESTINO_POR_DEFECTO),
        'hora':     int(c.get('AVISO_HORA') or os.getenv('AVISO_HORA', '8')),
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


def _nombres_de_usuarios(app, ids):
    """id -> nombre legible, para cuando el responsable no está escrito a mano."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    filas = app.supabase.get_in('users', 'id', ids, select='id,full_name,email') or []
    return {f['id']: (f.get('full_name') or f.get('email') or '') for f in filas}


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

    El módulo de Actividades baja del To-Do de Microsoft los correos marcados de
    Outlook y los pendientes personales de cada cuenta. Reclamarlos como
    incumplimiento llenaría el correo diario de decenas de líneas que nadie
    pactó con nadie. Se vigila lo que sí es un compromiso: lo que cuelga de un
    proyecto, y lo que se creó aquí a mano."""
    if t.get('project_id'):
        return True
    return t.get('source') != 'ms_todo' and t.get('source_app') != 'Outlook'


def incumplidos(app, hoy=None):
    """Proyectos y actividades cuya fecha ya pasó sin estar terminados."""
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

    # Nombre del responsable: primero lo escrito en la ficha, y si está vacío,
    # quien la creó. Un aviso que no dice de quién es no sirve para reclamar.
    nombres = _nombres_de_usuarios(
        app, [p.get('created_by') for p in proyectos] +
             [t.get('created_by') for t in tareas])
    nombre_proyecto = {p['id']: p.get('name') for p in
                       (app.supabase.get('projects', select='id,name') or [])}

    for p in proyectos:
        p['_responsable'] = p.get('owner') or nombres.get(p.get('created_by')) or '—'
        p['_dias'] = _dias_retraso(p.get('due_date'), hoy)
    for t in tareas:
        t['_responsable'] = (t.get('assigned_to') or t.get('assigned_email')
                             or nombres.get(t.get('created_by')) or '—')
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


def _ya_avisado(app, hoy):
    filas = app.supabase.get('vencimiento_avisos', {'fecha': hoy.isoformat()},
                             select='tipo,ref_id')
    if filas:
        return {(f.get('tipo'), f.get('ref_id')) for f in filas}
    # Sin filas puede ser «no hay nada» o «la tabla no existe»: en ambos casos
    # el respaldo en memoria es lo único que queda.
    if _avisado_en_memoria['fecha'] == hoy:
        return set(_avisado_en_memoria['claves'])
    return set()


def _registrar_avisos(app, hoy, destino, items):
    if _avisado_en_memoria['fecha'] != hoy:
        _avisado_en_memoria['fecha'] = hoy
        _avisado_en_memoria['claves'] = set()
    filas = []
    for tipo, obj, titulo in items:
        _avisado_en_memoria['claves'].add((tipo, obj['id']))
        filas.append({
            'tipo': tipo, 'ref_id': obj['id'], 'fecha': hoy.isoformat(),
            'destinatario': destino, 'titulo': titulo[:300],
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


def _componer(hoy, proyectos, tareas):
    e = _html.escape
    total = len(proyectos) + len(tareas)
    asunto = f'⚠️ Incumplimiento: {total} compromiso(s) vencido(s) — {_fmt(hoy)}'

    tabla_p = _tabla_html(
        f'📁 Proyectos incumplidos ({len(proyectos)})',
        ['Proyecto', 'Responsable', 'Vencía el', 'Retraso'],
        [[e(p.get('name') or '(sin nombre)'), e(p['_responsable']),
          _fmt(p.get('due_date')),
          f"<strong style='color:#b91c1c'>{p['_dias']} día(s)</strong>"]
         for p in proyectos])

    tabla_t = _tabla_html(
        f'📋 Actividades incumplidas ({len(tareas)})',
        ['Actividad', 'Proyecto', 'Responsable', 'Vencía el', 'Retraso', 'Avance'],
        [[e(t.get('title') or '(sin título)'), e(t['_proyecto']),
          e(t['_responsable']), _fmt(t.get('due_date')),
          f"<strong style='color:#b91c1c'>{t['_dias']} día(s)</strong>",
          f"{t.get('progress_pct') or 0}%"]
         for t in tareas])

    cuerpo_html = f"""<div style="font-family:Arial,sans-serif;max-width:760px;margin:0 auto;color:#0f172a">
  <div style="background:#b91c1c;color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">
    <div style="font-size:19px;font-weight:bold">⚠️ Compromisos incumplidos</div>
    <div style="font-size:13px;opacity:.9">Revisión del {_fmt(hoy)} · {total} pendiente(s) fuera de plazo</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:0;border-radius:0 0 12px 12px;padding:8px 20px 24px">
    <p style="font-size:14px;color:#334155">
      Los siguientes compromisos pasaron su fecha de vencimiento sin haberse
      completado. Constan como <strong>INCUMPLIDOS</strong>.
    </p>
    {tabla_p}
    {tabla_t}
    <p style="font-size:12px;color:#64748b;margin-top:26px;border-top:1px solid #e2e8f0;padding-top:12px">
      Aviso automático del calendario de vencimientos · CalendarioMAP.
      Mientras el trabajo siga sin cerrarse, este correo se repite una vez al día.
    </p>
  </div>
</div>"""

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
#  LA REVISIÓN
# ============================================================
def revisar_vencimientos(app, forzar=False):
    """Un ciclo completo: mirar qué está incumplido, avisar y dejar constancia.

    `forzar` reenvía aunque ya se hubiera avisado hoy (sirve para probar que el
    correo sale de verdad, sin esperar al día siguiente)."""
    if not app.supabase:
        return {'success': False, 'error': 'Sin base de datos'}
    with _LOCK:
        hoy = hoy_local()
        try:
            datos = incumplidos(app, hoy)
        except ConsultaFallida as e:
            # No se calla ni se da por bueno: se dice que HOY NO SE PUDO MIRAR.
            print(f'[avisos] no se pudo comprobar los vencimientos: {e}')
            return {'success': False, 'enviado': False, 'avisados': 0,
                    'error': f'No se pudo consultar la base ({e}). No se avisa '
                             'nada porque no se ha podido mirar, no porque no '
                             'haya incumplimientos.'}
        proyectos, tareas = datos['proyectos'], datos['tareas']
        total_detectado = len(proyectos) + len(tareas)

        if not forzar:
            ya = _ya_avisado(app, hoy)
            proyectos = [p for p in proyectos if ('proyecto', p['id']) not in ya]
            tareas = [t for t in tareas if ('actividad', t['id']) not in ya]

        if not proyectos and not tareas:
            return {'success': True, 'detectados': total_detectado, 'avisados': 0,
                    'enviado': False,
                    'mensaje': 'Nada nuevo que avisar' if total_detectado
                               else 'Sin incumplimientos'}

        if not correo_configurado(app):
            return {'success': False, 'detectados': total_detectado, 'avisados': 0,
                    'enviado': False,
                    'error': 'Correo no configurado: define SMTP_HOST, SMTP_USER, '
                             'SMTP_PASSWORD y SMTP_FROM en el entorno.'}

        asunto, cuerpo_html, cuerpo_texto = _componer(hoy, proyectos, tareas)
        destino = _conf(app)['destino']
        ok, error = enviar_correo(app, asunto, cuerpo_html, cuerpo_texto, [destino])
        if not ok:
            return {'success': False, 'detectados': total_detectado, 'avisados': 0,
                    'enviado': False, 'error': error}

        _registrar_avisos(app, hoy, destino,
                          [('proyecto', p, p.get('name') or '') for p in proyectos] +
                          [('actividad', t, t.get('title') or '') for t in tareas])
        print(f'[avisos] incumplimientos avisados a {destino}: '
              f'{len(proyectos)} proyecto(s), {len(tareas)} actividad(es)')
        return {'success': True, 'detectados': total_detectado,
                'avisados': len(proyectos) + len(tareas),
                'proyectos': len(proyectos), 'actividades': len(tareas),
                'enviado': True, 'destino': destino}


def estado(app):
    """Resumen para la pantalla de administración."""
    hoy = hoy_local()
    cf = _conf(app)
    resumen = {
        'correo_configurado': correo_configurado(app),
        'destino': cf['destino'],
        'hora_revision': cf['hora'],
        'proyectos_incumplidos': 0,
        'actividades_incumplidas': 0,
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
            try:
                revisar_vencimientos(app)
            except Exception as e:
                print(f'[avisos] error en la revisión diaria: {e}')

    threading.Thread(target=_bucle, name='avisos-vencimiento', daemon=True).start()
    print(f'[avisos] revisión diaria de vencimientos activa (a las {hora:02d}:00)')
