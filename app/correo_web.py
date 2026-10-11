# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Módulo CORREO: los correos que tienen que ver con algo, en la plataforma.

El correo se lee en la computadora de la persona (`python -m correo`), que es
donde están las credenciales de las nueve cuentas. Esa computadora publica aquí
sólo lo relacionado —lo que cita un quipux, trae una invitación o pone un
plazo— en `correo_mensajes` (migración 043), y el servidor lo enseña.

De dónde se lee, por este orden, igual que el Quipux: el SQLite de esta misma
computadora si lo hay (la máquina que lee el correo, donde es la fuente
inmediata), y la tabla publicada si no (el servidor, cualquier teléfono).

Al recibir la publicación, los plazos pasan además al cronograma
(`correo.cronograma`): es lo que hace que el semáforo los reclame.

Llega también lo POR ATENDER —marcado, o sin leer de los últimos días—, con su
estado como vínculos `marcado`, `sin_leer` y `contestado` (ver
`correo.publicar`). Esa parte llega completa cada vez, así que lo que estaba
por atender y ya no viene se ha atendido: se le quita el estado, y si no tenía
otra relación, se borra. No se queda en el servidor correo que no hace falta.
"""
import json
from datetime import date

from flask import flash, jsonify, redirect, render_template, request
from flask_login import current_user, login_required

TABLA = 'correo_mensajes'
# Si cada cuenta entra, tal como lo vio la última pasada. En `app_config`, como
# el estado de Google: es una sola fila que se sobrescribe, y no merece tabla.
CLAVE_CUENTAS = 'correo_cuentas'
# Sin pasada en más de esto, la pantalla avisa: la computadora que lee el
# correo lleva días apagada, o la tarea dejó de correr.
DIAS_SIN_PASADA = 2


def _es_el_dueno():
    """Sólo el dueño de las nueve cuentas, por su correo de entrada
    (CORREO_DUENO). Ni ser administrador ni tener un rol basta: un
    administrador del despacho no tiene por qué leer el correo personal de
    nadie, y un módulo concedible se acaba concediendo."""
    from flask import current_app
    dueno = (current_app.config.get('CORREO_DUENO') or '').strip().lower()
    return bool(dueno) and current_user.is_authenticated \
        and (getattr(current_user, 'email', '') or '').strip().lower() == dueno
TIPOS = ('quipux', 'cita', 'plazo')
ESTADOS = ('marcado', 'sin_leer', 'contestado')


def _tiene(m, tipo):
    return any(v['tipo'] == tipo for v in m['vinculos'])


def _pendiente(m):
    """Marcado o sin leer, y sin contestar: lo que alguien tiene que mirar."""
    return (_tiene(m, 'marcado') or _tiene(m, 'sin_leer')) and not _tiene(m, 'contestado')


def _hay_base(app):
    return getattr(app, 'supabase', None) is not None


def _de_la_base(app):
    filas = app.supabase.get(TABLA) or []
    for f in filas:
        try:
            f['vinculos'] = json.loads(f.get('vinculos') or '[]')
        except Exception:
            f['vinculos'] = []
    return filas


def _de_esta_computadora():
    """Lo relacionado del SQLite local, ya en la forma que se publica. Vacío si
    aquí no se lee correo —el servidor—, que es lo normal."""
    try:
        from correo import almacen, publicar
        import os
        if not os.path.exists(almacen.ruta_por_defecto()):
            return []
        return publicar.para_publicar()
    except Exception as e:
        print(f'[correo] no se pudo leer el correo de esta computadora: {str(e)[:120]}')
        return []


def _todo(app):
    mensajes = _de_esta_computadora()
    origen = 'esta computadora'
    if not mensajes and _hay_base(app):
        try:
            mensajes = _de_la_base(app)
            origen = 'plataforma'
        except Exception as e:
            print(f'[correo] no se pudo leer de la plataforma: {str(e)[:120]}')
    return mensajes, origen


def _guardar_cuentas(app, estado):
    valor = json.dumps(estado, ensure_ascii=False)
    try:
        if app.supabase.get('app_config', {'key': CLAVE_CUENTAS}, select='key'):
            app.supabase.update('app_config', CLAVE_CUENTAS, {'value': valor}, id_col='key')
        else:
            app.supabase.insert('app_config', {'key': CLAVE_CUENTAS, 'value': valor})
    except Exception as e:
        print(f'[correo] no se pudo guardar el estado de las cuentas: {str(e)[:120]}')


def _cuentas(app, origen):
    """{'ultima_pasada', 'cuentas': [...], 'sin_pasada': días o None}. Las
    que no entran y si hace días que no hay pasada: lo que tiene que saltar a
    la vista antes que la lista."""
    estado = {}
    if origen == 'esta computadora':
        try:
            from correo import publicar
            estado = publicar.estado_cuentas()
        except Exception as e:
            print(f'[correo] no se pudo leer el estado de las cuentas: {str(e)[:120]}')
    elif _hay_base(app):
        try:
            filas = app.supabase.get('app_config', {'key': CLAVE_CUENTAS}, select='value')
            if filas and filas[0].get('value'):
                estado = json.loads(filas[0]['value'])
        except Exception:
            pass
    ultima = estado.get('ultima_pasada')
    dias = None
    if ultima:
        try:
            dias = (date.today() - date.fromisoformat(ultima[:10])).days
        except ValueError:
            pass
    return {'ultima_pasada': ultima,
            'fallan': [c for c in estado.get('cuentas') or [] if c.get('falla_desde')],
            'sin_pasada': dias if dias is not None and dias > DIAS_SIN_PASADA else None}


def _filtrar(mensajes, tipo='', busca=''):
    hoy = date.today().isoformat()
    mensajes = sorted(mensajes, key=lambda m: m.get('fecha') or '', reverse=True)
    if tipo == 'pendiente':
        mensajes = [m for m in mensajes if _pendiente(m)]
    elif tipo == 'relacionado':
        mensajes = [m for m in mensajes if any(v['tipo'] in TIPOS for v in m['vinculos'])]
    elif tipo == 'plazo':
        # Lo que tiene plazo POR DELANTE, lo más próximo primero.
        con = []
        for m in mensajes:
            p = next((v for v in m['vinculos'] if v['tipo'] == 'plazo'), None)
            if p and p['clave'] >= hoy:
                con.append((p['clave'], m))
        mensajes = [m for _, m in sorted(con, key=lambda x: x[0])]
    elif tipo in TIPOS + ESTADOS:
        mensajes = [m for m in mensajes if _tiene(m, tipo)]
    if busca:
        b = busca.lower()
        mensajes = [m for m in mensajes
                    if b in ' '.join(str(m.get(c) or '') for c in ('asunto', 'de', 'resumen')).lower()
                    or any(b in (v.get('clave') or '').lower() for v in m['vinculos'])]
    return mensajes


def _contar(mensajes):
    hoy = date.today().isoformat()
    cuenta = {t: 0 for t in TIPOS + ('marcado', 'sin_leer')}
    cuenta['pendiente'] = sum(1 for m in mensajes if _pendiente(m))
    cuenta['relacionado'] = sum(1 for m in mensajes
                                if any(v['tipo'] in TIPOS for v in m['vinculos']))
    for m in mensajes:
        for t in ('marcado', 'sin_leer'):
            cuenta[t] += _tiene(m, t)
        tipos = {v['tipo'] for v in m['vinculos']}
        for t in TIPOS:
            if t in tipos:
                if t != 'plazo' or any(v['tipo'] == 'plazo' and v['clave'] >= hoy
                                       for v in m['vinculos']):
                    cuenta[t] += 1
    cuenta['total'] = len(mensajes)
    return cuenta


def _retirar_atendidos(app, vigentes):
    """Lo que estaba por atender y no ha vuelto a llegar: se leyó, se
    desmarcó o pasó de fecha. Si además tenía que ver con un quipux, una cita
    o un plazo, se queda sin el estado; si no, se borra."""
    retirados = 0
    for m in _de_la_base(app):
        if m['id'] in vigentes or not any(v['tipo'] in ESTADOS for v in m['vinculos']):
            continue
        quedan = [v for v in m['vinculos'] if v['tipo'] not in ESTADOS]
        if quedan:
            ok = app.supabase.update(TABLA, m['id'], {
                'vinculos': json.dumps(quedan, ensure_ascii=False)})
        else:
            ok = app.supabase.delete(TABLA, m['id'])
        retirados += bool(ok)
    return retirados


def registrar_correo(app, ctx):
    def _permitido():
        # Es el correo de UNA persona: no se reparte por roles ni se concede.
        return _es_el_dueno()

    @app.context_processor
    def _ve_correo():
        try:
            return {'ve_correo': _es_el_dueno()}
        except Exception:
            return {'ve_correo': False}

    @app.route('/correo')
    @login_required
    def correo_pantalla():
        if not _permitido():
            flash('No tienes acceso al módulo Correo.', 'warning')
            return redirect('/dashboard')
        return render_template('correo.html')

    @app.route('/correo/api/mensajes')
    @login_required
    def correo_mensajes():
        if not _permitido():
            return jsonify({'success': False, 'error': 'Sin acceso'}), 403
        mensajes, origen = _todo(app)
        lista = _filtrar(mensajes, request.args.get('tipo') or '',
                         (request.args.get('q') or '').strip())
        fechas = [m.get('actualizado') or m.get('fecha') for m in mensajes]
        return jsonify({'mensajes': lista[:400], 'cuenta': _contar(mensajes),
                        'cuentas': _cuentas(app, origen),
                        'origen': origen, 'hoy': date.today().isoformat(),
                        'ultima': max((f for f in fechas if f), default=None)})

    @app.route('/correo/api/publicar', methods=['POST'])
    def correo_publicar():
        """Guarda lo que manda la computadora que lee el correo, y lleva los
        plazos al cronograma.

        Sin sesión y con el secreto del cron, como `/quipux/api/publicar`:
        quien llama es un programa. Se puede llamar cuantas veces haga falta:
        la clave es la huella del mensaje, así que actualiza en vez de duplicar."""
        secreto = app.config.get('CRON_SECRET') or ''
        recibido = request.headers.get('X-Cron-Secret') or ''
        if not secreto or recibido != secreto:
            return jsonify({'success': False, 'error': 'No autorizado'}), 401
        if not _hay_base(app):
            return jsonify({'success': False,
                            'error': 'el servidor no tiene conexión con su base'}), 503
        cuerpo = request.get_json(silent=True) or {}
        mensajes = cuerpo.get('mensajes') or []
        completo = bool(cuerpo.get('completo'))
        # Una lista vacía sólo vale si es la completa: dice que no queda nada
        # por atender. Sin `completo`, vacía es un error de quien llama.
        if not isinstance(mensajes, list) or (not mensajes and not completo):
            return jsonify({'success': False, 'error': 'No llegó ningún mensaje'}), 400

        from datetime import datetime, timezone
        ahora = datetime.now(timezone.utc).isoformat()
        campos = ('id', 'fecha', 'de', 'asunto', 'resumen', 'cuentas', 'n_adjuntos', 'enlace')
        filas = []
        for m in mensajes:
            if not isinstance(m, dict) or not m.get('id'):
                continue
            fila = {c: m.get(c) for c in campos}
            fila['n_adjuntos'] = int(m.get('n_adjuntos') or 0)
            fila['vinculos'] = json.dumps(m.get('vinculos') or [], ensure_ascii=False)
            fila['actualizado'] = ahora
            filas.append(fila)

        subidos = 0
        # De cien en cien, por lo mismo que los quipux: un lote grande se pasa
        # del tiempo límite en frío y se pierde entero por la última fila.
        for i in range(0, len(filas), 100):
            lote = filas[i:i + 100]
            if not app.supabase.upsert(TABLA, lote, 'id'):
                return jsonify({'success': False, 'subidos': subidos,
                                'error': 'no se pudo guardar; ¿está aplicada la '
                                         'migración 043?'}), 500
            subidos += len(lote)

        atendidos = _retirar_atendidos(app, {f['id'] for f in filas}) if completo else 0
        if isinstance(cuerpo.get('cuentas'), dict):
            _guardar_cuentas(app, cuerpo['cuentas'])

        from correo import cronograma
        try:
            crono = cronograma.volcar(app.supabase, mensajes)
        except Exception as e:
            crono = {'error': str(e)[:200]}
        return jsonify({'success': True, 'recibidos': len(mensajes),
                        'publicados': subidos, 'atendidos': atendidos,
                        'cronograma': crono})

    return app
