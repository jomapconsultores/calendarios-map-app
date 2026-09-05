# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Módulo QUIPUX: lo que llega del Municipio, entero en el servidor.

Todo vive aquí: la recolección corre en este servidor, los documentos se
descargan a este disco y la lista se guarda en un SQLite junto a ellos. Nada
depende de un servicio externo, de una clave que configurar ni de una migración
que aplicar. El día que la nube no conteste, esto sigue enseñando qué se debe y
para cuándo, que es exactamente el día en que más falta hace.

Queda un paso que ningún servidor puede dar solo: CuencaDOC levanta a veces un
control de «escriba el texto que se muestra en la imagen». Ese control no se
rodea —está puesto para impedir precisamente que un programa entre por su
cuenta—, pero tampoco hace falta un navegador en el servidor para pasarlo: la
imagen se enseña en esta pantalla y la persona escribe las letras. El servidor
mantiene la sesión abierta entre los dos momentos.

De ahí que el acceso vaya en dos tiempos:

    empezar()  → o entra directo, o devuelve la imagen a resolver
    terminar() → con lo que la persona escribió, cierra el acceso

Entre uno y otro la sesión HTTP se guarda en la sesión del usuario de esta
aplicación. El captcha está atado a esas cookies concretas: usar otras sería
resolver una imagen que ya no vale para nada.
"""
import base64
import os
import threading
from datetime import date

from flask import jsonify, render_template, redirect, flash, request, session
from flask_login import login_required

# Una pasada cada vez. Dos a la vez se pisarían los archivos y, peor, se
# echarían mutuamente de CuencaDOC: el sistema admite una sola sesión.
_EN_CURSO = threading.Lock()
_ULTIMA = {'estado': 'nunca', 'detalle': '', 'resumen': None}

# La sesión de CuencaDOC a medio abrir, mientras la persona lee la imagen.
# Vive en memoria del proceso y se referencia desde la sesión del usuario.
_ACCESOS = {}


def _destino():
    return (os.getenv('QUIPUX_DESTINO')
            or os.path.join(os.path.expanduser('~'), 'Documentos', 'Quipux'))


#  DE DÓNDE SE LEE
#
#  Hay dos almacenes y hasta ahora sólo se miraba uno. La recolección corre en
#  la computadora de la persona —entrar a CuencaDOC necesita su credencial del
#  llavero y, a veces, que escriba el texto de una imagen— y apunta lo recogido
#  en un SQLite que vive en ESE disco. En el servidor ese archivo nace vacío,
#  así que la pantalla enseñaba ceros sin un solo error: indistinguible de «no
#  hay nada pendiente». Por eso no se veía la integración.
#
#  La migración 034 creó `quipux_documentos` justo para esto: la computadora
#  recoge y publica, el servidor enseña. Se lee del SQLite cuando tiene algo
#  (es la máquina que recolecta, y ahí es la fuente inmediata) y de la base
#  cuando no (el servidor, o cualquier teléfono).

def _hay_base(app):
    return getattr(app, 'supabase', None) is not None


def _docs_de_la_base(app, ver='pendientes', area='', bandeja='', busca='', tope=800):
    """Los documentos publicados en la plataforma, con los mismos filtros que
    aplica el almacén local, para que las dos rutas se comporten igual."""
    hoy = date.today().isoformat()
    # `get` sólo sabe filtrar por igualdad, así que lo que necesita
    # comparaciones (el plazo vencido, el estado distinto de cerrado) se
    # resuelve aquí. Son un par de cientos de documentos: cabe de sobra, y
    # evita inventar un método de consulta nuevo para este único caso.
    filtros = {}
    if area:
        filtros['area'] = area
    if bandeja:
        filtros['bandeja'] = bandeja
    docs = app.supabase.get('quipux_documentos', filters=filtros or None) or []

    def abierto(d):
        return (d.get('estado') or 'abierto') != 'cerrado'

    if ver == 'vencidos':
        docs = [d for d in docs if d.get('plazo_fecha') and d['plazo_fecha'] < hoy and abierto(d)]
    elif ver == 'con_plazo':
        docs = [d for d in docs if d.get('plazo_fecha')]
    elif ver == 'pendientes':
        docs = [d for d in docs if abierto(d)]

    if busca:
        b = busca.lower()
        docs = [d for d in docs
                if any(b in str(d.get(c) or '').lower()
                       for c in ('asunto', 'numero', 'remitente', 'tramite', 'referencia'))]

    # El mismo orden que el almacén local: lo que vence antes va primero y lo
    # que no tiene plazo, al final.
    docs.sort(key=lambda d: (d.get('plazo_fecha') or '9999-99-99',
                             '' if d.get('fecha_doc') is None else str(d['fecha_doc'])))
    return docs[:tope]


def _resumen_de_la_base(app):
    """El marcador (total, abiertos, con plazo, vencidos) contado sobre la base."""
    hoy = date.today().isoformat()
    docs = app.supabase.get('quipux_documentos', select='estado,plazo_fecha') or []
    abiertos = [d for d in docs if (d.get('estado') or 'abierto') != 'cerrado']
    return {
        'total': len(docs),
        'abiertos': len(abiertos),
        'con_plazo': len([d for d in abiertos if d.get('plazo_fecha')]),
        'vencidos': len([d for d in abiertos
                         if d.get('plazo_fecha') and d['plazo_fecha'] < hoy]),
        'origen': 'plataforma',
    }


def _resumen_mejor(app):
    """El resumen del SQLite si tiene algo; si no, el de la base.

    Y si no hay nada en ninguno de los dos, lo dice con esas palabras en vez de
    pintar ceros: «vacío» y «aquí nunca se ha recogido nada» son dos cosas muy
    distintas, y confundirlas es lo que hacía parecer que el módulo no iba."""
    local = {}
    try:
        from quipux import almacen
        local = almacen.resumen()
    except Exception as e:
        local = {'total': 0, 'error': str(e)[:200]}
    if local.get('total'):
        local['origen'] = 'esta computadora'
        return local
    if _hay_base(app):
        try:
            r = _resumen_de_la_base(app)
            if r.get('total'):
                return r
            r['sin_recoger'] = True
            return r
        except Exception as e:
            local['error'] = str(e)[:200]
    local.setdefault('total', 0)
    local['sin_recoger'] = True
    local.setdefault('origen', 'esta computadora')
    return local


def _guardar_sesion_compartida(q):
    """Deja la sesión de CuencaDOC donde la encuentre la sincronización de fondo.

    Sin esto, entrar desde la pantalla serviría para una sola pasada: media hora
    después el proceso de fondo no tendría con qué entrar y volvería a pedir el
    texto de la imagen. Guardada, se reutiliza mientras el sistema la dé por
    buena — que es lo que convierte esto en algo que se sincroniza solo."""
    try:
        from quipux import almacen
        almacen.guardar_sesion(q.galletas())
    except Exception as e:
        print(f'[quipux] no se pudo guardar la sesión: {str(e)[:120]}')


def registrar_quipux(app, ctx):
    is_admin = ctx['is_admin']
    user_can = ctx['user_can']

    def _permitido():
        # Es la bandeja de UNA persona en un sistema del Municipio: no es
        # información del despacho que se reparta por roles.
        return is_admin() or user_can('quipux')

    def _no(mensaje='Sin acceso', codigo=403):
        return jsonify({'success': False, 'error': mensaje}), codigo

    # ------------------------------------------------------------------
    #  La pantalla
    # ------------------------------------------------------------------
    @app.route('/quipux')
    @login_required
    def quipux_pantalla():
        if not _permitido():
            flash('No tienes acceso al módulo Quipux.', 'warning')
            return redirect('/dashboard')
        resumen = _resumen_mejor(app)
        return render_template('quipux.html', resumen=resumen,
                               destino=_destino(), ultima=_ULTIMA)

    @app.route('/quipux/api/documentos')
    @login_required
    def quipux_documentos():
        if not _permitido():
            return _no()
        from quipux import almacen
        filtros = dict(
            ver=(request.args.get('ver') or 'pendientes'),
            area=(request.args.get('area') or ''),
            bandeja=(request.args.get('bandeja') or ''),
            busca=(request.args.get('q') or '').strip())
        try:
            docs = almacen.documentos(**filtros)
        except Exception:
            docs = []
        # En el servidor el SQLite está vacío: lo recogido vive en la tabla que
        # publica la computadora. Sin esto la pantalla salía siempre en blanco.
        if not docs and _hay_base(app):
            try:
                docs = _docs_de_la_base(app, **filtros)
            except Exception as e:
                print(f'[quipux] no se pudo leer de la plataforma: {str(e)[:120]}')
        return jsonify({'documentos': docs, 'total': len(docs),
                        'hoy': date.today().isoformat()})

    @app.route('/quipux/api/estado')
    @login_required
    def quipux_estado():
        if not _permitido():
            return jsonify({'vencidos': 0})
        r = _resumen_mejor(app)
        r['pasada'] = _ULTIMA
        return jsonify(r)

    # ------------------------------------------------------------------
    #  Las tareas: qué hay que hacer y para cuándo
    # ------------------------------------------------------------------
    @app.route('/quipux/api/tareas')
    @login_required
    def quipux_tareas():
        if not _permitido():
            return _no()
        from quipux import almacen
        estado = request.args.get('estado') or 'pendiente'
        return jsonify({'tareas': almacen.tareas(estado=estado),
                        'hoy': date.today().isoformat()})

    @app.route('/quipux/api/tareas/<int:tid>', methods=['POST'])
    @login_required
    def quipux_tarea_marcar(tid):
        if not _permitido():
            return _no()
        nuevo = (request.json or {}).get('estado', 'hecha')
        if nuevo not in ('pendiente', 'hecha', 'descartada'):
            return _no('Estado no válido', 400)
        from quipux import almacen
        almacen.marcar_tarea(tid, nuevo)
        return jsonify({'success': True})

    @app.route('/quipux/api/compromisos')
    @login_required
    def quipux_compromisos():
        """Lo que hay que ENTREGAR según el texto de los documentos.

        No es lo mismo que la lista de documentos: un oficio puede pedir tres
        cosas con tres fechas, y la bandeja sólo enseña una línea. Cada
        compromiso viaja con la frase textual de la que sale, para poder
        comprobarlo sin abrir el documento."""
        if not _permitido():
            return _no()
        from quipux import almacen
        estado = request.args.get('estado') or 'pendiente'
        return jsonify({'compromisos': almacen.compromisos(estado=estado),
                        'hoy': date.today().isoformat()})

    @app.route('/quipux/api/compromisos/<int:cid>', methods=['POST'])
    @login_required
    def quipux_compromiso_marcar(cid):
        if not _permitido():
            return _no()
        nuevo = (request.json or {}).get('estado', 'hecho')
        if nuevo not in ('pendiente', 'hecho', 'descartado'):
            return _no('Estado no válido', 400)
        from quipux import almacen
        almacen.marcar_compromiso(cid, nuevo)
        return jsonify({'success': True})

    # ------------------------------------------------------------------
    #  Entrar a CuencaDOC, en dos tiempos
    # ------------------------------------------------------------------
    @app.route('/quipux/api/acceso', methods=['POST'])
    @login_required
    def quipux_acceso():
        """Primer tiempo. O entra directo, o devuelve la imagen a resolver."""
        if not _permitido():
            return _no()
        from quipux.sesion import ErrorQuipux, Quipux
        q = Quipux(registro=lambda *a: None)
        try:
            estado, imagen = q.empezar()
        except ErrorQuipux as e:
            return jsonify({'success': False, 'error': str(e)})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:250]})

        if estado == 'dentro':
            session['quipux_cookies'] = q.galletas()
            _guardar_sesion_compartida(q)
            return jsonify({'success': True, 'estado': 'dentro', 'nombre': q.nombre})

        # Hace falta la persona. Se guarda la sesión a medio abrir: el texto de
        # la imagen sólo vale para ESTAS cookies.
        clave = os.urandom(8).hex()
        _ACCESOS[clave] = q
        session['quipux_acceso'] = clave
        return jsonify({'success': True, 'estado': 'captcha',
                        'imagen': 'data:image/png;base64,'
                                  + base64.b64encode(imagen).decode()})

    @app.route('/quipux/api/acceso/confirmar', methods=['POST'])
    @login_required
    def quipux_acceso_confirmar():
        """Segundo tiempo: lo que la persona leyó en la imagen."""
        if not _permitido():
            return _no()
        clave = session.get('quipux_acceso')
        q = _ACCESOS.get(clave)
        if not q:
            return jsonify({'success': False, 'error':
                            'No hay ningún acceso a medias. Empieza de nuevo.'})
        texto = (request.json or {}).get('texto', '').strip()
        if not texto:
            return jsonify({'success': False, 'error': 'Escribe el texto de la imagen.'})
        from quipux.sesion import ErrorQuipux
        try:
            nombre = q.terminar(texto)
        except ErrorQuipux as e:
            return jsonify({'success': False, 'error': str(e)})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:250]})
        session['quipux_cookies'] = q.galletas()
        _guardar_sesion_compartida(q)
        _ACCESOS.pop(clave, None)
        session.pop('quipux_acceso', None)
        return jsonify({'success': True, 'nombre': nombre})

    # ------------------------------------------------------------------
    #  La pasada
    # ------------------------------------------------------------------
    @app.route('/quipux/api/recoger', methods=['POST'])
    @login_required
    def quipux_recoger():
        """Recorre las áreas y las bandejas con la sesión ya abierta.

        Corre en segundo plano: doscientos documentos con sus adjuntos no caben
        en el tiempo que una página está dispuesta a esperar. La pantalla
        pregunta cómo va."""
        if not _permitido():
            return _no()
        galletas = session.get('quipux_cookies')
        if not galletas:
            return jsonify({'success': False, 'error': 'sin_sesion'})
        if _EN_CURSO.locked():
            return jsonify({'success': False, 'error':
                            'Ya hay una recolección en marcha.'})

        def _trabajo(galletas, destino):
            with _EN_CURSO:
                _ULTIMA.update({'estado': 'en curso', 'detalle': 'entrando…'})
                try:
                    from quipux.recolector import Recolector
                    from quipux.sesion import Quipux
                    r = Recolector(destino=destino,
                                   registro=lambda m: _ULTIMA.update({'detalle': str(m)[:200]}))
                    r.q = Quipux(registro=r.log)
                    r.q.poner_galletas(galletas)
                    if not r.q.sigue_dentro():
                        _ULTIMA.update({'estado': 'sin sesión',
                                        'detalle': 'la sesión de CuencaDOC caducó'})
                        return
                    import os as _os
                    _os.makedirs(r.destino, exist_ok=True)
                    from quipux import almacen, archivo
                    estado = archivo.leer_estado(
                        _os.path.join(r.destino, '_estado.json'))
                    r._recorrer_areas(estado)
                    r._escribir_indices()
                    almacen.guardar(r.documentos)
                    creadas, act = almacen.crear_tareas(r.documentos)
                    resumen = {'documentos': len(r.documentos),
                               'nuevos': sum(1 for d in r.documentos if d.get('nuevo')),
                               'adjuntos': sum(d.get('n_adjuntos', 0) for d in r.documentos),
                               'segundos': 0, 'fallos': r.fallos,
                               'tareas': {'creadas': creadas, 'actualizadas': act}}
                    almacen.apuntar_pasada(resumen)
                    # Y se publica en la plataforma. Sin este paso lo recogido
                    # se quedaba en el disco de esta computadora: la migración
                    # 034 creó la tabla puente, pero nadie la llenaba, así que
                    # desde el servidor o el teléfono no se veía absolutamente
                    # nada. Si falla, se dice en el estado, no en silencio.
                    if _hay_base(app):
                        from quipux import planificacion
                        pub = planificacion.publicar(app.supabase, r.documentos)
                        resumen['publicados'] = pub.get('subidos', 0)
                        if pub.get('error'):
                            resumen['publicar_error'] = pub['error']
                    _ULTIMA.update({'estado': 'terminada', 'resumen': resumen,
                                    'detalle': f'{len(r.documentos)} documento(s), '
                                               f'{creadas} tarea(s) nueva(s)'})
                except Exception as e:
                    _ULTIMA.update({'estado': 'falló', 'detalle': str(e)[:250]})
                finally:
                    try:
                        r.q.salir()
                    except Exception:
                        pass

        threading.Thread(target=_trabajo, args=(galletas, _destino()),
                         name='quipux-recoger', daemon=True).start()
        return jsonify({'success': True, 'estado': 'en curso'})

    @app.route('/quipux/api/recoger/estado')
    @login_required
    def quipux_recoger_estado():
        if not _permitido():
            return _no()
        return jsonify(_ULTIMA)

    return app
