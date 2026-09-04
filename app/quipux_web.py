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
        from quipux import almacen
        try:
            resumen = almacen.resumen()
        except Exception as e:
            resumen = {'total': 0, 'error': str(e)[:200]}
        return render_template('quipux.html', resumen=resumen,
                               destino=_destino(), ultima=_ULTIMA)

    @app.route('/quipux/api/documentos')
    @login_required
    def quipux_documentos():
        if not _permitido():
            return _no()
        from quipux import almacen
        docs = almacen.documentos(
            ver=(request.args.get('ver') or 'pendientes'),
            area=(request.args.get('area') or ''),
            bandeja=(request.args.get('bandeja') or ''),
            busca=(request.args.get('q') or '').strip())
        return jsonify({'documentos': docs, 'total': len(docs),
                        'hoy': date.today().isoformat()})

    @app.route('/quipux/api/estado')
    @login_required
    def quipux_estado():
        if not _permitido():
            return jsonify({'vencidos': 0})
        from quipux import almacen
        try:
            r = almacen.resumen()
        except Exception:
            r = {'vencidos': 0, 'total': 0}
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
