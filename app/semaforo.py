# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Semaforización: el mismo color quiere decir lo mismo en todas partes.

Un plazo sólo puede estar en uno de cinco sitios, y de un vistazo hay que saber
en cuál: si va bien, si aprieta, si ya se incumplió, si se cumplió a tiempo o si
se cumplió tarde. Eso es todo lo que hace falta para mirar una planificación y
entenderla sin leerla entera.

Esta tabla vive AQUÍ y en un solo sitio a propósito. La lista de proyectos, las
barras del cronograma, el panel y el correo de incumplimientos pintan lo mismo,
y si cada pantalla tuviera su propio criterio acabarían discrepando: una tarea
en ámbar en una pantalla y en rojo en la otra no es un detalle estético, es el
sistema contradiciéndose sobre si un compromiso está roto o no. Las plantillas
reciben esta tabla tal cual (`SEMAFORO_JSON`) y el JavaScript la usa sin
copiarla.

Los cinco estados:

    verde      En plazo            queda margen
    ambar      Por vencer          vence hoy o dentro de UMBRAL_AVISO días
    rojo       Incumplido          la fecha pasó y no se cerró
    cumplido   Cumplido a tiempo   cerrado en fecha o antes
    tardio     Cumplido con retraso cerrado, pero después del plazo
    gris       Sin plazo           no hay fecha que vigilar

«Cumplido con retraso» existe porque un proyecto no se arregla marcándolo como
hecho tres semanas después: si eso se pintara verde, el historial diría que
todo salió bien y no habría forma de ver quién entrega siempre tarde.
"""
from datetime import datetime, date

# Días de antelación con que un plazo empieza a avisar. Tres días es lo que
# tarda en poder reaccionarse a algo sin trabajar de noche.
UMBRAL_AVISO = 3

# id -> (etiqueta, color, color de fondo, icono). Los colores son los mismos que
# usa el resto de la aplicación para éxito/aviso/peligro.
ESTADOS = {
    'verde':    {'etiqueta': 'En plazo',             'color': '#16a34a',
                 'fondo': '#dcfce7', 'icono': '🟢', 'orden': 3},
    'ambar':    {'etiqueta': 'Por vencer',           'color': '#d97706',
                 'fondo': '#fef3c7', 'icono': '🟡', 'orden': 1},
    'rojo':     {'etiqueta': 'Incumplido',           'color': '#dc2626',
                 'fondo': '#fee2e2', 'icono': '🔴', 'orden': 0},
    'cumplido': {'etiqueta': 'Cumplido',             'color': '#0369a1',
                 'fondo': '#e0f2fe', 'icono': '✅', 'orden': 5},
    'tardio':   {'etiqueta': 'Cumplido con retraso', 'color': '#c2410c',
                 'fondo': '#ffedd5', 'icono': '🟠', 'orden': 4},
    'gris':     {'etiqueta': 'Sin plazo',            'color': '#64748b',
                 'fondo': '#f1f5f9', 'icono': '⚪', 'orden': 2},
}

ESTADOS_CERRADOS = {'done', 'completed', 'cancelled'}


def _a_fecha(valor):
    if not valor:
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    try:
        return datetime.strptime(str(valor)[:10], '%Y-%m-%d').date()
    except Exception:
        return None


def evaluar(due_date, status=None, completed_date=None, hoy=None):
    """En qué color cae este plazo. Devuelve (id_estado, días).

    `días` es la distancia al vencimiento: negativo si ya pasó, positivo si
    falta. Es None cuando no hay fecha, y para lo ya cerrado son los días de
    adelanto (positivo) o de retraso (negativo) con que se entregó."""
    vence = _a_fecha(due_date)
    hoy = hoy or date.today()
    cerrado = (status or '') in ESTADOS_CERRADOS

    if cerrado:
        if not vence:
            return 'cumplido', None
        # Sin fecha de cierre no se puede saber si llegó tarde; se le da por
        # cumplido antes que acusarle de un retraso que no consta.
        hecho = _a_fecha(completed_date)
        if not hecho:
            return 'cumplido', None
        dias = (vence - hecho).days
        return ('cumplido' if dias >= 0 else 'tardio'), dias

    if not vence:
        return 'gris', None
    dias = (vence - hoy).days
    if dias < 0:
        return 'rojo', dias
    if dias <= UMBRAL_AVISO:
        return 'ambar', dias
    return 'verde', dias


def resumen(items, clave_due='due_date', clave_status='status',
            clave_hecho='completed_date', hoy=None):
    """Cuántos hay de cada color. Sirve para el marcador de cumplimiento."""
    conteo = {k: 0 for k in ESTADOS}
    for it in (items or []):
        estado, _ = evaluar(it.get(clave_due), it.get(clave_status),
                            it.get(clave_hecho), hoy)
        conteo[estado] += 1
    return conteo


def porcentaje_cumplimiento(conteo):
    """Qué parte de lo que tenía plazo se cerró a tiempo.

    Lo entregado con retraso NO cuenta como cumplido: cuenta como entregado.
    Es justo la distinción que un porcentaje de avance normal borra, y la que
    hace falta para saber si un equipo llega o no llega."""
    con_plazo = sum(conteo.get(k, 0) for k in ('verde', 'ambar', 'rojo',
                                               'cumplido', 'tardio'))
    if not con_plazo:
        return None
    return round(conteo.get('cumplido', 0) * 100 / con_plazo)
