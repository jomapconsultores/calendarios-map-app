# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Módulo QUIPUX: lo que llega del Municipio, dentro de la plataforma.

El recolector (paquete `quipux/`) entra a CuencaDOC, baja los documentos y los
deja clasificados en el disco con su índice. Eso resuelve tenerlos; no resuelve
MIRARLOS. Un Excel en una carpeta hay que acordarse de abrirlo, y lo que hay
que acordarse de abrir se deja de abrir a la segunda semana.

Esta pantalla pone lo mismo donde ya se entra todos los días: al lado del
calendario y de la planificación. Lo primero que se ve no es la lista de
documentos —eso es un archivador— sino lo que vence y lo que está vencido, que
es lo que cambia lo que uno hace hoy.

De dónde salen los datos, por orden:

  1. De la tabla `quipux_documentos`, si la base la tiene. Es lo que permite
     mirarlo desde el teléfono o desde el servidor, sin depender de una
     computadora encendida.
  2. Del `INDICE.json` que el recolector deja en el disco. Funciona aunque la
     base no conteste, que es exactamente el caso en el que uno más necesita
     saber qué debía para hoy.

Lo que esta pantalla NO hace es entrar a CuencaDOC. Eso corre en la máquina de
la persona, con su credencial y, cuando el sistema lo pide, con ella tecleando
el texto de la imagen. Desde el servidor no hay nada que se pueda hacer ahí, y
fingir que sí lo habría acabaría en un botón que no funciona.
"""
import json
import os
from datetime import date, datetime

from flask import jsonify, render_template, redirect, flash, request
from flask_login import login_required

TABLA = 'quipux_documentos'

CAMPOS = ('id,numero,asunto,de,tipo,fecha_doc,tramite,referencia,categoria,'
          'area,bandeja,estado,carpeta,enlace,n_adjuntos,plazo_fecha,'
          'plazo_origen,plazo_seguro,actualizado')


def _destino():
    """Dónde dejó el recolector su índice."""
    return (os.getenv('QUIPUX_DESTINO')
            or os.path.join(os.path.expanduser('~'), 'Documentos', 'Quipux'))


def _desde_disco():
    ruta = os.path.join(_destino(), 'INDICE.json')
    try:
        with open(ruta, encoding='utf-8') as f:
            datos = json.load(f)
    except Exception:
        return None
    salida = []
    for d in datos.get('documentos', []):
        plazo = d.get('plazo') or {}
        salida.append({
            **{k: d.get(k) for k in ('id', 'numero', 'asunto', 'de', 'tipo',
                                     'fecha_doc', 'tramite', 'referencia',
                                     'categoria', 'area', 'bandeja', 'estado',
                                     'carpeta', 'enlace', 'n_adjuntos')},
            'plazo_fecha': plazo.get('fecha') or '',
            'plazo_origen': plazo.get('origen') or '',
            'plazo_seguro': bool(plazo.get('seguro')),
        })
    return {'documentos': salida, 'actualizado': datos.get('actualizado'),
            'fuente': 'la computadora'}


def _desde_base(app):
    try:
        filas = app.supabase.get(TABLA, select=CAMPOS)
    except Exception:
        return None
    if not filas:
        return None
    ultima = max((f.get('actualizado') or '') for f in filas) or None
    return {'documentos': filas, 'actualizado': ultima, 'fuente': 'la base'}


def cargar(app):
    """Lo que hay, venga de donde venga. Se dice de dónde vino: mirar una lista
    del jueves creyendo que es la de hoy es peor que no mirar ninguna."""
    return (_desde_base(app) if app.supabase else None) or _desde_disco() or {
        'documentos': [], 'actualizado': None, 'fuente': None}


def _resumen(documentos):
    hoy = date.today().isoformat()
    abiertos = [d for d in documentos if (d.get('estado') or 'abierto') != 'cerrado']
    con_plazo = [d for d in abiertos if d.get('plazo_fecha')]
    return {
        'total': len(documentos),
        'abiertos': len(abiertos),
        'con_plazo': len(con_plazo),
        'vencidos': sum(1 for d in con_plazo if d['plazo_fecha'] < hoy),
        'esta_semana': sum(1 for d in con_plazo if hoy <= d['plazo_fecha'] <= _fin_de_semana()),
        'deducidos': sum(1 for d in con_plazo if not d.get('plazo_seguro')),
        'adjuntos': sum(int(d.get('n_adjuntos') or 0) for d in documentos),
        'areas': sorted({d.get('area') or '' for d in documentos} - {''}),
        'bandejas': sorted({d.get('bandeja') or '' for d in documentos} - {''}),
    }


def _fin_de_semana():
    hoy = date.today()
    from datetime import timedelta
    return (hoy + timedelta(days=6 - hoy.weekday())).isoformat()


def registrar_quipux(app, ctx):
    """Engancha el módulo. `ctx` trae los ayudantes de la aplicación."""
    is_admin = ctx['is_admin']
    user_can = ctx['user_can']

    def _permitido():
        # Es la bandeja de UNA persona en un sistema del Municipio: no es
        # información del despacho que se reparta por roles. La ve quien tiene
        # el módulo concedido, y el administrador.
        return is_admin() or user_can('quipux')

    @app.route('/quipux')
    @login_required
    def quipux_pantalla():
        if not _permitido():
            flash('No tienes acceso al módulo Quipux.', 'warning')
            return redirect('/dashboard')
        datos = cargar(app)
        return render_template('quipux.html',
                               resumen=_resumen(datos['documentos']),
                               actualizado=datos['actualizado'],
                               fuente=datos['fuente'],
                               destino=_destino(),
                               # En el servidor no existe ninguna carpeta de
                               # Documentos ni ningún llavero de Windows, así
                               # que enseñar «/root/Documentos/Quipux» sólo
                               # confunde: la ruta que importa es la de la
                               # computadora desde la que se recoge.
                               en_servidor=(os.name != 'nt'))

    @app.route('/quipux/api/documentos')
    @login_required
    def quipux_documentos():
        if not _permitido():
            return jsonify({'error': 'Sin acceso'}), 403
        datos = cargar(app)
        docs = datos['documentos']

        # Filtros. Se aplican aquí y no en el navegador porque la lista puede
        # ser de varios cientos y el móvil no tiene por qué cargarla entera.
        area = (request.args.get('area') or '').strip()
        bandeja = (request.args.get('bandeja') or '').strip()
        ver = (request.args.get('ver') or 'pendientes').strip()
        busca = (request.args.get('q') or '').strip().lower()
        hoy = date.today().isoformat()

        if area:
            docs = [d for d in docs if (d.get('area') or '') == area]
        if bandeja:
            docs = [d for d in docs if (d.get('bandeja') or '') == bandeja]
        if ver == 'vencidos':
            docs = [d for d in docs if d.get('plazo_fecha') and d['plazo_fecha'] < hoy
                    and (d.get('estado') or 'abierto') != 'cerrado']
        elif ver == 'con_plazo':
            docs = [d for d in docs if d.get('plazo_fecha')]
        elif ver == 'pendientes':
            docs = [d for d in docs if (d.get('estado') or 'abierto') != 'cerrado']
        if busca:
            def coincide(d):
                return any(busca in str(d.get(c) or '').lower()
                           for c in ('asunto', 'numero', 'de', 'tramite', 'referencia'))
            docs = [d for d in docs if coincide(d)]

        # Lo que vence antes, primero; lo que no tiene plazo, al final. Ese es
        # el orden en que hay que trabajar, no el de llegada.
        docs = sorted(docs, key=lambda d: (d.get('plazo_fecha') or '9999-99-99',
                                           d.get('fecha_doc') or ''))
        return jsonify({
            'documentos': docs[:600],
            'total': len(docs),
            'actualizado': datos['actualizado'],
            'fuente': datos['fuente'],
            'hoy': hoy,
        })

    @app.route('/quipux/api/estado')
    @login_required
    def quipux_estado():
        """Para el contador del menú y para saber si la lista está fresca."""
        if not _permitido():
            return jsonify({'vencidos': 0})
        datos = cargar(app)
        r = _resumen(datos['documentos'])
        r['actualizado'] = datos['actualizado']
        r['fuente'] = datos['fuente']
        r['al_dia'] = bool(datos['actualizado'] and
                           datos['actualizado'][:10] == date.today().isoformat())
        return jsonify(r)

    @app.route('/quipux/api/recoger', methods=['POST'])
    @login_required
    def quipux_recoger():
        """Lanza la pasada, y sólo si la aplicación corre en la computadora de
        la persona: entrar a CuencaDOC necesita su credencial del llavero de
        Windows y, cuando el sistema lo pide, que teclee el texto de la imagen.
        Desde el servidor eso no existe, así que aquí se dice en vez de dejar un
        botón que se queda pensando para siempre."""
        if not _permitido():
            return jsonify({'success': False, 'error': 'Sin acceso'}), 403
        if os.name != 'nt':
            return jsonify({'success': False, 'error':
                            'La recolección corre en la computadora de la persona, '
                            'no en el servidor. Ejecútala allí con: python -m quipux'})
        try:
            from quipux.recolector import ejecutar
            r = ejecutar(registro=lambda *a: None)
            return jsonify({'success': True, 'documentos': r['documentos'],
                            'nuevos': r['nuevos'], 'adjuntos': r['adjuntos'],
                            'fallos': r['fallos'][:10]})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:250]})

    return app
