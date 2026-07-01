"""
Feriados nacionales de Ecuador — cálculo automático con la Ley de traslado.

Fuente normativa del traslado del descanso obligatorio:
  - Ley Orgánica para la Optimización de la Jornada Laboral y Descanso Obligatorio
    (R.O. Suplemento 906, 20-dic-2016).
  - Disposición General Primera de la Ley Orgánica Reformatoria a la LOSEP y al
    Código del Trabajo.

Reglas de traslado del DÍA DE DESCANSO:
    martes            -> lunes anterior
    miércoles/jueves  -> viernes de la misma semana
    sábado            -> viernes anterior
    domingo           -> lunes siguiente
    lunes/viernes     -> sin cambio

Feriados que NO se trasladan (se observan en su fecha exacta):
    - Año Nuevo (1 ene)
    - Navidad (25 dic)
    - Martes de Carnaval (y, por convención, el lunes de Carnaval y el
      Viernes Santo, que ya caen en un día de semana fijo)

Verificación ("con la verificación del caso"):
    Para los años presentes en FERIADOS_VERIFICADOS la lista se toma VERBATIM del
    calendario oficial publicado (Viceministerio de Turismo / Ministerio del
    Trabajo) y se marca verificado=True. Para el resto de años se PROYECTA con las
    reglas anteriores y se marca verificado=False. El algoritmo reproduce de forma
    exacta la lista oficial 2026 (validado contra la publicación oficial).
"""
from datetime import date, timedelta

DIAS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')


def _pascua(anio):
    """Domingo de Pascua (algoritmo de Meeus/Butcher, calendario gregoriano)."""
    a = anio % 19
    b = anio // 100
    c = anio % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(anio, mes, dia)


def _trasladar(d):
    """Fecha de descanso trasladada según el día de la semana (regla de la Ley)."""
    wd = d.weekday()  # 0=lunes ... 6=domingo
    if wd == 1:                       # martes -> lunes anterior
        return d - timedelta(days=1)
    if wd == 2:                       # miércoles -> viernes de la semana
        return d + timedelta(days=2)
    if wd == 3:                       # jueves -> viernes de la semana
        return d + timedelta(days=1)
    if wd == 5:                       # sábado -> viernes anterior
        return d - timedelta(days=1)
    if wd == 6:                       # domingo -> lunes siguiente
        return d + timedelta(days=1)
    return d                          # lunes / viernes -> sin cambio


def _base(anio):
    """Feriados base: (nombre, fecha_real, se_traslada, ciudad).

    ciudad=None => feriado NACIONAL. Con ciudad => feriado LOCAL de esa ciudad.
    """
    P = _pascua(anio)
    return [
        # --- Nacionales ---
        ('Año Nuevo',                     date(anio, 1, 1),        False, None),
        ('Carnaval',                      P - timedelta(days=48),  False, None),  # lunes
        ('Carnaval',                      P - timedelta(days=47),  False, None),  # martes
        ('Viernes Santo',                 P - timedelta(days=2),   False, None),
        ('Día del Trabajo',               date(anio, 5, 1),        True,  None),
        ('Batalla de Pichincha',          date(anio, 5, 24),       True,  None),
        ('Primer Grito de Independencia', date(anio, 8, 10),       True,  None),
        ('Independencia de Guayaquil',    date(anio, 10, 9),       True,  None),
        ('Día de los Difuntos',           date(anio, 11, 2),       True,  None),
        ('Independencia de Cuenca',       date(anio, 11, 3),       True,  None),
        ('Navidad',                       date(anio, 12, 25),      False, None),
        # --- Locales ---
        ('Fundación de Cuenca',           date(anio, 4, 12),       True,  'CUENCA'),
        ('Fundación de Guayaquil',        date(anio, 7, 25),       True,  'GUAYAQUIL'),
    ]


