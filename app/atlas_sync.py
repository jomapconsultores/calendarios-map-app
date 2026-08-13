# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Puente entre la agenda de este sistema y las reuniones de ATLAS.

Lo que se agenda en ATLAS aparece aquí, y lo que se agenda aquí aparece en
ATLAS. Son dos bases de datos distintas (dos proyectos de Supabase separados),
así que el puente vive aquí y habla con las dos por su API.

EL DISPARADOR ES EL CALENDARIO «ATLAS» —el que tiene atlas.cenest@gmail.com—.
Sólo cruzan el puente las citas de ese calendario: una cita personal o de JOMAP
no tiene por qué aparecer en la agenda de ATLAS, ni al revés.

CÓMO SE DECIDE QUIÉN MANDA
ATLAS no guarda fecha de modificación en `reuniones`: no se le puede preguntar
«¿esto cambió?». Por eso se guarda una HUELLA del contenido en cada
sincronización (`atlas_hash`) y en la siguiente pasada se compara:

    ATLAS ≠ huella  y  nuestro = huella   -> cambió allá     -> se trae
    ATLAS = huella  y  nuestro ≠ huella   -> cambió aquí     -> se lleva
    ATLAS ≠ huella  y  nuestro ≠ huella   -> cambiaron ambos -> manda ATLAS
    los dos = huella                      -> nada que hacer

Que mande ATLAS en el empate no es arbitrario: es el sistema que el usuario
señaló como disparador, y perder un cambio nuestro es menos grave que
sobrescribir la agenda de la que dependen las clases.

