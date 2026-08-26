# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Bitácora de actividades: nada se borra ni se mueve sin decir por qué.

Un plazo que se puede correr sin dar explicaciones no es un plazo, es una
sugerencia. Y una actividad que se puede borrar sin dejar rastro convierte el
incumplimiento en algo que se arregla con la tecla de suprimir: si el viernes no
llegó, el lunes ya no existía. Este módulo es lo que impide las dos cosas.

Dos reglas, y ninguna se puede esquivar cambiando de pantalla —valen igual en la
lista de Planificación y en las barras del cronograma—:

    BORRAR una actividad         exige justificación
    MOVER una fecha              exige justificación

Lo demás se apunta solo, sin pedir nada: el alta, el cierre y la reapertura. Con
eso el reporte cuenta la historia entera de cada compromiso —qué se hizo, quién,
cuándo— y no sólo los dos momentos incómodos.

Cada apunte guarda COPIAS del título, el proyecto y el responsable. Es a
propósito: tiene que seguir contando la historia cuando la actividad ya no
está, que es justamente el caso que más importa.
"""
from datetime import datetime, timezone

from . import semaforo as _semaforo

TABLA = 'actividad_bitacora'

# Longitud mínima de una justificación. Ocho caracteres no garantizan una buena
# razón, pero descartan «ok», «ya», «.» y el resto de formas de saltarse el
# trámite sin escribir nada. Quien tenga una razón de verdad no se entera de
# este límite.
#
# Bajado de doce a ocho: el motivo más frecuente y más legítimo para borrar es
# que la actividad está repetida, y «duplicado» son nueve caracteres. Obligar a
# rellenar hasta doce hacía que se escribiera «duplicado 123», que es peor
# constancia que «duplicado» a secas —enseña que el mínimo se cubre con
# relleno—. El número vive SÓLO aquí: las pantallas lo reciben del servidor.
MINIMO_JUSTIFICACION = 8

# Las fechas cuyo movimiento hay que justificar, y cómo se llaman por escrito.
FECHAS_VIGILADAS = {
    'due_date':   'fecha de vencimiento',
    'start_date': 'fecha de inicio',
}

ACCIONES = {
    'creado':       'Creada',
    'reprogramado': 'Reprogramada',
    'cumplido':     'Cumplida',
    'reabierto':    'Reabierta',
    'borrado':      'Eliminada',
}


class FaltaJustificacion(Exception):
    """Se intentó borrar o mover una fecha sin explicar por qué.

    Es un error de negocio, no un fallo técnico: la respuesta tiene que decir
    exactamente qué falta para que quien está delante pueda arreglarlo."""


def validar_justificacion(texto):
    """Devuelve la justificación limpia o explica por qué no vale."""
    limpio = ' '.join(str(texto or '').split())
    if not limpio:
        raise FaltaJustificacion('Hace falta justificar este cambio.')
    if len(limpio) < MINIMO_JUSTIFICACION:
        raise FaltaJustificacion(
            f'La justificación es demasiado corta ({len(limpio)} caracteres). '
            f'Escribe al menos {MINIMO_JUSTIFICACION}: quien lea esto dentro de '
            'seis meses tiene que entender por qué se hizo.')
    return limpio[:2000]


def fechas_que_cambian(actual, cambios):
    """Qué fechas vigiladas mueve este cambio. Devuelve {campo: (antes, después)}.

    Sólo cuenta lo que de verdad cambia: reenviar la misma fecha no es mover
    nada y no tiene por qué pedir explicaciones."""
    movidas = {}
    for campo in FECHAS_VIGILADAS:
        if campo not in cambios:
            continue
        antes = (actual.get(campo) or None)
        despues = (cambios.get(campo) or None)
        antes = str(antes)[:10] if antes else None
        despues = str(despues)[:10] if despues else None
        if antes != despues:
            movidas[campo] = (antes, despues)
    return movidas


def _dias(antes, despues):
    """Cuántos días se movió. Positivo si se alargó el plazo."""
    if not antes or not despues:
        return None
    try:
        a = datetime.strptime(antes, '%Y-%m-%d').date()
        d = datetime.strptime(despues, '%Y-%m-%d').date()
        return (d - a).days
    except Exception:
        return None


def _quien(usuario):
    if not usuario:
        return {}
    return {
        'usuario_id': getattr(usuario, 'id', None),
        'usuario_nombre': getattr(usuario, 'full_name', None),
        'usuario_email': getattr(usuario, 'email', None),
    }


def _retrato(tarea, nombre_proyecto=None):
    """Cómo estaba la actividad en el momento del apunte."""
    estado, _ = _semaforo.evaluar(tarea.get('due_date'), tarea.get('status'),
                                  tarea.get('completed_date'))
    return {
        'task_id': tarea.get('id'),
        'project_id': tarea.get('project_id'),
        'titulo': (tarea.get('title') or '')[:300],
        'proyecto': (nombre_proyecto or '')[:200] or None,
        'responsable': (tarea.get('assigned_to') or tarea.get('assigned_email') or '')[:200] or None,
        'estado': tarea.get('status'),
        'avance_pct': tarea.get('progress_pct') or 0,
        'vencia_el': tarea.get('due_date'),
        'semaforo': estado,
    }


def apuntar(app, accion, tarea, usuario, justificacion=None,
            campo=None, antes=None, despues=None, nombre_proyecto=None):
    """Deja constancia. Nunca lanza: un apunte que falla no puede tumbar la
    operación que lo provocó, pero sí tiene que verse en el registro."""
    fila = {
        'accion': accion,
        'campo': campo,
        'valor_antes': antes,
        'valor_despues': despues,
        'dias_movidos': _dias(antes, despues) if campo else None,
        'justificacion': justificacion,
        'creado_en': datetime.now(timezone.utc).isoformat(),
    }
    fila.update(_retrato(tarea, nombre_proyecto))
    fila.update(_quien(usuario))
    try:
        if not app.supabase.insert(TABLA, fila):
            print(f'[bitacora] no se pudo apuntar «{accion}» de '
                  f'{fila.get("titulo")!r} — ¿falta la migración 032?')
    except Exception as e:
        print(f'[bitacora] error apuntando «{accion}»: {e}')


def apuntar_reprogramacion(app, tarea, usuario, movidas, justificacion,
                           nombre_proyecto=None):
    """Un apunte por cada fecha movida: mover el inicio y el vencimiento a la
    vez son dos decisiones, y en el reporte se leen como tales."""
    for campo, (antes, despues) in movidas.items():
        apuntar(app, 'reprogramado', tarea, usuario, justificacion,
                campo=campo, antes=antes, despues=despues,
                nombre_proyecto=nombre_proyecto)


def leer(app, filtros=None, limite=300):
    """El reporte, lo último primero."""
    consulta = {'order': 'creado_en.desc', 'limit': str(limite)}
    for k, v in (filtros or {}).items():
        consulta[k] = v
    return app.supabase.get_q(TABLA, consulta, select='*') or []


def describir(apunte):
    """Una línea legible para el reporte y para el correo."""
    accion = ACCIONES.get(apunte.get('accion'), apunte.get('accion') or '')
    if apunte.get('accion') == 'reprogramado':
        que = FECHAS_VIGILADAS.get(apunte.get('campo'), apunte.get('campo') or 'fecha')
        dias = apunte.get('dias_movidos')
        cuanto = ''
        if dias:
            cuanto = f' ({abs(dias)} día(s) {"más tarde" if dias > 0 else "antes"})'
        return (f'{accion}: {que} {apunte.get("valor_antes") or "—"} → '
                f'{apunte.get("valor_despues") or "—"}{cuanto}')
    return accion
