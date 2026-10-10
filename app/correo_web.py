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
"""
import json
from datetime import date

from flask import flash, jsonify, redirect, render_template, request
from flask_login import login_required

TABLA = 'correo_mensajes'
TIPOS = ('quipux', 'cita', 'plazo')


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


def _filtrar(mensajes, tipo='', busca=''):
    hoy = date.today().isoformat()
    if tipo == 'plazo':
        # Lo que tiene plazo POR DELANTE, lo más próximo primero.
        con = []
        for m in mensajes:
            p = next((v for v in m['vinculos'] if v['tipo'] == 'plazo'), None)
            if p and p['clave'] >= hoy:
                con.append((p['clave'], m))
        mensajes = [m for _, m in sorted(con, key=lambda x: x[0])]
    elif tipo in TIPOS:
        mensajes = [m for m in mensajes if any(v['tipo'] == tipo for v in m['vinculos'])]
    if busca:
        b = busca.lower()
        mensajes = [m for m in mensajes
                    if b in ' '.join(str(m.get(c) or '') for c in ('asunto', 'de', 'resumen')).lower()
                    or any(b in (v.get('clave') or '').lower() for v in m['vinculos'])]
    return mensajes


def _contar(mensajes):
    hoy = date.today().isoformat()
    cuenta = {t: 0 for t in TIPOS}
    for m in mensajes:
        tipos = {v['tipo'] for v in m['vinculos']}
        for t in TIPOS:
            if t in tipos:
                if t != 'plazo' or any(v['tipo'] == 'plazo' and v['clave'] >= hoy
                                       for v in m['vinculos']):
                    cuenta[t] += 1
    cuenta['total'] = len(mensajes)
    return cuenta


def registrar_correo(app, ctx):
    is_admin = ctx['is_admin']
    user_can = ctx['user_can']

    def _permitido():
        # Es el correo de UNA persona: no se reparte por roles.
        return is_admin() or user_can('correo')

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
        mensajes = (request.get_json(silent=True) or {}).get('mensajes') or []
        if not isinstance(mensajes, list) or not mensajes:
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

        from correo import cronograma
        try:
            crono = cronograma.volcar(app.supabase, mensajes)
        except Exception as e:
            crono = {'error': str(e)[:200]}
        return jsonify({'success': True, 'recibidos': len(mensajes),
                        'publicados': subidos, 'cronograma': crono})

    return app
