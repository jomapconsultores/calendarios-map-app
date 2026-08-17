# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Módulo CRONOGRAMA: diagrama de Gantt por actividad, asistido por IA.

Un plan agrupa actividades; cada actividad tiene fechas, duración, avance,
responsable y dependencias. Las actividades pueden escribirse a mano o traerse
de la planificación — todas o sólo las que hagan falta, que es como se trabaja:
no toda actividad de un proyecto entra en un cronograma formal.

Las barras se pintan con el SEMÁFORO (app/semaforo.py), no con el estado: una
actividad «en progreso» se veía azul tanto si le sobraba un mes como si llevaba
dos semanas de retraso, y así el diagrama enseñaba en qué se trabaja, no qué se
está incumpliendo — que es lo que se viene a mirar.

La IA hace el trabajo pesado de planificar: propone fechas de inicio y fin,
duraciones y el orden de dependencias. Lo que devuelve es una PROPUESTA, no un
cambio: se muestra al usuario y se aplica sólo lo que él aprueba. Planificar es
una decisión suya; la IA le ahorra el borrador.

La actividad importada guarda `task_id`, así que el avance del plan se puede
refrescar sobre el cronograma sin volver a crearla.
"""
from datetime import datetime, timezone, date, timedelta

from flask import request, jsonify, render_template, redirect, flash
from flask_login import login_required, current_user

from . import ia as ia_mod

CAMPOS_ACTIVIDAD = {'name', 'description', 'responsible', 'start_date', 'end_date',
                    'duration_days', 'progress_pct', 'status', 'priority', 'color',
                    'is_milestone', 'depends_on', 'order_index', 'ai_notes', 'task_id',
                    # La fecha en que se cerró de verdad. Es lo que separa
                    # «cumplida» de «cumplida con retraso»: sin ella, marcar algo
                    # como hecho tres semanas tarde se vería igual de verde que
                    # entregarlo a tiempo, y el cronograma dejaría de servir para
                    # analizar qué se cumplió.
                    'completed_date'}

ESTADOS = ('pending', 'in_progress', 'review', 'done', 'blocked')
PRIORIDADES = ('low', 'medium', 'high', 'urgent')


def _fecha(valor):
    """Acepta 'AAAA-MM-DD', un ISO completo o nada. Devuelve 'AAAA-MM-DD' o None."""
    texto = str(valor or '').strip()[:10]
    if not texto:
        return None
    try:
        return datetime.strptime(texto, '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


def _dias_habiles(inicio, fin):
    """Días laborables entre dos fechas, ambos extremos incluidos.

    La duración de una actividad se mide en días de trabajo: contar los fines de
    semana inflaría todas las estimaciones del cronograma."""
    if not (inicio and fin):
        return None
    d1 = datetime.strptime(inicio, '%Y-%m-%d').date()
    d2 = datetime.strptime(fin, '%Y-%m-%d').date()
    if d2 < d1:
        return None
    dias, actual = 0, d1
    while actual <= d2:
        if actual.weekday() < 5:
            dias += 1
        actual += timedelta(days=1)
    return dias


def registrar_cronograma(app, ctx):
    """Cuelga las rutas del cronograma. `ctx` trae los ayudantes de
    app/__init__.py (ver el mismo patrón en app/directorio.py)."""
    is_admin              = ctx['is_admin']
    user_can              = ctx['user_can']
    _sanitize             = ctx['_sanitize']
    _sanitize_hex_color   = ctx['_sanitize_hex_color']
    leer_tareas           = ctx['leer_tareas']
    db = lambda: app.supabase

    def _sin_acceso():
        return jsonify({'success': False, 'error': 'Sin acceso al módulo Cronograma'}), 403

    def _sin_permiso(que):
        """Tiene el módulo pero no esa acción delicada en concreto.

        Se distingue de _sin_acceso a propósito: «no tienes el módulo» y «no te
        han dado el borrado» son dos cosas distintas, y decirle a alguien la
        primera cuando le pasa la segunda le hace buscar el problema donde no
        está."""
        return jsonify({'success': False,
                        'error': f'No tienes permiso para {que}. Pídeselo al administrador.'}), 403

    def _plan_visible(plan):
        """Un administrador ve todos los planes; el resto, los que creó."""
        return bool(plan) and (is_admin() or plan.get('created_by') == str(current_user.id))

    def _cargar_plan(pid):
        filas = db().get('gantt_plans', {'id': pid}, select='*')
        return filas[0] if filas else None

    def _guardar_actividad(aid, cambios):
        """Guarda tolerando que la migración 031 no esté aplicada.

        `completed_date` sólo existe a partir de esa migración. Si todavía no se
        corrió, PostgREST rechaza el PATCH ENTERO y el usuario ve que marcar una
        actividad como hecha «no hace nada», sin ninguna pista de por qué. Así
        que se reintenta sin esa columna: lo que se pierde es distinguir el
        retraso, no el cambio de estado. Mismo criterio que `_crear_usuario` en
        app/__init__.py con `created_by_admin`."""
        if db().update('gantt_activities', aid, cambios):
            return True
        recorte = {k: v for k, v in cambios.items() if k != 'completed_date'}
        if recorte == cambios:
            return False
        return db().update('gantt_activities', aid, recorte)

    def _volcar_tareas(pid, tareas):
        """Convierte actividades de la planificación en barras de este plan.

        Devuelve las filas creadas, o None si no había ninguna nueva. Lo usan
        los dos caminos: la importación a mano y el cronograma que se abre desde
        un proyecto."""
        existentes = db().get('gantt_activities', {'plan_id': pid},
                              select='task_id,order_index') or []
        ya_importadas = {a.get('task_id') for a in existentes}
        orden = max([a.get('order_index') or 0 for a in existentes], default=0)

        nuevas = []
        for t in tareas:
            if t['id'] in ya_importadas:
                continue
            orden += 1
            inicio = _fecha(t.get('start_date'))
            fin    = _fecha(t.get('due_date'))
            nuevas.append({
                'plan_id': pid, 'task_id': t['id'],
                'name': _sanitize(t.get('title'), 300),
                'description': _sanitize(t.get('description'), 2000) or None,
                'responsible': _sanitize(t.get('assigned_to'), 200) or None,
                'start_date': inicio, 'end_date': fin,
                'duration_days': _dias_habiles(inicio, fin),
                'progress_pct': t.get('progress_pct') or 0,
                'status': t.get('status') if t.get('status') in ESTADOS else 'pending',
                'priority': t.get('priority') if t.get('priority') in PRIORIDADES else 'medium',
                'completed_date': _fecha(t.get('completed_date')),
                'order_index': orden,
                'created_by': current_user.id,
            })
        if not nuevas:
            return None
        filas = db().insert('gantt_activities', nuevas)
        if filas is None:
            for fila in nuevas:                   # migración 031 sin aplicar
                fila.pop('completed_date', None)
            filas = db().insert('gantt_activities', nuevas)
        return filas or []

    def _normalizar_actividad(entrada):
        """Deja una actividad lista para guardar: fechas válidas, duración
        coherente con ellas, avance acotado, dependencias como lista de ids."""
        act = {k: v for k, v in entrada.items() if k in CAMPOS_ACTIVIDAD}

        if 'name' in act:
            act['name'] = _sanitize(act['name'], 300)
        for campo in ('description', 'ai_notes'):
            if campo in act:
                act[campo] = _sanitize(act[campo], 2000) or None
        if 'responsible' in act:
            act['responsible'] = _sanitize(act['responsible'], 200) or None
        for campo in ('start_date', 'end_date', 'completed_date'):
            if campo in act:
                act[campo] = _fecha(act[campo])
        if 'status' in act and act['status'] not in ESTADOS:
            act['status'] = 'pending'
        # Cerrar una actividad sin decir cuándo dejaría el semáforo sin poder
        # distinguir a tiempo de tarde; se apunta hoy, que es cuando se cerró.
        if act.get('status') == 'done' and not act.get('completed_date'):
            act['completed_date'] = date.today().isoformat()
        elif act.get('status'):
            # Reabrir una actividad borra su fecha de cierre: si se quedara,
            # seguiría contando como cumplida en el marcador.
            act['completed_date'] = None
        if 'priority' in act and act['priority'] not in PRIORIDADES:
            act['priority'] = 'medium'
        if 'color' in act and act['color']:
            act['color'] = _sanitize_hex_color(act['color'])
        if 'is_milestone' in act:
            act['is_milestone'] = bool(act['is_milestone'])
        if 'progress_pct' in act:
            try:
                act['progress_pct'] = max(0, min(100, int(float(act['progress_pct']))))
            except (TypeError, ValueError):
                act['progress_pct'] = 0
        if 'depends_on' in act:
            dependencias = act['depends_on']
            act['depends_on'] = [str(d) for d in dependencias if d] if isinstance(dependencias, list) else []
        if 'order_index' in act:
            try:
                act['order_index'] = int(act['order_index'])
            except (TypeError, ValueError):
                act['order_index'] = 0

        # La duración se deriva de las fechas cuando ambas están; si sólo hay
        # inicio y una duración escrita a mano, se respeta la duración.
        if act.get('start_date') and act.get('end_date'):
            calculada = _dias_habiles(act['start_date'], act['end_date'])
            if calculada is not None:
                act['duration_days'] = calculada
        elif 'duration_days' in act:
            try:
                act['duration_days'] = max(0, int(float(act['duration_days'])))
            except (TypeError, ValueError):
                act['duration_days'] = None
        return act

    # ============================================================
    #  PÁGINA
    # ============================================================
    @app.route('/cronograma')
    @login_required
    def cronograma():
        if not user_can('cronograma'):
            flash('No tienes acceso al módulo Cronograma.', 'warning')
            return redirect('/dashboard')
        return render_template('cronograma.html',
                               is_admin_user=is_admin(),
                               ia=ia_mod.estado(),
                               can_planning=user_can('planning'),
                               page_title='Planificación · Cronograma',
                               page_sub='Los plazos del plan en barras, con planificación asistida')

    # ============================================================
    #  PLANES
    # ============================================================
    @app.route('/cronograma/api/planes', methods=['GET'])
    @login_required
    def cronograma_planes():
        if not user_can('cronograma'):
            return _sin_acceso()
        planes = db().get('gantt_plans', select='*') or []
        if not is_admin():
            planes = [p for p in planes if p.get('created_by') == str(current_user.id)]
        actividades = db().get('gantt_activities', select='plan_id,status') or []
        conteo = {}
        for a in actividades:
            resumen = conteo.setdefault(a['plan_id'], {'total': 0, 'done': 0})
            resumen['total'] += 1
            if a.get('status') == 'done':
                resumen['done'] += 1
        for p in planes:
            p.update(conteo.get(p['id'], {'total': 0, 'done': 0}))
        planes.sort(key=lambda p: p.get('created_at') or '', reverse=True)
        return jsonify(planes)

    @app.route('/cronograma/api/planes', methods=['POST'])
    @login_required
    def cronograma_crear_plan():
        if not user_can('cronograma'):
            return _sin_acceso()
        cuerpo = request.get_json() or {}
        nombre = _sanitize(cuerpo.get('name'), 200)
        if not nombre:
            return jsonify({'success': False, 'error': 'El nombre del cronograma es obligatorio'})
        fila = db().insert('gantt_plans', {
            'name': nombre,
            'description': _sanitize(cuerpo.get('description'), 1000) or None,
            'start_date': _fecha(cuerpo.get('start_date')),
            'end_date':   _fecha(cuerpo.get('end_date')),
            'color': _sanitize_hex_color(cuerpo.get('color')),
            'project_id': cuerpo.get('project_id') or None,
            'created_by': current_user.id,
        })
        return jsonify({'success': bool(fila), 'plan': fila[0] if fila else None})

    @app.route('/cronograma/api/planes/<pid>', methods=['PATCH'])
    @login_required
    def cronograma_editar_plan(pid):
        if not user_can('cronograma'):
            return _sin_acceso()
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404
        cuerpo = request.get_json() or {}
        cambios = {}
        if 'name' in cuerpo:
            nombre = _sanitize(cuerpo['name'], 200)
            if not nombre:
                return jsonify({'success': False, 'error': 'El nombre es obligatorio'})
            cambios['name'] = nombre
        if 'description' in cuerpo:
            cambios['description'] = _sanitize(cuerpo['description'], 1000) or None
        for campo in ('start_date', 'end_date'):
            if campo in cuerpo:
                cambios[campo] = _fecha(cuerpo[campo])
        if 'color' in cuerpo:
            cambios['color'] = _sanitize_hex_color(cuerpo['color'])
        if 'status' in cuerpo:
            cambios['status'] = 'archived' if cuerpo['status'] == 'archived' else 'active'
        if not cambios:
            return jsonify({'success': False, 'error': 'Nada que actualizar'})
        cambios['updated_at'] = datetime.now(timezone.utc).isoformat()
        return jsonify({'success': db().update('gantt_plans', pid, cambios)})

    @app.route('/cronograma/api/planes/<pid>', methods=['DELETE'])
    @login_required
    def cronograma_borrar_plan(pid):
        if not user_can('cronograma.eliminar'):
            return _sin_permiso('eliminar cronogramas')
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404
        if not (is_admin() or plan.get('created_by') == str(current_user.id)):
            return jsonify({'success': False, 'error': 'Sin permisos'}), 403
        return jsonify({'success': db().delete('gantt_plans', pid)})

    # ============================================================
    #  ACTIVIDADES
    # ============================================================
    @app.route('/cronograma/api/planes/<pid>/actividades', methods=['GET'])
    @login_required
    def cronograma_actividades(pid):
        if not user_can('cronograma'):
            return _sin_acceso()
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404
        filas = db().get('gantt_activities', {'plan_id': pid}, select='*') or []
        filas.sort(key=lambda a: (a.get('order_index') or 0,
                                  a.get('start_date') or '9999-99-99',
                                  (a.get('name') or '').lower()))
        return jsonify({'plan': plan, 'actividades': filas})

    @app.route('/cronograma/api/planes/<pid>/actividades', methods=['POST'])
    @login_required
    def cronograma_crear_actividad(pid):
        if not user_can('cronograma'):
            return _sin_acceso()
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404
        actividad = _normalizar_actividad(request.get_json() or {})
        if not actividad.get('name'):
            return jsonify({'success': False, 'error': 'El nombre de la actividad es obligatorio'})
        if actividad.get('order_index') is None:
            existentes = db().get('gantt_activities', {'plan_id': pid}, select='order_index') or []
            actividad['order_index'] = max([a.get('order_index') or 0 for a in existentes], default=0) + 1
        actividad['plan_id'] = pid
        actividad['created_by'] = current_user.id
        fila = db().insert('gantt_activities', actividad)
        if not fila and 'completed_date' in actividad:
            actividad.pop('completed_date')      # migración 031 sin aplicar
            fila = db().insert('gantt_activities', actividad)
        return jsonify({'success': bool(fila), 'actividad': fila[0] if fila else None})

    @app.route('/cronograma/api/actividades/<aid>', methods=['PATCH'])
    @login_required
    def cronograma_editar_actividad(aid):
        if not user_can('cronograma'):
            return _sin_acceso()
        filas = db().get('gantt_activities', {'id': aid}, select='*')
        if not filas:
            return jsonify({'success': False, 'error': 'Actividad no encontrada'}), 404
        if not _plan_visible(_cargar_plan(filas[0]['plan_id'])):
            return jsonify({'success': False, 'error': 'Sin permisos'}), 403
        cambios = _normalizar_actividad(request.get_json() or {})
        if 'name' in cambios and not cambios['name']:
            return jsonify({'success': False, 'error': 'El nombre de la actividad es obligatorio'})
        if not cambios:
            return jsonify({'success': False, 'error': 'Nada que actualizar'})
        cambios['updated_at'] = datetime.now(timezone.utc).isoformat()
        return jsonify({'success': _guardar_actividad(aid, cambios)})

    @app.route('/cronograma/api/actividades/<aid>', methods=['DELETE'])
    @login_required
    def cronograma_borrar_actividad(aid):
        if not user_can('cronograma'):
            return _sin_acceso()
        if not user_can('cronograma.eliminar'):
            return _sin_permiso('eliminar actividades')
        filas = db().get('gantt_activities', {'id': aid}, select='plan_id')
        if not filas:
            return jsonify({'success': False, 'error': 'Actividad no encontrada'}), 404
        if not _plan_visible(_cargar_plan(filas[0]['plan_id'])):
            return jsonify({'success': False, 'error': 'Sin permisos'}), 403
        return jsonify({'success': db().delete('gantt_activities', aid)})

    # ============================================================
    #  TRAER ACTIVIDADES DESDE LA PLANIFICACIÓN
    # ============================================================
    @app.route('/cronograma/api/planes/<pid>/tareas-disponibles', methods=['GET'])
    @login_required
    def cronograma_tareas_disponibles(pid):
        """Actividades de la planificación que el usuario ve y que aún no están
        en este plan.

        Se filtran con la MISMA regla de visibilidad que el módulo de
        Planificación: el cronograma no puede convertirse en una puerta trasera
        para ver actividades de proyectos ajenos al rol activo."""
        if not user_can('cronograma'):
            return _sin_acceso()
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404

        tareas = leer_tareas(app, current_user.id)
        ya_importadas = {a.get('task_id') for a in
                         (db().get('gantt_activities', {'plan_id': pid}, select='task_id') or [])}
        proyectos = {p['id']: p['name'] for p in (db().get('projects', select='id,name') or [])}

        disponibles = []
        for t in tareas:
            if t['id'] in ya_importadas:
                continue
            if request.args.get('ocultar_completadas', '1') == '1' and t.get('status') == 'done':
                continue
            disponibles.append({
                'id': t['id'], 'title': t.get('title'), 'phase': t.get('phase'),
                'status': t.get('status'), 'priority': t.get('priority'),
                'start_date': t.get('start_date'), 'due_date': t.get('due_date'),
                'progress_pct': t.get('progress_pct') or 0,
                'assigned_to': t.get('assigned_to'),
                'proyecto': proyectos.get(t.get('project_id')),
            })
        disponibles.sort(key=lambda t: (t.get('due_date') or '9999-99-99', t.get('title') or ''))
        return jsonify(disponibles)

    @app.route('/cronograma/api/planes/<pid>/importar-tareas', methods=['POST'])
    @login_required
    def cronograma_importar_tareas(pid):
        """Convierte actividades de la planificación en barras del cronograma.

        Body: {task_ids: [...]}  — o {todas: true} para traerlas todas."""
        if not user_can('cronograma'):
            return _sin_acceso()
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404

        cuerpo = request.get_json() or {}
        ids_pedidos = set(cuerpo.get('task_ids') or [])
        if not ids_pedidos and not cuerpo.get('todas'):
            return jsonify({'success': False, 'error': 'No se seleccionó ninguna tarea'})

        tareas = leer_tareas(app, current_user.id)
        if ids_pedidos:
            tareas = [t for t in tareas if t['id'] in ids_pedidos]
        elif cuerpo.get('ocultar_completadas', True):
            tareas = [t for t in tareas if t.get('status') != 'done']
        if not tareas:
            return jsonify({'success': False, 'error': 'Ninguna de las tareas seleccionadas está disponible'})

        existentes = db().get('gantt_activities', {'plan_id': pid}, select='task_id,order_index') or []
        ya_importadas = {a.get('task_id') for a in existentes}
        orden = max([a.get('order_index') or 0 for a in existentes], default=0)

        filas = _volcar_tareas(pid, tareas)
        if filas is None:
            return jsonify({'success': True, 'importadas': 0,
                            'mensaje': 'Esas actividades ya estaban en el cronograma'})
        return jsonify({'success': bool(filas), 'importadas': len(filas or []),
                        'actividades': filas or []})

    @app.route('/cronograma/api/proyecto/<pid>', methods=['POST'])
    @login_required
    def cronograma_de_proyecto(pid):
        """El cronograma de un proyecto: si no existe se crea, y se llena con
        las actividades del proyecto.

        Sin esto, ver un proyecto en barras eran cuatro pasos —crear un plan,
        ponerle nombre, abrir «traer del plan», marcar las actividades—, y por
        cuatro pasos la gente no lo hace: el Gantt se quedaba vacío mientras la
        planificación estaba llena. Ahora es un botón."""
        if not user_can('cronograma'):
            return _sin_acceso()
        proyectos = db().get('projects', {'id': pid}, select='*')
        if not proyectos:
            return jsonify({'success': False, 'error': 'Proyecto no encontrado'}), 404
        proyecto = proyectos[0]

        # ¿Ya tenía cronograma? No se crea otro: dos planes para el mismo
        # proyecto es exactamente la duplicación que esto viene a evitar.
        planes = [p for p in (db().get('gantt_plans', {'project_id': pid}, select='*') or [])
                  if _plan_visible(p)]
        if planes:
            plan = planes[0]
            creado = False
        else:
            fila = db().insert('gantt_plans', {
                'name': _sanitize(proyecto.get('name'), 200) or 'Cronograma',
                'description': _sanitize(proyecto.get('description'), 1000) or None,
                'start_date': _fecha(proyecto.get('start_date')),
                'end_date':   _fecha(proyecto.get('due_date')),
                'color': _sanitize_hex_color(proyecto.get('color')),
                'project_id': pid,
                'created_by': current_user.id,
            })
            if not fila:
                return jsonify({'success': False, 'error': 'No se pudo crear el cronograma'})
            plan, creado = fila[0], True

        tareas = leer_tareas(app, current_user.id, {'project_id': pid})
        traidas = _volcar_tareas(plan['id'], tareas)
        return jsonify({'success': True, 'plan_id': plan['id'], 'creado': creado,
                        'importadas': len(traidas or [])})

    @app.route('/cronograma/api/planes/<pid>/sincronizar-tareas', methods=['POST'])
    @login_required
    def cronograma_sincronizar_tareas(pid):
        """Refresca desde la planificación el avance y el estado de las
        actividades que vinieron de ella. Lo que se planificó en el cronograma
        (fechas) no se toca: esas son decisiones del plan."""
        if not user_can('cronograma'):
            return _sin_acceso()
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404

        actividades = [a for a in (db().get('gantt_activities', {'plan_id': pid}, select='*') or [])
                       if a.get('task_id')]
        if not actividades:
            return jsonify({'success': True, 'actualizadas': 0,
                            'mensaje': 'Ninguna actividad viene de la planificación'})
        tareas = {t['id']: t for t in
                  (db().get_in('tasks', 'id', [a['task_id'] for a in actividades],
                               select='id,status,progress_pct,title,assigned_to,'
                                      'completed_date') or [])}
        actualizadas = 0
        for act in actividades:
            tarea = tareas.get(act['task_id'])
            if not tarea:
                continue
            estado = tarea.get('status') if tarea.get('status') in ESTADOS else 'pending'
            avance = tarea.get('progress_pct') or 0
            cerrada = _fecha(tarea.get('completed_date'))
            if (estado == act.get('status') and avance == (act.get('progress_pct') or 0)
                    and cerrada == act.get('completed_date')):
                continue
            if _guardar_actividad(act['id'], {
                    'status': estado, 'progress_pct': avance,
                    'completed_date': cerrada,
                    'updated_at': datetime.now(timezone.utc).isoformat()}):
                actualizadas += 1
        return jsonify({'success': True, 'actualizadas': actualizadas})

    # ============================================================
    #  PLANIFICACIÓN CON IA
    # ============================================================
    @app.route('/cronograma/api/planes/<pid>/planificar', methods=['POST'])
    @login_required
    def cronograma_planificar(pid):
        """Pide a la IA un cronograma para las actividades del plan.

        Devuelve PROPUESTAS, no cambios. Cada propuesta viene emparejada con la
        actividad existente por nombre, para que el usuario apruebe una por una
        en la pantalla y sólo entonces se escriba."""
        if not user_can('cronograma'):
            return _sin_acceso()
        if not user_can('cronograma.planificar_ia'):
            return _sin_permiso('planificar con IA')
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404
        if not ia_mod.disponible():
            return jsonify({'success': False, 'error': ia_mod.estado()['motivo']})

        cuerpo = request.get_json() or {}
        actividades = db().get('gantt_activities', {'plan_id': pid}, select='*') or []
        if not actividades:
            return jsonify({'success': False,
                            'error': 'El cronograma no tiene actividades. '
                                     'Agrégalas o tráelas de la planificación.'})

        # Sólo lo pendiente de planificar, salvo que se pida replanificar todo.
        candidatas = actividades
        if cuerpo.get('solo_sin_fechas'):
            candidatas = [a for a in actividades if not (a.get('start_date') and a.get('end_date'))]
            if not candidatas:
                return jsonify({'success': False,
                                'error': 'Todas las actividades ya tienen fechas. '
                                         'Desmarca «sólo las que faltan» para replanificar.'})

        entrada = [{
            'name': a.get('name'),
            'description': a.get('description') or '',
            'responsible': a.get('responsible') or '',
            'start_date': a.get('start_date') or '',
            'end_date': a.get('end_date') or '',
            'priority': a.get('priority') or 'medium',
            'estado': a.get('status') or 'pending',
            'avance_pct': a.get('progress_pct') or 0,
        } for a in candidatas]

        responsables = sorted({a['responsible'] for a in entrada if a['responsible']})
        try:
            propuesta = ia_mod.planificar_cronograma(
                entrada,
                contexto=_sanitize(cuerpo.get('contexto'), 2000) or (plan.get('description') or ''),
                fecha_inicio=_fecha(cuerpo.get('fecha_inicio')) or plan.get('start_date') or date.today().isoformat(),
                fecha_limite=_fecha(cuerpo.get('fecha_limite')) or plan.get('end_date'),
                responsables=responsables,
                jornada=_sanitize(cuerpo.get('jornada'), 500) or '',
            )
        except ia_mod.IANoDisponible as e:
            return jsonify({'success': False, 'error': str(e)})
        except Exception as e:
            return jsonify({'success': False, 'error': f'La IA no pudo planificar: {str(e)[:200]}'})

        # Emparejar cada propuesta con su actividad por nombre normalizado.
        por_nombre = {(a.get('name') or '').strip().lower(): a for a in actividades}
        propuestas = []
        for sugerida in propuesta.get('actividades') or []:
            actual = por_nombre.get((sugerida.get('name') or '').strip().lower())
            inicio = _fecha(sugerida.get('start_date'))
            fin    = _fecha(sugerida.get('end_date'))
            # Las dependencias vienen por nombre: se traducen a ids reales.
            dependencias = [por_nombre[n.strip().lower()]['id']
                            for n in (sugerida.get('depends_on') or [])
                            if n and n.strip().lower() in por_nombre]
            propuestas.append({
                'actividad_id': actual['id'] if actual else None,
                'nombre': sugerida.get('name'),
                'nueva': actual is None,
                'antes': {'start_date': actual.get('start_date') if actual else None,
                          'end_date': actual.get('end_date') if actual else None,
                          'responsible': actual.get('responsible') if actual else None},
                'cambios': {
                    'start_date': inicio,
                    'end_date': fin,
                    'duration_days': _dias_habiles(inicio, fin) or sugerida.get('duration_days'),
                    'responsible': _sanitize(sugerida.get('responsible'), 200) or None,
                    'priority': sugerida.get('priority') if sugerida.get('priority') in PRIORIDADES else 'medium',
                    'is_milestone': bool(sugerida.get('is_milestone')),
                    'depends_on': dependencias,
                    'ai_notes': _sanitize(sugerida.get('ai_notes'), 2000) or None,
                },
            })
        return jsonify({'success': True, 'propuestas': propuestas,
                        'resumen': propuesta.get('resumen'),
                        'riesgos': propuesta.get('riesgos') or [],
                        'ruta_critica': propuesta.get('ruta_critica') or []})

    @app.route('/cronograma/api/planes/<pid>/aplicar-plan', methods=['POST'])
    @login_required
    def cronograma_aplicar_plan(pid):
        """Escribe las propuestas que el usuario aprobó.

        Body: {propuestas: [{actividad_id, nombre, cambios: {...}}]}"""
        if not user_can('cronograma'):
            return _sin_acceso()
        # Mismo permiso que pedir la propuesta: de nada sirve cerrar la puerta
        # de entrada si la de salida —escribir lo que la IA propuso— queda
        # abierta.
        if not user_can('cronograma.planificar_ia'):
            return _sin_permiso('aplicar una planificación de IA')
        plan = _cargar_plan(pid)
        if not _plan_visible(plan):
            return jsonify({'success': False, 'error': 'Cronograma no encontrado'}), 404

        cuerpo = request.get_json() or {}
        propuestas = cuerpo.get('propuestas') or []
        if not propuestas:
            return jsonify({'success': False, 'error': 'No se aprobó ninguna propuesta'})

        # Sólo se puede escribir sobre actividades de ESTE plan: un id ajeno en el
        # cuerpo de la petición no debe poder tocar el cronograma de otro.
        del_plan = {a['id'] for a in
                    (db().get('gantt_activities', {'plan_id': pid}, select='id') or [])}
        orden = len(del_plan)
        aplicadas, creadas = 0, 0

        for propuesta in propuestas:
            cambios = _normalizar_actividad(propuesta.get('cambios') or {})
            cambios['ai_generated'] = True
            aid = propuesta.get('actividad_id')
            if aid and aid in del_plan:
                cambios['updated_at'] = datetime.now(timezone.utc).isoformat()
                if db().update('gantt_activities', aid, cambios):
                    aplicadas += 1
            elif propuesta.get('nueva') and propuesta.get('nombre'):
                orden += 1
                cambios.update({'plan_id': pid,
                                'name': _sanitize(propuesta['nombre'], 300),
                                'order_index': orden,
                                'created_by': current_user.id})
                if db().insert('gantt_activities', cambios):
                    creadas += 1

        # El análisis de la IA se guarda CON EL PLAN. Antes se enseñaba una vez
        # en la ventana y se perdía al cerrarla: saber cuál es la ruta crítica y
        # qué riesgos hay sirve mientras se ejecuta el plan, no sólo el minuto en
        # que se generó.
        analisis = {}
        if 'resumen' in cuerpo:
            analisis['ai_resumen'] = _sanitize(cuerpo.get('resumen'), 4000) or None
        for campo, clave in (('riesgos', 'ai_riesgos'), ('ruta_critica', 'ai_ruta_critica')):
            if campo in cuerpo:
                lista = cuerpo.get(campo)
                analisis[clave] = [_sanitize(x, 500) for x in lista
                                   if isinstance(lista, list) and x][:30]
        if analisis:
            analisis['ai_generado_en'] = datetime.now(timezone.utc).isoformat()
            analisis['updated_at'] = analisis['ai_generado_en']
            db().update('gantt_plans', pid, analisis)

        return jsonify({'success': True, 'aplicadas': aplicadas, 'creadas': creadas})

    @app.route('/cronograma/api/ia-estado', methods=['GET'])
    @login_required
    def cronograma_ia_estado():
        return jsonify(ia_mod.estado())

    return app