def _calcular(anio):
    """Proyección algorítmica de los feriados observados para un año dado."""
    base = _base(anio)
    # Las fechas no trasladables ocupan su posición con prioridad.
    ocupadas = {real for _, real, mueve, _c in base if not mueve}

    resultado = []
    for nombre, real, mueve, ciudad in base:
        obs = real
        if mueve:
            cand = _trasladar(real)
            # Si el traslado cae sobre otro feriado ya observado, se mantiene en
            # su fecha real (evita solapar dos descansos en el mismo día —
            # p. ej. Difuntos 2-nov + Independencia de Cuenca 3-nov).
            obs = real if (cand != real and cand in ocupadas) else cand
            ocupadas.add(obs)
        resultado.append({
            'nombre': nombre,
            'fecha': obs.isoformat(),
            'fecha_real': real.isoformat(),
            'trasladado': obs != real,
            'dia_semana': DIAS[obs.weekday()],
            'ciudad': ciudad,
            'ambito': 'nacional' if ciudad is None else 'local',
        })
    resultado.sort(key=lambda r: r['fecha'])
    return resultado


# Calendario OFICIAL confirmado (verbatim de la publicación oficial).
# año -> [(nombre, fecha_observada_ISO, fecha_real_ISO, ciudad), ...]
# ciudad=None => nacional; con ciudad => feriado local.
FERIADOS_VERIFICADOS = {
    2026: [
        ('Año Nuevo',                     '2026-01-01', '2026-01-01', None),
        ('Carnaval',                      '2026-02-16', '2026-02-16', None),
        ('Carnaval',                      '2026-02-17', '2026-02-17', None),
        ('Viernes Santo',                 '2026-04-03', '2026-04-03', None),
        ('Fundación de Cuenca',           '2026-04-13', '2026-04-12', 'CUENCA'),
        ('Día del Trabajo',               '2026-05-01', '2026-05-01', None),
        ('Batalla de Pichincha',          '2026-05-25', '2026-05-24', None),
        ('Fundación de Guayaquil',        '2026-07-24', '2026-07-25', 'GUAYAQUIL'),
        ('Primer Grito de Independencia', '2026-08-10', '2026-08-10', None),
        ('Independencia de Guayaquil',    '2026-10-09', '2026-10-09', None),
        ('Día de los Difuntos',           '2026-11-02', '2026-11-02', None),
        ('Independencia de Cuenca',       '2026-11-03', '2026-11-03', None),
        ('Navidad',                       '2026-12-25', '2026-12-25', None),
    ],
}

_cache = {}


def feriados(anio):
    """Lista de feriados nacionales observados de un año.

    Devuelve dicts con: nombre, fecha (observada, ISO), fecha_real (ISO),
    trasladado (bool), dia_semana, verificado (bool).
    """
    anio = int(anio)
    if anio in _cache:
        return _cache[anio]

    if anio in FERIADOS_VERIFICADOS:
        out = []
        for nombre, obs, real, ciudad in FERIADOS_VERIFICADOS[anio]:
            od = date.fromisoformat(obs)
            out.append({
                'nombre': nombre,
                'fecha': obs,
                'fecha_real': real,
                'trasladado': obs != real,
                'dia_semana': DIAS[od.weekday()],
                'ciudad': ciudad,
                'ambito': 'nacional' if ciudad is None else 'local',
                'verificado': True,
            })
        out.sort(key=lambda r: r['fecha'])
    else:
        out = _calcular(anio)
        for r in out:
            r['verificado'] = False

    _cache[anio] = out
    return out


def feriados_rango(desde, hasta):
    """Feriados cuya fecha observada está en [desde, hasta) (objetos date)."""
    res = []
    for y in range(desde.year, hasta.year + 1):
        for f in feriados(y):
            fd = date.fromisoformat(f['fecha'])
            if desde <= fd < hasta:
                res.append(f)
    return res


def es_feriado(d, ciudad=None):
    """Si la fecha `d` (date) es día de descanso, devuelve el feriado; si no, None.

    Los feriados nacionales aplican siempre. Los locales aplican solo si `ciudad`
    coincide con la ciudad del feriado (comparación por mayúsculas). Si no se pasa
    ciudad, cualquier feriado (nacional o local) cuenta.
    """
    iso = d.isoformat()
    cu = (ciudad or '').strip().upper()
    match = None
    for f in feriados(d.year):
        if f['fecha'] != iso:
            continue
        if f['ambito'] == 'nacional':
            return f
        if not cu or (f.get('ciudad') or '').upper() == cu:
            match = f
    return match