QUÉ NO HACE
No borra en ATLAS. Si una reunión desaparece allá, la cita de aquí se marca como
cancelada (reversible) en vez de eliminarse: un borrado accidental en un sistema
no debe destruir el historial del otro.
"""
import hashlib
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, date, time as _time, timedelta, timezone

import requests as req_lib

ATLAS_URL = os.getenv('ATLAS_SUPABASE_URL', 'https://naubddczohedvtywmmmy.supabase.co')
CALENDARIO_ATLAS = os.getenv('ATLAS_CALENDAR_ID', 'atlas')
_TIMEOUT = (6, 25)

# Estados de ATLAS que se consideran vigentes. Una reunión cancelada allá no
# debe crear una cita nueva aquí.
ESTADOS_VIGENTES = {'programada', 'confirmada', 'pendiente'}

_lock = threading.Lock()


def disponible():
    """True si hay clave para hablar con la base de ATLAS."""
    return bool(os.getenv('ATLAS_SUPABASE_KEY'))


def estado():
    if not disponible():
        return {'disponible': False,
                'motivo': 'Falta ATLAS_SUPABASE_KEY en el servidor'}
    return {'disponible': True, 'motivo': None,
            'url': ATLAS_URL, 'calendario': CALENDARIO_ATLAS}


def _cabeceras():
    clave = os.getenv('ATLAS_SUPABASE_KEY', '')
    return {'apikey': clave, 'Authorization': f'Bearer {clave}',
            'Content-Type': 'application/json'}


# ============================================================
#  ACCESO A LA BASE DE ATLAS
# ============================================================
def _atlas_get(recurso, params=''):
    r = req_lib.get(f'{ATLAS_URL}/rest/v1/{recurso}?{params}',
                    headers=_cabeceras(), timeout=_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f'ATLAS respondió HTTP {r.status_code}: {r.text[:160]}')
    return r.json()


def _atlas_insert(recurso, datos):
    r = req_lib.post(f'{ATLAS_URL}/rest/v1/{recurso}',
                     headers=dict(_cabeceras(), Prefer='return=representation'),
                     json=datos, timeout=_TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f'ATLAS rechazó el alta HTTP {r.status_code}: {r.text[:160]}')
    cuerpo = r.json()
    return cuerpo[0] if isinstance(cuerpo, list) and cuerpo else cuerpo


def _atlas_update(recurso, id_val, datos):
    r = req_lib.patch(f'{ATLAS_URL}/rest/v1/{recurso}?id=eq.{id_val}',
                      headers=dict(_cabeceras(), Prefer='return=minimal'),
                      json=datos, timeout=_TIMEOUT)
    return r.status_code in (200, 204)


# ============================================================
#  TRADUCCIÓN ENTRE LOS DOS MODELOS
# ============================================================
def _huella(titulo, fecha, hora_ini, hora_fin, encargado, tema, asistentes):
    """Huella estable del contenido de una reunión.

    Sólo entran los campos que se sincronizan: así un cambio en algo que no
    cruza el puente (por ejemplo el mapa de una cita nuestra) no se confunde con
    una modificación que haya que propagar."""
    crudo = '|'.join(str(x or '').strip().lower() for x in
                     (titulo, fecha, hora_ini, hora_fin, encargado, tema, asistentes))
    return hashlib.sha256(crudo.encode('utf-8')).hexdigest()[:32]


def _reunion_a_huella(r):
    return _huella(r.get('titulo'), r.get('fecha'),
                   str(r.get('hora_inicio') or '')[:5], str(r.get('hora_fin') or '')[:5],
                   r.get('encargado'), r.get('tema'), r.get('asistentes'))


def _cita_a_huella(c):
    ini, fin = _partir_horas(c)
    return _huella(c.get('title'), ini[0] if ini else '', ini[1] if ini else '',
                   fin, c.get('encargado'), c.get('tema'), c.get('invitados'))


def _partir_horas(cita):
    """De start_time/end_time ISO a (fecha, hora_inicio) y hora_fin."""
    def _leer(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        except ValueError:
            return None
    d1, d2 = _leer(cita.get('start_time')), _leer(cita.get('end_time'))
    if not d1:
        return None, ''
    return (d1.date().isoformat(), d1.strftime('%H:%M')), (d2.strftime('%H:%M') if d2 else '')


def reunion_a_cita(r, zona):
    """Campos de una reunión de ATLAS traducidos a una cita de este sistema."""
    fecha = str(r.get('fecha') or '')[:10]
    if not fecha:
        return None
    hi = str(r.get('hora_inicio') or '09:00')[:5]
    hf = str(r.get('hora_fin') or '')[:5]
    try:
        inicio = zona.localize(datetime.combine(
            date.fromisoformat(fecha), _time.fromisoformat(hi)))
    except Exception:
        return None
    if hf:
        try:
            fin = zona.localize(datetime.combine(
                date.fromisoformat(fecha), _time.fromisoformat(hf)))
        except Exception:
            fin = inicio + timedelta(hours=1)
    else:
        fin = inicio + timedelta(hours=1)
    if fin <= inicio:                       # reunión que cruza la medianoche
        fin += timedelta(days=1)

    return {
        'title':       (r.get('titulo') or 'Reunión ATLAS')[:255],
        'calendar_id': CALENDARIO_ATLAS,
        'start_time':  inicio.isoformat(),
        'end_time':    fin.isoformat(),
        'encargado':   (r.get('encargado') or '')[:255] or None,
        'tema':        r.get('tema') or None,
        'invitados':   r.get('asistentes') or None,
        'status':      'confirmed',
        'client_name': (r.get('encargado') or 'ATLAS')[:255],
    }


def cita_a_reunion(c):
    """Campos de una cita de este sistema traducidos a una reunión de ATLAS."""
    horas, hora_fin = _partir_horas(c)
    if not horas:
        return None
    return {
        'titulo':      (c.get('title') or 'Reunión')[:255],
        'fecha':       horas[0],
        'hora_inicio': horas[1],
        'hora_fin':    hora_fin or None,
        'encargado':   (c.get('encargado') or '')[:255] or None,
        'tema':        c.get('tema') or None,
        'asistentes':  c.get('invitados') or None,
        'estado':      'Programada',
    }


# ============================================================
#  SINCRONIZACIÓN
# ============================================================
def sincronizar(app, zona, deadline_segundos=90):
    """Una pasada completa en los dos sentidos. Devuelve el resumen."""
    if not disponible():
        return {'success': False, 'error': estado()['motivo']}
    if not _lock.acquire(blocking=False):
        return {'success': False, 'error': 'Ya hay una sincronización en curso'}
    try:
        return _sincronizar(app, zona, deadline_segundos)
    finally:
        _lock.release()


def _sincronizar(app, zona, deadline_segundos):
    limite = time.monotonic() + deadline_segundos
    res = {'success': True, 'traidas': 0, 'llevadas': 0, 'actualizadas_aqui': 0,
           'actualizadas_alla': 0, 'canceladas': 0, 'conflictos': 0, 'errores': []}

    try:
        reuniones = _atlas_get('reuniones', 'select=*&order=fecha.desc')
    except Exception as e:
        return {'success': False, 'error': str(e)[:200]}

    citas = app.supabase.get('appointments', {'calendar_id': CALENDARIO_ATLAS}, select='*') or []
    por_reunion = {c['atlas_reunion_id']: c for c in citas if c.get('atlas_reunion_id')}
    ahora = datetime.now(timezone.utc).isoformat()

    # ── 1. ATLAS → sistema ────────────────────────────────────────────────
    ids_atlas = set()
    # Citas que la fase 1 acaba de reescribir con el contenido de ATLAS. La
    # fase 2 trabaja sobre la lectura ANTERIOR, así que sin esto vería su copia
    # vieja, la creería «cambiada aquí» y la devolvería a ATLAS: el conflicto
    # acabaría ganándolo justo quien no debía.
    ya_resueltas = set()
    for r in reuniones:
        if time.monotonic() > limite:
            res['errores'].append('Se agotó el tiempo: quedaron reuniones por revisar')
            break
        rid = r.get('id')
        if rid is None:
            continue
        ids_atlas.add(rid)
        if str(r.get('estado') or '').strip().lower() not in ESTADOS_VIGENTES:
            continue

        campos = reunion_a_cita(r, zona)
        if not campos:
            res['errores'].append(f'Reunión {rid} sin fecha u hora utilizable')
            continue
        huella_atlas = _reunion_a_huella(r)
        cita = por_reunion.get(rid)

        if not cita:
            nueva = dict(campos, atlas_reunion_id=rid, atlas_hash=huella_atlas,
                         atlas_synced_at=ahora, notes='Reunión sincronizada desde ATLAS')
            if app.supabase.insert('appointments', nueva):
                res['traidas'] += 1
            else:
                res['errores'].append(f'No se pudo crear la cita de la reunión {rid}')
            continue

        huella_guardada = cita.get('atlas_hash')
        cambio_alla = huella_atlas != huella_guardada
        cambio_aqui = _cita_a_huella(cita) != huella_guardada
        if cambio_alla:
            if cambio_aqui:
                res['conflictos'] += 1        # los dos cambiaron: manda ATLAS
            if app.supabase.update('appointments', cita['id'],
                                   dict(campos, atlas_hash=huella_atlas,
                                        atlas_synced_at=ahora)):
                res['actualizadas_aqui'] += 1
                ya_resueltas.add(cita['id'])

    # ── 2. Sistema → ATLAS ────────────────────────────────────────────────
    for c in citas:
        if time.monotonic() > limite:
            res['errores'].append('Se agotó el tiempo: quedaron citas por revisar')
            break
        if c.get('status') == 'cancelled' or c['id'] in ya_resueltas:
            continue
        rid = c.get('atlas_reunion_id')

        if not rid:
            # Cita nacida aquí: se crea allá.
            cuerpo = cita_a_reunion(c)
            if not cuerpo:
                continue
            try:
                creada = _atlas_insert('reuniones', cuerpo)
            except Exception as e:
                res['errores'].append(f'No se pudo crear en ATLAS: {str(e)[:120]}')
                continue
            nuevo_id = (creada or {}).get('id')
            if nuevo_id:
                app.supabase.update('appointments', c['id'], {
                    'atlas_reunion_id': nuevo_id,
                    'atlas_hash': _reunion_a_huella(dict(cuerpo, id=nuevo_id)),
                    'atlas_synced_at': ahora})
                res['llevadas'] += 1
            continue

        if rid not in ids_atlas:
            # La reunión ya no está en ATLAS: se cancela aquí, NO se borra.
            if c.get('status') != 'cancelled':
                app.supabase.update('appointments', c['id'], {
                    'status': 'cancelled',
                    'notes': 'La reunión fue eliminada en ATLAS',
                    'atlas_synced_at': ahora})
                res['canceladas'] += 1
            continue

        # Enlazada y viva: si cambió sólo aquí, se lleva el cambio allá.
        huella_guardada = c.get('atlas_hash')
        if _cita_a_huella(c) != huella_guardada:
            cuerpo = cita_a_reunion(c)
            if cuerpo and _atlas_update('reuniones', rid, cuerpo):
                app.supabase.update('appointments', c['id'], {
                    'atlas_hash': _reunion_a_huella(dict(cuerpo, id=rid)),
                    'atlas_synced_at': ahora})
                res['actualizadas_alla'] += 1

    return res


def arrancar_autosync(app, zona, interval_min=10):
    """Hilo de fondo, con el mismo candado de un solo worker que el de To-Do."""
    if not disponible():
        print('[atlas-sync] sin ATLAS_SUPABASE_KEY: desactivado')
        return
    try:
        import fcntl
    except Exception:
        print('[atlas-sync] fcntl no disponible (dev local): scheduler desactivado')
        return
    try:
        import tempfile
        ruta = os.path.join(tempfile.gettempdir(), 'atlas_sync.lock')
        archivo = open(ruta, 'w')
        fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        app._atlas_sync_lock = archivo
    except Exception:
        return

    def _bucle():
        time.sleep(90)
        while True:
            try:
                r = sincronizar(app, zona)
                if r.get('success') and any(r.get(k) for k in
                                            ('traidas', 'llevadas', 'actualizadas_aqui',
                                             'actualizadas_alla', 'canceladas')):
                    print(f'[atlas-sync] {r}')
            except Exception as e:
                print(f'[atlas-sync] {e}')
            time.sleep(interval_min * 60)

    threading.Thread(target=_bucle, name='atlas-sync', daemon=True).start()
    print(f'[atlas-sync] activo (cada {interval_min} min)')


# ============================================================
#  PERSONAS DE ATLAS  →  DIRECTORIO
#
#  La primera carga de personas al Directorio se hizo con un script suelto que
#  no quedó en el repositorio: trajo usuarios y docentes, y a los padres de
#  familia no los tocó. No había forma de repetirla ni de ampliarla, sólo de
#  volver a escribirla. Esto la convierte en una función del sistema.
#
#  Aquí NO se da por supuesto el esquema de ATLAS. Este proyecto sólo sabía
#  leer su tabla `reuniones`; cómo se llama la tabla de representantes, o sus
#  columnas, es algo que no consta en ninguna parte de este lado. Así que se
#  descubre y se informa, en lugar de adivinar y fallar en silencio: `explorar`
#  pregunta a ATLAS qué tablas de personas existen y qué columnas traen, y la
#  pantalla enseña lo que encontró antes de importar nada.
# ============================================================

# Nombres con los que suele aparecer cada grupo. Se prueban todos; los que no
# existan simplemente no salen en el resultado.
GRUPOS_PERSONAS = {
    'representantes': ('representantes', 'padres', 'padres_familia', 'padres_de_familia',
                       'acudientes', 'apoderados', 'tutores'),
    'socios':         ('socios', 'asociados', 'miembros'),
    'docentes':       ('docentes', 'profesores', 'maestros'),
    'estudiantes':    ('estudiantes', 'alumnos'),
    'usuarios':       ('usuarios', 'users', 'personas'),
}

# Los colectivos que se mantienen al día SOLOS, sin que nadie los estrene a
# mano: padres de familia, socios y docentes. Son los que se pidieron.
#
# Estudiantes y usuarios quedan fuera a propósito. No es un olvido: traerse el
# alumnado entero de un colegio a un directorio de clientes es una decisión con
# consecuencias —de volumen y de datos de menores— que nadie ha tomado. Si algún
# día se quiere, se añade aquí y se dice por qué.
GRUPOS_AUTOMATICOS = ('representantes', 'socios', 'docentes')

# Columnas de ATLAS que valen para cada campo del Directorio, en orden de
# preferencia. La primera que exista y traiga algo, gana.
EQUIVALENCIAS = {
    'nombres':   ('nombres', 'nombre', 'first_name', 'primer_nombre'),
    'apellidos': ('apellidos', 'apellido', 'last_name', 'primer_apellido'),
    'completo':  ('nombre_completo', 'full_name', 'nombres_completos', 'razon_social'),
    'documento': ('cedula', 'documento', 'dni', 'identificacion', 'doc_number', 'ruc'),
    'email':     ('email', 'correo', 'correo_electronico', 'mail'),
    'movil':     ('telefono', 'celular', 'movil', 'phone', 'telefono_movil'),
    'fijo':      ('telefono_fijo', 'convencional', 'landline'),
    'direccion': ('direccion', 'domicilio', 'address'),
    'ciudad':    ('ciudad', 'canton', 'city'),
}


def _primer_valor(fila, claves):
    for clave in claves:
        valor = fila.get(clave)
        if valor is not None and str(valor).strip():
            return str(valor).strip()
    return ''


def explorar():
    """Qué tablas de personas expone ATLAS y qué columnas traen.

    Devuelve, por grupo, la primera tabla que responda: su nombre, cuántas
    filas tiene y sus columnas. Lo que no exista se informa como tal — es la
    diferencia entre «ATLAS no tiene representantes» y «me equivoqué de
    nombre»."""
    if not disponible():
        return {'success': False, 'error': 'Falta ATLAS_SUPABASE_KEY en el servidor'}
    hallazgos, probados = {}, []
    for grupo, candidatos in GRUPOS_PERSONAS.items():
        for recurso in candidatos:
            probados.append(recurso)
            try:
                muestra = _atlas_get(recurso, 'select=*&limit=1')
            except Exception:
                continue          # no existe o no se puede leer: se prueba el siguiente
            try:
                total = len(_atlas_get(recurso, 'select=id'))
            except Exception:
                total = None
            hallazgos[grupo] = {
                'tabla': recurso,
                'filas': total,
                'columnas': sorted(muestra[0].keys()) if muestra else [],
            }
            break
    return {'success': True, 'grupos': hallazgos, 'probados': probados,
            'url': ATLAS_URL}


def leer_personas(recurso):
    """Filas crudas de una tabla de personas de ATLAS."""
    if not disponible():
        raise RuntimeError('Falta ATLAS_SUPABASE_KEY en el servidor')
    return _atlas_get(recurso, 'select=*')


def a_contacto(fila, etiquetas):
    """Traduce una fila de ATLAS al formato del Directorio.

    Devuelve (registro, motivo_de_descarte). El descarte no se decide aquí a la
    ligera: quien no tiene NINGUNA forma de contacto no sirve en un directorio,
    pero la falta de cédula no descarta a nadie —se le pone una referencia
    provisional, como ya hizo la primera carga— porque un padre de familia sin
    cédula registrada en ATLAS sigue siendo alguien a quien hay que poder
    llamar."""
    nombres   = _primer_valor(fila, EQUIVALENCIAS['nombres'])
    apellidos = _primer_valor(fila, EQUIVALENCIAS['apellidos'])
    completo  = _primer_valor(fila, EQUIVALENCIAS['completo'])
    if not (nombres or apellidos) and completo:
        partes = completo.split()
        nombres, apellidos = ' '.join(partes[:1]), ' '.join(partes[1:])
    email = _primer_valor(fila, EQUIVALENCIAS['email'])
    movil = _primer_valor(fila, EQUIVALENCIAS['movil'])
    fijo  = _primer_valor(fila, EQUIVALENCIAS['fijo'])

    if not (nombres or apellidos):
        return None, 'Sin nombre en ATLAS'
    if not (email or movil or fijo):
        return None, 'Sin correo ni teléfono en ATLAS'

    return {
        'first_name': nombres or None,
        'last_name': apellidos or None,
        'doc_number': _primer_valor(fila, EQUIVALENCIAS['documento']),
        'email': email or None,
        'mobile': movil or None,
        'landline': fijo or None,
        'home_address': _primer_valor(fila, EQUIVALENCIAS['direccion']) or None,
        'city': _primer_valor(fila, EQUIVALENCIAS['ciudad']) or None,
        'tags': etiquetas,
        'atlas_id': fila.get('id'),
    }, None


# ============================================================
#  SINCRONIZACIÓN DE PERSONAS EN LOS DOS SENTIDOS
#
#  Mismo mecanismo que ya usan las reuniones (ver `_sincronizar`), porque ya
#  está probado: se guarda una HUELLA del contenido en la última pasada y se
#  compara contra los dos lados.
#
#      la huella de ATLAS ya no coincide  -> cambió allá
#      la huella nuestra ya no coincide   -> cambió aquí
#      no coincide ninguna                -> cambiaron los dos: manda ATLAS
#
#  Manda ATLAS por la misma razón que en las reuniones: es el sistema donde esos
#  datos nacen. El teléfono de un padre de familia lo actualiza la secretaría
#  del colegio; aquí se consulta y se corrige, pero la fuente es aquélla.
#
#  NO SE BORRA NADA, en ningún sentido. Que una fila desaparezca de un lado no
#  prueba que deba desaparecer del otro —puede ser un filtro, un permiso o un
#  error—, y un padre de familia borrado en el sistema del colegio por un
#  descuido de este lado no se recupera con un «deshacer».
# ============================================================

# Campos que cruzan el puente. Sólo estos entran en la huella: así, un cambio en
# algo que NO se sincroniza —el sector, una nota interna, la verificación del
# SRI— no se confunde con una modificación que haya que propagar.
CAMPOS_PUENTE = ('nombres', 'apellidos', 'documento', 'email', 'movil', 'fijo',
                 'direccion', 'ciudad')

# Cómo se llama cada campo del puente en NUESTRA tabla de contactos.
NUESTRO_NOMBRE = {
    'nombres':   'first_name',
    'apellidos': 'last_name',
    'documento': 'doc_number',
    'email':     'email',
    'movil':     'mobile',
    'fijo':      'landline',
    'direccion': 'home_address',
    'ciudad':    'city',
}

# Etiqueta con la que queda marcado en el Directorio cada colectivo.
ETIQUETAS_POR_GRUPO = {
    'representantes': 'atlas,representante',
    'socios':         'atlas,socio',
    'docentes':       'atlas,docente',
    'estudiantes':    'atlas,estudiante',
    'usuarios':       'atlas,usuario',
}

# Sector del Directorio donde cae cada colectivo la primera vez.
SECTOR_POR_GRUPO = {
    'representantes': 'Padres de familia',
    'socios':         'Socios',
    'docentes':       'Clases (ATLAS)',
    'estudiantes':    'Estudiantes (ATLAS)',
    'usuarios':       'Clases (ATLAS)',
}


def _huella_personas(valores):
    """Huella estable de los campos que cruzan. Se compara en minúsculas y sin
    espacios de sobra: que alguien escriba «  Ana » en vez de «Ana» no es un
    cambio que merezca cruzar el puente y volver."""
    crudo = '|'.join(str(valores.get(c) or '').strip().lower() for c in CAMPOS_PUENTE)
    return hashlib.sha256(crudo.encode('utf-8')).hexdigest()[:32]


def mapa_de_columnas(columnas):
    """Qué columna de ATLAS corresponde a cada campo del puente, EN ESTA tabla.

    El esquema de ATLAS no consta en este proyecto, así que no se adivina: se
    mira qué columnas trae de verdad la tabla y se elige, para cada campo, el
    primer nombre conocido que exista. Lo que no aparezca queda FUERA del puente
    en lugar de escribirse en una columna equivocada. Escribir en la columna que
    no era, en la base de otro sistema, es de lo peor que puede hacer un
    puente."""
    presentes = set(columnas or [])
    mapa = {}
    for campo in CAMPOS_PUENTE:
        for candidata in EQUIVALENCIAS[campo]:
            if candidata in presentes:
                mapa[campo] = candidata
                break
    return mapa


def _valores_de_atlas(fila, mapa):
    return {campo: str(fila.get(mapa[campo]) or '').strip() if mapa.get(campo) else ''
            for campo in CAMPOS_PUENTE}


def _valores_de_contacto(contacto):
    return {campo: str(contacto.get(NUESTRO_NOMBRE[campo]) or '').strip()
            for campo in CAMPOS_PUENTE}


def _sector_de(app, nombre, _memoria={}):
    """Id del sector, creándolo la primera vez.

    Antes lo resolvía la pantalla de importación. Al quitarla, el puente tiene
    que saber dónde deja a cada colectivo: una persona que entra sin sector
    queda fuera de todos los filtros del Directorio, que es como no haber
    entrado. Se recuerda por pasada para no consultar lo mismo por cada fila."""
    if nombre in _memoria:
        return _memoria[nombre]
    slug = re.sub(r'[^a-z0-9]+', '-',
                  unicodedata.normalize('NFKD', nombre.lower())
                  .encode('ascii', 'ignore').decode()).strip('-')
    filas = app.supabase.get('sectors', {'slug': slug}, select='id')
    if filas:
        _memoria[nombre] = filas[0]['id']
    else:
        creado = app.supabase.insert('sectors', {
            'name': nombre, 'slug': slug, 'active': True,
            'description': 'Creado por la sincronización con ATLAS.'})
        _memoria[nombre] = creado[0]['id'] if creado else None
    return _memoria[nombre]


def sincronizar_personas(app, grupos=None, deadline_segundos=90):
    """Cruza las personas de ATLAS con el Directorio, en los dos sentidos.

    `grupos` limita a ciertos colectivos. Sin él se sincronizan los de
    GRUPOS_AUTOMATICOS —padres de familia, socios y docentes—, que es lo que se
    pidió: que estén al día solos, sin que nadie pulse nada. Los demás
    colectivos que ATLAS exponga se quedan quietos salvo que se pidan
    expresamente."""
    if not disponible():
        return {'success': False, 'error': 'Falta ATLAS_SUPABASE_KEY en el servidor'}
    if not _lock.acquire(blocking=False):
        return {'success': False, 'error': 'Ya hay una sincronización en curso'}
    try:
        return _sincronizar_personas(app, grupos, deadline_segundos)
    finally:
        _lock.release()


def _sincronizar_personas(app, grupos, deadline_segundos):
    limite = time.monotonic() + deadline_segundos
    res = {'success': True, 'traidas': 0, 'llevadas': 0, 'actualizadas_aqui': 0,
           'actualizadas_alla': 0, 'conflictos': 0, 'errores': [], 'grupos': []}

    exploracion = explorar()
    if not exploracion.get('success'):
        return exploracion
    ahora = datetime.now(timezone.utc).isoformat()
    # Sin lista explícita, los que deben mantenerse al día solos.
    objetivo = set(grupos or GRUPOS_AUTOMATICOS)

    for grupo, hallazgo in (exploracion.get('grupos') or {}).items():
        if grupo not in objetivo:
            continue
        tabla = hallazgo['tabla']
        mapa = mapa_de_columnas(hallazgo.get('columnas'))

        contactos = app.supabase.get('contacts', {'atlas_tabla': tabla}, select='*') or []
        por_id = {str(c['atlas_persona_id']): c for c in contactos
                  if c.get('atlas_persona_id')}

        try:
            filas = leer_personas(tabla)
        except Exception as e:
            res['errores'].append(f'{grupo}: no se pudo leer ATLAS ({str(e)[:120]})')
            continue

        # ── 1. ATLAS → sistema ────────────────────────────────────────────
        # Los que esta fase acaba de reescribir con el contenido de ATLAS. La
        # fase 2 trabaja sobre la lectura ANTERIOR, así que sin esto vería su
        # copia vieja, la creería «cambiada aquí» y la devolvería a ATLAS: el
        # conflicto acabaría ganándolo justo quien no debía.
        resueltos = set()
        for fila in filas:
            if time.monotonic() > limite:
                res['errores'].append(f'{grupo}: se agotó el tiempo')
                break
            pid = fila.get('id')
            if pid is None:
                continue
            pid = str(pid)
            valores = _valores_de_atlas(fila, mapa)
            if not (valores['nombres'] or valores['apellidos']):
                continue
            huella_alla = _huella_personas(valores)
            contacto = por_id.get(pid)

            if not contacto:
                registro, motivo = a_contacto(fila, ETIQUETAS_POR_GRUPO.get(grupo, 'atlas'))
                if motivo:
                    continue          # sin forma de contacto: no entra al directorio
                registro.pop('atlas_id', None)
                if not registro.get('doc_number'):
                    registro['doc_number'] = f'ATLAS-{grupo[:3].upper()}-{pid}'
                registro.update({'atlas_persona_id': pid, 'atlas_tabla': tabla,
                                 'atlas_hash': huella_alla, 'atlas_synced_at': ahora,
                                 'source': 'atlas', 'source_file': f'ATLAS · {grupo}'})
                sector = _sector_de(app, SECTOR_POR_GRUPO.get(grupo, 'ATLAS'))
                if sector:
                    registro['sector_id'] = sector
                if app.supabase.insert('contacts', registro):
                    res['traidas'] += 1
                else:
                    res['errores'].append(f'{grupo}: no se pudo crear a la persona {pid}')
                continue

            huella_guardada = contacto.get('atlas_hash')
            cambio_alla = huella_alla != huella_guardada
            cambio_aqui = _huella_personas(_valores_de_contacto(contacto)) != huella_guardada
            if cambio_alla:
                if cambio_aqui:
                    res['conflictos'] += 1        # cambiaron los dos: manda ATLAS
                cambios = {NUESTRO_NOMBRE[c]: (valores[c] or None)
                           for c in CAMPOS_PUENTE if mapa.get(c)}
                cambios.update({'atlas_hash': huella_alla, 'atlas_synced_at': ahora})
                if app.supabase.update('contacts', contacto['id'], cambios):
                    res['actualizadas_aqui'] += 1
                    resueltos.add(pid)
                else:
                    res['errores'].append(f'{grupo}: no se pudo actualizar a {pid}')

        # ── 2. sistema → ATLAS ────────────────────────────────────────────
        for contacto in contactos:
            if time.monotonic() > limite:
                break
            pid = str(contacto.get('atlas_persona_id') or '')
            if pid and pid in resueltos:
                continue                      # ya mandó ATLAS en la fase 1
            valores = _valores_de_contacto(contacto)
            huella_aqui = _huella_personas(valores)
            if pid and huella_aqui == contacto.get('atlas_hash'):
                continue                      # de este lado no cambió nada
            cuerpo = {mapa[c]: (valores[c] or None) for c in CAMPOS_PUENTE if mapa.get(c)}
            if not cuerpo:
                continue
            try:
                if pid:
                    if _atlas_update(tabla, pid, cuerpo):
                        app.supabase.update('contacts', contacto['id'],
                                            {'atlas_hash': huella_aqui,
                                             'atlas_synced_at': ahora})
                        res['actualizadas_alla'] += 1
                else:
                    creada = _atlas_insert(tabla, cuerpo)
                    nuevo = str((creada or {}).get('id') or '')
                    if nuevo:
                        app.supabase.update('contacts', contacto['id'],
                                            {'atlas_persona_id': nuevo, 'atlas_tabla': tabla,
                                             'atlas_hash': huella_aqui,
                                             'atlas_synced_at': ahora})
                        res['llevadas'] += 1
            except Exception as e:
                res['errores'].append(f'{grupo}: {str(e)[:120]}')

        res['grupos'].append({
            'grupo': grupo, 'tabla': tabla,
            'campos_puente': sorted(mapa.keys()),
            # Lo que ATLAS no tiene con ningún nombre conocido. Se informa en vez
            # de callarlo: un campo que no cruza y nadie sabe que no cruza es un
            # dato que alguien creerá haber guardado.
            'sin_equivalencia': sorted(set(CAMPOS_PUENTE) - set(mapa)),
        })
    return res


def arrancar_autosync_personas(app, interval_min=15):
    """Cruza las personas cada cierto tiempo, en segundo plano.

    Mismo candado de archivo que el de las reuniones: con dos workers de
    gunicorn, sin él la sincronización correría por duplicado y los dos procesos
    se pisarían escribiendo en ATLAS."""
    try:
        import fcntl, tempfile
        ruta = os.path.join(tempfile.gettempdir(), 'atlas_sync_personas.lock')
        archivo = open(ruta, 'w')
        fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        app._atlas_personas_lock = archivo
    except Exception:
        return

    def _bucle():
        time.sleep(150)      # se deja arrancar la aplicación antes de nada
        while True:
            try:
                r = sincronizar_personas(app)
                if r.get('success') and any(r.get(k) for k in
                                            ('traidas', 'llevadas', 'actualizadas_aqui',
                                             'actualizadas_alla')):
                    print(f'[atlas-personas] {r}')
            except Exception as e:
                print(f'[atlas-personas] {e}')
            time.sleep(interval_min * 60)

    threading.Thread(target=_bucle, name='atlas-personas', daemon=True).start()
    print(f'[atlas-personas] activo (cada {interval_min} min)')
