# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Módulo CRONOGRAMA: el Gantt de un proyecto, asistido por IA.

El cronograma NO se crea: nace del proyecto. Cada proyecto de Planificación
tiene el suyo, y sus barras son sus actividades. Al abrirlo se crea si no
existía, entran las actividades nuevas y se refrescan las que ya estaban.

Antes había que crear un plan, ponerle nombre y luego «traer» las actividades a
mano. Eran dos listas de lo mismo que había que mantener a la par, y con eso
sólo caben dos finales: o alguien se acuerda de sincronizarlas siempre, o el
Gantt acaba enseñando un plan que ya no es el que se está ejecutando. La fuente
de la verdad es la planificación; esto es su otra vista.

Por eso lo que se edita sobre una barra —fechas, responsable, avance, estado—
se escribe TAMBIÉN en la actividad de la planificación. Mover una barra y que
la lista siguiera diciendo la fecha vieja es la misma incoherencia por el otro
lado. Lo que sólo vive aquí son las dependencias, los hitos y el orden: cosas
del dibujo, no del compromiso.

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
    user_has_project_access = ctx['user_has_project_access']
    get_user_projects     = ctx['get_user_projects']
    _bitacora             = ctx['_bitacora']
    nombre_de_proyecto    = ctx['nombre_de_proyecto']
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
        """Quién puede ver un cronograma.

        Si cuelga de un proyecto, manda el proyecto: el cronograma es su otra
        cara y no puede tener permisos propios. Atarlo a quien lo creó daba un
        resultado absurdo —dos personas con acceso al mismo proyecto acababan
        con dos cronogramas distintos, uno cada una—."""
        if not plan:
            return False
        if is_admin():
            return True
        if plan.get('project_id'):
            return user_has_project_access(app, current_user.id, plan['project_id'])
        return plan.get('created_by') == str(current_user.id)

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

    # Lo que significa lo mismo a los dos lados. La izquierda es el nombre en
    # el cronograma; la derecha, en la planificación.
    ESPEJO = {'name': 'title', 'responsible': 'assigned_to',
              'start_date': 'start_date', 'end_date': 'due_date',
              'progress_pct': 'progress_pct', 'status': 'status',
              'completed_date': 'completed_date'}

    def _escribir_en_la_planificacion(actividad, cambios):
        """Lleva a la actividad de la planificación lo que se cambió en la barra.

        Sin esto, mover una barra dejaba la lista diciendo la fecha vieja: dos
        pantallas del mismo compromiso contando cosas distintas. Se escribe sólo
        lo que de verdad cambió y sólo lo que significa lo mismo a los dos
        lados; las dependencias, los hitos y el orden se quedan aquí."""
        tid = actividad.get('task_id')
        if not tid:
            return False
        espejo = {destino: cambios[origen]
                  for origen, destino in ESPEJO.items() if origen in cambios}
        if not espejo:
            return False
        espejo['updated_at'] = datetime.now(timezone.utc).isoformat()
        return db().update('tasks', tid, espejo)

    def _crear_barra(plan, actividad):
        """Crea una barra y, si el cronograma cuelga de un proyecto, la actividad
        que le corresponde en la planificación.

        Lo que nace aquí tiene que nacer también allí. Si no, el Gantt acumula
        barras que la lista no conoce, no entran en el semáforo ni en el correo
        de incumplimientos, y vuelve a haber dos versiones del mismo plan."""
        pid = plan['id']
        if actividad.get('order_index') is None:
            existentes = db().get('gantt_activities', {'plan_id': pid}, select='order_index') or []
            actividad['order_index'] = max([a.get('order_index') or 0 for a in existentes], default=0) + 1

        if plan.get('project_id') and not actividad.get('task_id'):
            tarea = db().insert('tasks', {
                'title': actividad['name'],
                'project_id': plan['project_id'],
                'assigned_to': actividad.get('responsible') or None,
                'start_date': actividad.get('start_date'),
                'due_date': actividad.get('end_date'),
                'status': actividad.get('status') or 'pending',
                'priority': actividad.get('priority') or 'medium',
                'progress_pct': actividad.get('progress_pct') or 0,
                'phase': 'General',
                'source': 'cronograma',
                'created_by': current_user.id,
            })
            if tarea:
                actividad['task_id'] = tarea[0]['id']

        actividad['plan_id'] = pid
        actividad['created_by'] = current_user.id
        fila = db().insert('gantt_activities', actividad)
        if not fila and 'completed_date' in actividad:
            actividad.pop('completed_date')      # migración 031 sin aplicar
            fila = db().insert('gantt_activities', actividad)
        return fila

    def _refrescar_barras(pid, tareas):
        """Pone al día las barras que ya existían con lo que dice la
        planificación. Devuelve cuántas cambiaron.

        Se hace al abrir, no con un botón: un cronograma que hay que acordarse
        de refrescar es un cronograma en el que no se puede confiar."""
        por_tarea = {t['id']: t for t in tareas}
        barras = [a for a in (db().get('gantt_activities', {'plan_id': pid}, select='*') or [])
                  if a.get('task_id') in por_tarea]
        cambiadas = 0
        for barra in barras:
            t = por_tarea[barra['task_id']]
            nuevo = {
                'name': _sanitize(t.get('title'), 300),
                'responsible': _sanitize(t.get('assigned_to'), 200) or None,
                'start_date': _fecha(t.get('start_date')),
                'end_date': _fecha(t.get('due_date')),
                'progress_pct': t.get('progress_pct') or 0,
                'status': t.get('status') if t.get('status') in ESTADOS else 'pending',
                'completed_date': _fecha(t.get('completed_date')),
            }
            distinto = {k: v for k, v in nuevo.items() if barra.get(k) != v}
            if not distinto:
                continue
            if distinto.get('start_date') or distinto.get('end_date'):
                distinto['duration_days'] = _dias_habiles(nuevo['start_date'], nuevo['end_date'])
            distinto['updated_at'] = datetime.now(timezone.utc).isoformat()
            if _guardar_actividad(barra['id'], distinto):
                cambiadas += 1
        return cambiadas

    # En el cronograma la fecha de fin se llama `end_date`; en la planificación,
    # `due_date`. Es el mismo compromiso, así que se vigila igual.
    FECHAS_BARRA = {'start_date': 'start_date', 'end_date': 'due_date'}

    def _fechas_movidas(barra, cambios):
        """Qué fechas mueve este cambio, con los nombres de la planificación."""
        movidas = {}
        for campo, equivalente in FECHAS_BARRA.items():
            if campo not in cambios:
                continue
            antes = str(barra.get(campo))[:10] if barra.get(campo) else None
            despues = str(cambios.get(campo))[:10] if cambios.get(campo) else None
            if antes != despues:
                movidas[equivalente] = (antes, despues)
        return movidas

    def _apuntar_movimiento(barra, cambios, movidas, justificacion):
        """Deja el movimiento en la bitácora, con la ficha de la actividad de la
        planificación para que el reporte lo cuente igual venga de donde venga."""
        if not movidas:
            return
        tid = barra.get('task_id')
        tarea = None
        if tid:
            filas = db().get('tasks', {'id': tid}, select='*')
            tarea = filas[0] if filas else None
        if not tarea:
            # Barra suelta (cronograma sin proyecto): se apunta con lo que hay.
            tarea = {'id': tid, 'title': cambios.get('name') or barra.get('name'),
                     'assigned_to': barra.get('responsible'),
                     'due_date': cambios.get('end_date') or barra.get('end_date'),
                     'status': barra.get('status'), 'progress_pct': barra.get('progress_pct')}
        _bitacora.apuntar_reprogramacion(
            app, tarea, current_user, movidas, justificacion,
            nombre_de_proyecto(app, tarea.get('project_id')))

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
    @app.route('/cronograma/api/proyectos', methods=['GET'])
    @login_required
    def cronograma_proyectos():
        """Los proyectos que se pueden ver en barras: los de Planificación.

        Esta lista sustituye a la de «cronogramas». No se elige un plan de una
        lista propia porque ya no hay planes propios que elegir: hay proyectos,
        y cada uno tiene su Gantt."""
        if not user_can('cronograma'):
            return _sin_acceso()
        proyectos = get_user_projects(app, current_user.id) or []
        # El contador va sobre las ACTIVIDADES de la planificación, no sobre las
        # barras: si sólo contara barras, un proyecto recién abierto saldría
        # vacío hasta que alguien entrara, y no es verdad — el trabajo está.
        tareas = leer_tareas(app, current_user.id, campos='id,status,project_id')
        conteo = {}
        for t in tareas:
            if not t.get('project_id'):
                continue
            r = conteo.setdefault(t['project_id'], {'total': 0, 'done': 0})
            r['total'] += 1
            if t.get('status') == 'done':
                r['done'] += 1
        salida = []
        for pr in proyectos:
            salida.append({
                'id': pr['id'], 'name': pr.get('name') or '(sin nombre)',
                'color': pr.get('color') or '#4f46e5',
                'owner': pr.get('owner'), 'status': pr.get('status'),
                'start_date': pr.get('start_date'), 'due_date': pr.get('due_date'),
                **conteo.get(pr['id'], {'total': 0, 'done': 0}),
            })
        salida.sort(key=lambda x: (x.get('due_date') or '9999-99-99', x['name'].lower()))
        return jsonify(salida)

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
        fila = _crear_barra(plan, actividad)
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
        cuerpo = request.get_json() or {}
        justificacion = cuerpo.pop('justificacion', None)
        cambios = _normalizar_actividad(cuerpo)
        if 'name' in cambios and not cambios['name']:
            return jsonify({'success': False, 'error': 'El nombre de la actividad es obligatorio'})
        if not cambios:
            return jsonify({'success': False, 'error': 'Nada que actualizar'})

        # Arrastrar una barra es mover una fecha, y aquí se pide lo mismo que en
        # la lista. Si el Gantt no lo pidiera, sería la puerta de atrás: bastaría
        # con abrir la otra pantalla para saltarse la regla.
        movidas = _fechas_movidas(filas[0], cambios)
        if movidas:
            try:
                justificacion = _bitacora.validar_justificacion(justificacion)
            except _bitacora.FaltaJustificacion as e:
                return jsonify({'success': False, 'requiere_justificacion': True,
                                'fechas': list(movidas), 'error': str(e)}), 400

        cambios['updated_at'] = datetime.now(timezone.utc).isoformat()
        ok = _guardar_actividad(aid, cambios)
        if ok:
            _escribir_en_la_planificacion(filas[0], cambios)
            _apuntar_movimiento(filas[0], cambios, movidas, justificacion)
        return jsonify({'success': ok})

    @app.route('/cronograma/api/actividades/<aid>', methods=['DELETE'])
    @login_required
    def cronograma_borrar_actividad(aid):
        """Borrar la barra borra la actividad, y exige justificación.

        Quitar sólo la barra no serviría de nada —al abrir el proyecto volvería
        a entrar— y además abriría la puerta de atrás: eliminar aquí sin dar
        explicaciones lo que en la lista sí hay que justificar."""
        if not user_can('cronograma'):
            return _sin_acceso()
        if not user_can('cronograma.eliminar'):
            return _sin_permiso('eliminar actividades')
        filas = db().get('gantt_activities', {'id': aid}, select='*')
        if not filas:
            return jsonify({'success': False, 'error': 'Actividad no encontrada'}), 404
        barra = filas[0]
        if not _plan_visible(_cargar_plan(barra['plan_id'])):
            return jsonify({'success': False, 'error': 'Sin permisos'}), 403

        tarea = None
        if barra.get('task_id'):
            encontradas = db().get('tasks', {'id': barra['task_id']}, select='*')
            tarea = encontradas[0] if encontradas else None

        cuerpo = request.get_json(silent=True) or {}
        if tarea:
            try:
                justificacion = _bitacora.validar_justificacion(cuerpo.get('justificacion'))
            except _bitacora.FaltaJustificacion as e:
                return jsonify({'success': False, 'requiere_justificacion': True,
                                'error': str(e)}), 400
            _bitacora.apuntar(app, 'borrado', tarea, current_user, justificacion,
                              nombre_proyecto=nombre_de_proyecto(app, tarea.get('project_id')))

        ok = db().delete('gantt_activities', aid)
        if ok and tarea:
            db().delete('tasks', tarea['id'])
        return jsonify({'success': ok})


    @app.route('/cronograma/api/proyecto/<pid>', methods=['POST'])
    @login_required
    def cronograma_de_proyecto(pid):
        """El cronograma de un proyecto, puesto al día.

        Se crea si no existía, entran las actividades nuevas y se refrescan las
        que ya estaban. Es lo único que hace falta para abrirlo, y por eso ya no
        hay «nuevo cronograma» ni «traer del plan»: eran cuatro pasos —crear un
        plan, ponerle nombre, abrir «traer», marcar las actividades— para
        conseguir esto mismo, y además había que repetir el cuarto cada vez que
        la planificación cambiaba. Dos listas de lo mismo mantenidas a mano sólo
        pueden acabar de una manera."""
        if not user_can('cronograma'):
            return _sin_acceso()
        # El acceso lo manda el PROYECTO. Sin esta comprobación bastaba con
        # adivinar un id para dejar creado un cronograma sobre el proyecto de
        # otro —vacío, porque las actividades sí se filtran, pero creado—.
        if not (is_admin() or user_has_project_access(app, current_user.id, pid)):
            return jsonify({'success': False, 'error': 'Sin acceso a ese proyecto'}), 403
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
        refrescadas = _refrescar_barras(plan['id'], tareas)
        return jsonify({'success': True, 'plan_id': plan['id'], 'creado': creado,
                        'importadas': len(traidas or []),
                        'refrescadas': refrescadas})

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
        del_plan = {a['id']: a for a in
                    (db().get('gantt_activities', {'plan_id': pid},
                              select='*') or [])}

        # Aplicar una propuesta de la IA mueve fechas —a veces todas— y eso se
        # justifica igual que moverlas a mano. Si no, sería la forma más cómoda
        # de saltarse la regla: pedir un plan y aprobarlo. Basta una
        # justificación para toda la aplicación, porque es una sola decisión.
        mueve_fechas = any(
            _fechas_movidas(del_plan[p['actividad_id']],
                            _normalizar_actividad(p.get('cambios') or {}))
            for p in propuestas
            if p.get('actividad_id') and p.get('actividad_id') in del_plan)
        justificacion = cuerpo.get('justificacion')
        if mueve_fechas:
            try:
                justificacion = _bitacora.validar_justificacion(justificacion)
            except _bitacora.FaltaJustificacion as e:
                return jsonify({'success': False, 'requiere_justificacion': True,
                                'error': str(e)}), 400
        orden = len(del_plan)
        aplicadas, creadas = 0, 0

        for propuesta in propuestas:
            cambios = _normalizar_actividad(propuesta.get('cambios') or {})
            cambios['ai_generated'] = True
            aid = propuesta.get('actividad_id')
            if aid and aid in del_plan:
                cambios['updated_at'] = datetime.now(timezone.utc).isoformat()
                movidas = _fechas_movidas(del_plan[aid], cambios)
                if _guardar_actividad(aid, cambios):
                    aplicadas += 1
                    # Las fechas que aprueba la IA son fechas del compromiso, no
                    # del dibujo: bajan a la planificación como cualquier otra
                    # edición. Si no, la lista seguiría con las de antes.
                    _escribir_en_la_planificacion(del_plan[aid], cambios)
                    _apuntar_movimiento(del_plan[aid], cambios, movidas, justificacion)
            elif propuesta.get('nueva') and propuesta.get('nombre'):
                orden += 1
                cambios.update({'name': _sanitize(propuesta['nombre'], 300),
                                'order_index': orden})
                if _crear_barra(plan, cambios):
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
