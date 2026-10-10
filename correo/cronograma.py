# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Un correo con plazo se convierte en una actividad del cronograma.

Lo mismo que `quipux.planificacion` hace con los oficios, y con las mismas
reglas, para que el semáforo y el aviso de incumplimiento las reclamen igual:

  * Todas cuelgan de un proyecto contenedor, «Correo — plazos», permanente y
    sin fecha de fin.
  * Se apunta de dónde salió cada una (`source='correo'`, `source_id` con la
    huella del mensaje) para que la pasada siguiente actualice la que ya está
    en vez de crear otra igual. El `source_id` lleva delante «correo:» porque
    la columna tiene un índice ÚNICO para todo `tasks` (ver 011): un número a
    secas podría chocar con el de un quipux.
  * Nunca se pisa lo que una persona haya tocado: si la actividad está cerrada
    o cancelada, se respeta aunque el correo siga diciendo la misma fecha.

Todas entran con prioridad media y la marca de que el plazo es DEDUCIDO: sale
de una frase de un correo, no de un sistema que lo diga. Quien la mire tiene
derecho a saberlo, y a descartarla con un clic si la frase hablaba de otra cosa.

Esto corre en el SERVIDOR, al recibir la publicación: la computadora que lee el
correo no alcanza la base.
"""
from datetime import date

ORIGEN = 'correo'
PROYECTO = 'Correo — plazos'
RESPONSABLE = 'Marco Antonio Posligua San Martin'


def _proyecto(db, registro=print):
    try:
        for p in db.get('projects', select='id,name') or []:
            if (p.get('name') or '').strip().lower() == PROYECTO.lower():
                return p['id']
    except Exception as e:
        registro(f'[correo] no se pudo leer los proyectos: {str(e)[:120]}')
        return None
    creado = db.insert('projects', {
        'name': PROYECTO,
        'description': 'Fechas de entrega leídas en el correo de las cuentas de la casa. '
                       'Son deducidas de una frase: revisar antes de dar por buena.',
        'status': 'active',
        'owner': RESPONSABLE,
    })
    if creado:
        registro(f'[correo] creado el proyecto «{PROYECTO}»')
        return creado[0]['id']
    return None


def _plazo(mensaje):
    for v in mensaje.get('vinculos') or []:
        if v.get('tipo') == 'plazo' and v.get('clave'):
            return v
    return None


def _actividad(mensaje, plazo, project_id):
    de = (mensaje.get('de') or '').split('<')[0].strip().strip('"') or mensaje.get('de') or ''
    notas = [f"De: {mensaje.get('de') or ''}",
             f"Plazo deducido: {plazo.get('detalle') or ''}",
             (mensaje.get('resumen') or '')[:600]]
    fila = {
        'title': f"[correo] {(mensaje.get('asunto') or '(sin asunto)')[:170]}",
        'project_id': project_id,
        'assigned_to': RESPONSABLE,
        'due_date': plazo['clave'],
        'start_date': (mensaje.get('fecha') or '')[:10] or date.today().isoformat(),
        'status': 'pending',
        'priority': 'medium',
        'progress_pct': 0,
        'phase': de[:80] or 'Correo',
        'notes': '\n'.join(n for n in notas if n),
        'source': ORIGEN,
        'alert_days': 3,
    }
    if mensaje.get('enlace'):
        fila['source_url'] = mensaje['enlace']
        fila['source_app'] = 'Gmail'
    return fila


def volcar(db, mensajes, registro=print):
    """Lleva al cronograma los correos con plazo por delante. Devuelve el resumen."""
    resumen = {'creadas': 0, 'actualizadas': 0, 'sin_cambios': 0, 'respetadas': 0,
               'error': None}
    hoy = date.today().isoformat()
    con_plazo = [(m, _plazo(m)) for m in mensajes]
    con_plazo = [(m, p) for m, p in con_plazo if p and p['clave'] >= hoy]
    if not con_plazo:
        return resumen

    pid = _proyecto(db, registro)
    if not pid:
        resumen['error'] = 'no se pudo preparar el proyecto «Correo — plazos»'
        return resumen
    try:
        existentes = {str(t.get('source_id')): t for t in
                      db.get('tasks', {'source': ORIGEN},
                             select='id,due_date,status,source_id') or []}
    except Exception as e:
        resumen['error'] = f'no se pudieron leer las actividades: {str(e)[:120]}'
        return resumen

    for m, plazo in con_plazo:
        clave = f"{ORIGEN}:{m['id']}"
        vieja = existentes.get(clave)
        nueva = _actividad(m, plazo, pid)
        if not vieja:
            if db.insert('tasks', {**nueva, 'source_id': clave}):
                resumen['creadas'] += 1
            else:
                resumen['error'] = 'no se pudo crear alguna actividad'
            continue
        if (vieja.get('status') or '') in ('done', 'cancelled'):
            resumen['respetadas'] += 1
            continue
        if (vieja.get('due_date') or '')[:10] != nueva['due_date']:
            if db.update('tasks', vieja['id'], {'due_date': nueva['due_date']}):
                resumen['actualizadas'] += 1
            continue
        resumen['sin_cambios'] += 1
    return resumen
