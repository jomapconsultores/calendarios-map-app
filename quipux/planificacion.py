# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Un quipux con plazo se convierte en una actividad del cronograma.

Tener los documentos bajados y ordenados resuelve la mitad del problema: saber
qué hay. La otra mitad es que el plazo se reclame solo, y eso ya existe en la
plataforma —el semáforo, el aviso diario de incumplimiento, el calendario de
vencimientos—. Lo único que faltaba era que lo de CuencaDOC llegara hasta ahí.

Cada área tiene su proyecto contenedor («Quipux — GESTIÓN DE PLANIFICACIÓN Y
PROYECTOS»), permanente, sin fecha de fin: el trabajo de un área no termina.
Cada documento con plazo cuelga de él como actividad con su fecha.

Sólo se vuelca lo que TIENE plazo. Meter también lo que no lo tiene llenaría el
cronograma de cosas que no vencen nunca y acabaría convirtiendo el semáforo en
ruido, que es la manera de que deje de mirarse.

Se apunta de dónde salió cada actividad (`source='quipux'` y el identificador
del documento) para que la pasada siguiente actualice la que ya está en vez de
crear otra igual. Y nunca se pisa lo que una persona haya tocado a mano: si la
actividad está cerrada o le movieron la fecha aquí dentro, se respeta. El
sistema documental manda sobre lo que dice el documento; sobre cómo se organiza
uno para cumplirlo, manda quien lo cumple.
"""
from datetime import date

ORIGEN = 'quipux'


def _proyecto_de_area(db, area, registro=print):
    """Busca —o crea— el proyecto contenedor del área. Devuelve su id o None."""
    nombre = f'Quipux — {area}'
    try:
        existentes = db.get('projects', select='id,name,status') or []
    except Exception as e:
        registro(f'[quipux] no se pudo leer los proyectos: {str(e)[:120]}')
        return None
    for p in existentes:
        if (p.get('name') or '').strip().lower() == nombre.lower():
            return p['id']
    creado = db.insert('projects', {
        'name': nombre,
        'description': f'Documentos de CuencaDOC del área {area}, con sus plazos.',
        'status': 'active',
        'owner': 'Marco Antonio Posligua San Martin',
        # Sin fecha de vencimiento: es trabajo permanente. Cada actividad lleva
        # la suya, que es la que de verdad se incumple.
    })
    if creado:
        registro(f'[quipux] creado el proyecto «{nombre}»')
        return creado[0]['id']
    registro(f'[quipux] no se pudo crear el proyecto «{nombre}»')
    return None


def _ya_estan(db, registro=print):
    """Lo que pasadas anteriores dejaron en el cronograma, por documento."""
    try:
        filas = db.get('tasks', {'source': ORIGEN},
                       select='id,title,due_date,status,source_id') or []
    except Exception:
        # La columna `source_id` puede no existir en esta base: se reintenta
        # sin ella y se relaciona por el número del documento en el título.
        try:
            filas = db.get('tasks', {'source': ORIGEN},
                           select='id,title,due_date,status') or []
        except Exception as e:
            registro(f'[quipux] no se pudieron leer las actividades: {str(e)[:120]}')
            return {}
    indice = {}
    for f in filas:
        clave = f.get('source_id')
        if not clave:
            # «[DGPG-2050-2026] ACTUALIZACIÓN…» → DGPG-2050-2026
            titulo = f.get('title') or ''
            if titulo.startswith('['):
                clave = titulo[1:titulo.find(']')] if ']' in titulo else None
        if clave:
            indice[str(clave)] = f
    return indice


def _actividad(doc, project_id, responsable):
    plazo = doc.get('plazo') or {}
    return {
        'title': f"[{doc.get('numero', 's/n')}] {(doc.get('asunto') or '')[:170]}",
        'project_id': project_id,
        'assigned_to': responsable,
        'due_date': plazo.get('fecha'),
        'start_date': (doc.get('fecha_doc') or '')[:10] or date.today().isoformat(),
        'status': 'pending',
        'priority': 'high' if plazo.get('seguro') else 'medium',
        'progress_pct': 0,
        'phase': doc.get('bandeja') or 'General',
        'source': ORIGEN,
        'alert_days': 3,
    }


def volcar(db, documentos, responsable='Marco Antonio Posligua San Martin',
           registro=print):
    """Lleva al cronograma los documentos con plazo. Devuelve el resumen.

    `db` es el cliente de Supabase de la aplicación. Si no hay base disponible,
    no se lanza nada: se dice y se sigue. El índice en disco ya cumple su parte,
    y perder la pasada entera por no poder escribir en la base sería tirar
    también el trabajo de descarga."""
    resumen = {'creadas': 0, 'actualizadas': 0, 'sin_cambios': 0,
               'respetadas': 0, 'omitidas': 0, 'error': None}
    if db is None:
        resumen['error'] = 'no hay conexión con la base de la plataforma'
        return resumen

    con_plazo = [d for d in documentos if (d.get('plazo') or {}).get('fecha')]
    resumen['omitidas'] = len(documentos) - len(con_plazo)
    if not con_plazo:
        return resumen

    existentes = _ya_estan(db, registro)
    proyectos = {}

    for doc in con_plazo:
        area = doc.get('area') or 'CuencaDOC'
        if area not in proyectos:
            proyectos[area] = _proyecto_de_area(db, area, registro)
        pid = proyectos[area]
        if not pid:
            resumen['error'] = 'no se pudo preparar el proyecto contenedor'
            continue

        clave = str(doc.get('id') or doc.get('numero') or '')
        nueva = _actividad(doc, pid, responsable)
        vieja = existentes.get(clave) or existentes.get(str(doc.get('numero') or ''))

        if not vieja:
            fila = dict(nueva)
            fila['source_id'] = clave
            creada = db.insert('tasks', fila)
            if not creada:
                # Base sin columna `source_id`: se reintenta sin ella. El
                # título ya lleva el número, que basta para reconocerla.
                creada = db.insert('tasks', nueva)
            if creada:
                resumen['creadas'] += 1
            continue

        # Ya estaba. Sólo se toca la fecha, y sólo si el sistema documental
        # dice otra cosa Y nadie la ha cerrado aquí.
        if (vieja.get('status') or '') in ('done', 'cancelled'):
            resumen['respetadas'] += 1
            continue
        if (vieja.get('due_date') or '')[:10] != (nueva['due_date'] or '')[:10]:
            if db.update('tasks', vieja['id'], {'due_date': nueva['due_date']}):
                resumen['actualizadas'] += 1
                registro(f"[quipux] {doc.get('numero')}: el plazo cambió a {nueva['due_date']}")
            continue
        resumen['sin_cambios'] += 1

    return resumen


TABLA_DOCUMENTOS = 'quipux_documentos'


def publicar(db, documentos, registro=print):
    """Sube lo recogido a la base, para que se pueda mirar desde cualquier parte.

    La recolección corre en la computadora de la persona y no se puede mover a
    un servidor: entrar a CuencaDOC necesita su credencial del llavero y, a
    veces, que ella escriba el texto de una imagen. Pero MIRAR lo recogido sí
    tiene que poder hacerse desde el teléfono o desde la plataforma desplegada,
    y ahí un archivo en el disco de una computadora no sirve de nada.

    Es una foto de lo que dice CuencaDOC, no una copia de trabajo: lo que se
    hace con cada documento vive en el cronograma. Dos sitios para lo mismo
    acaban siempre en que ninguno de los dos es el bueno."""
    resumen = {'subidos': 0, 'error': None}
    if db is None:
        resumen['error'] = 'no hay conexión con la base de la plataforma'
        return resumen
    from datetime import datetime, timezone
    ahora = datetime.now(timezone.utc).isoformat()

    filas = []
    for d in documentos:
        if not d.get('id'):
            continue
        plazo = d.get('plazo') or {}
        filas.append({
            'id': str(d['id']),
            'numero': d.get('numero'), 'asunto': d.get('asunto'),
            'remitente': d.get('de'), 'tipo': d.get('tipo'),
            'fecha_doc': (d.get('fecha_doc') or '')[:10] or None,
            'tramite': d.get('tramite'), 'referencia': d.get('referencia'),
            'categoria': d.get('categoria'),
            'area': d.get('area'), 'bandeja': d.get('bandeja'),
            'estado': d.get('estado') or 'abierto',
            'carpeta': d.get('carpeta'), 'enlace': d.get('enlace'),
            'n_adjuntos': int(d.get('n_adjuntos') or 0),
            'plazo_fecha': plazo.get('fecha') or None,
            'plazo_origen': plazo.get('origen') or None,
            'plazo_seguro': bool(plazo.get('seguro')),
            'actualizado': ahora,
        })
    if not filas:
        return resumen

    # De cien en cien: una sola petición con doscientas filas se pasa del
    # tiempo límite en frío y se pierde la pasada entera por el último documento.
    for i in range(0, len(filas), 100):
        lote = filas[i:i + 100]
        if db.upsert(TABLA_DOCUMENTOS, lote, 'id'):
            resumen['subidos'] += len(lote)
        else:
            resumen['error'] = ('no se pudo subir a la base; ¿está aplicada la '
                                'migración 034?')
            break
    if resumen['subidos']:
        registro(f"[quipux] {resumen['subidos']} documento(s) publicados en la plataforma")
    return resumen


def cliente_de_la_plataforma(registro=print):
    """El cliente de Supabase que usa la aplicación, con su misma configuración.

    Se importa aquí dentro y no arriba para que este paquete se pueda usar sin
    la aplicación web delante: descargar los quipux tiene sentido por sí solo,
    aunque la plataforma esté caída."""
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        url, clave = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY')
        if not url or not clave:
            registro('[quipux] sin SUPABASE_URL/KEY: no se vuelca al cronograma')
            return None
        from app import SupabaseAPI
        return SupabaseAPI(url, clave)
    except Exception as e:
        registro(f'[quipux] no se pudo preparar la conexión con la base: {str(e)[:150]}')
        return None
