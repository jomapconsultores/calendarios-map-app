# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Asistencia con IA (Claude) para dos trabajos concretos del sistema:

  1) CLASIFICAR lo que llega en un Excel, un PDF o un Word y colocar cada dato
     en la columna que le corresponde del directorio. Los archivos que manda un
     cliente nunca traen las cabeceras que uno espera: una columna dice "Telf",
     otra mezcla nombre y cédula, el PDF es un listado corrido sin tabla. La IA
     lee ese material en bruto y devuelve filas ya mapeadas a las columnas.

  2) PLANIFICAR un cronograma: proponer fechas de inicio, fin, duración,
     responsable y dependencias para un conjunto de actividades.

En ambos casos la salida es JSON validado contra un esquema (structured
outputs), no texto libre que después haya que adivinar cómo parsear.

La IA es OPCIONAL. Sin ANTHROPIC_API_KEY el módulo entero queda desactivado y
las importaciones caen al mapeo por cabeceras, que ya funciona sin ella. Nunca
se rompe la aplicación por no tener clave.
"""
import base64
import json
import os
import time

import requests as req_lib

try:
    import anthropic
    ANTHROPIC_DISPONIBLE = True
except ImportError:                                   # pragma: no cover
    ANTHROPIC_DISPONIBLE = False

MODELO = os.getenv('ANTHROPIC_MODEL', 'claude-opus-5')

# ── Mistral ──────────────────────────────────────────────────────────────────
# Se habla por HTTP y no con su SDK: el proyecto ya usa `requests` para Supabase,
# Microsoft Graph y el SRI, así que no se añade una dependencia nueva por dos
# llamadas. Mistral aporta dos cosas que Claude no cubre aquí:
#   * OCR de verdad (mistral-ocr): lee FOTOS y PDF escaneados, que es lo que
#     traen los clientes cuando mandan una ficha hecha con el móvil.
#   * Una alternativa de clasificación más barata para lotes grandes.
MISTRAL_API   = 'https://api.mistral.ai/v1'
MISTRAL_TEXTO = os.getenv('MISTRAL_MODEL', 'mistral-large-latest')
MISTRAL_OCR   = os.getenv('MISTRAL_OCR_MODEL', 'mistral-ocr-latest')
_TIMEOUT_MISTRAL = (10, 180)

# Qué motor usa la clasificación: 'auto' toma Mistral si hay clave suya y si no
# Claude. Se puede forzar con IA_PROVEEDOR=anthropic|mistral.
PROVEEDOR = (os.getenv('IA_PROVEEDOR') or 'auto').lower()

# Cuántas filas se mandan por llamada. Un lote grande gasta menos llamadas pero
# arriesga topar el límite de salida; 40 es el punto donde un Excel de 500 filas
# se procesa en pocas llamadas sin truncarse.
FILAS_POR_LOTE = 40

# Columnas del directorio que la IA puede rellenar. El esquema se genera de esta
# lista, así que agregar una columna aquí la habilita en la importación.
CAMPOS_CONTACTO = [
    ('doc_type',      'Tipo de documento: "cedula" (10 dígitos), "ruc" (13 dígitos) o "pasaporte"'),
    ('doc_number',    'Número de cédula, RUC o pasaporte, sólo los dígitos, sin guiones ni puntos'),
    ('first_name',    'Nombres de pila de la persona'),
    ('last_name',     'Apellidos de la persona'),
    ('business_name', 'Razón social si el registro es de una empresa'),
    ('trade_name',    'Nombre comercial'),
    ('mobile',        'Número celular (en Ecuador empieza con 09 y tiene 10 dígitos)'),
    ('landline',      'Teléfono convencional o fijo (7 u 8 dígitos, o 9 con código de provincia)'),
    ('email',         'Correo electrónico'),
    ('website',       'Dirección web o página del contacto'),
    ('work_address',  'Dirección del lugar de trabajo o de la empresa'),
    ('home_address',  'Dirección domiciliaria'),
    ('city',          'Ciudad'),
    ('province',      'Provincia'),
    ('sector',        'Sector o servicio al que pertenece el registro'),
    ('notes',         'Cualquier dato relevante que no encaje en las columnas anteriores'),
]

_INSTRUCCIONES = """Eres un asistente que ordena bases de datos de clientes de un estudio contable ecuatoriano.

Recibes material en bruto (filas de un Excel, texto extraído de un PDF o de un Word) y lo conviertes en registros estructurados, colocando cada dato en la columna que le corresponde.

Reglas:
- Un registro por persona o empresa. Si una fila trae dos personas, devuélvelas como dos registros.
- Los números de documento van sólo con dígitos. 10 dígitos es cédula, 13 es RUC. Si tiene letras es pasaporte.
- Separa nombres de apellidos. En Ecuador el orden habitual escrito es "APELLIDO1 APELLIDO2 NOMBRE1 NOMBRE2"; cuando el texto viene con coma ("PEREZ LOPEZ, JUAN CARLOS") lo de antes de la coma son los apellidos.
- Distingue celular de convencional: el celular ecuatoriano tiene 10 dígitos y empieza con 09; el convencional tiene 7 u 8 dígitos, o 9 si incluye el código de provincia (02, 04, 07...).
- Las redes sociales van en `socials`, una entrada por red, con el nombre de la red y la URL o el usuario. Un usuario suelto como "@estudio_map" es válido: pon la red que corresponda y el usuario.
- Si un dato no aparece, deja la cadena vacía. No inventes datos, no completes con suposiciones y no rellenes con "N/A".
- Devuelve los registros en el mismo orden en que aparecen en el material.

Deja `confianza` en "baja" para el registro que hayas tenido que interpretar mucho, para que una persona lo revise antes de guardar."""


def _esquema_contactos():
    propiedades = {campo: {'type': 'string', 'description': ayuda}
                   for campo, ayuda in CAMPOS_CONTACTO}
    propiedades['socials'] = {
        'type': 'array',
        'description': 'Redes sociales del contacto',
        'items': {
            'type': 'object',
            'properties': {
                'red': {'type': 'string', 'description': 'Facebook, Instagram, LinkedIn, X, TikTok, WhatsApp...'},
                'url': {'type': 'string', 'description': 'URL completa o nombre de usuario'},
            },
            'required': ['red', 'url'],
            'additionalProperties': False,
        },
    }
    propiedades['confianza'] = {
        'type': 'string', 'enum': ['alta', 'media', 'baja'],
        'description': 'Qué tan seguro está el mapeo de este registro',
    }
    return {
        'type': 'object',
        'properties': {
            'registros': {'type': 'array', 'items': {
                'type': 'object',
                'properties': propiedades,
                'required': list(propiedades.keys()),
                'additionalProperties': False,
            }},
        },
        'required': ['registros'],
        'additionalProperties': False,
    }


_ESQUEMA_GANTT = {
    'type': 'object',
    'properties': {
        'actividades': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'name':          {'type': 'string', 'description': 'Nombre de la actividad'},
                'description':   {'type': 'string'},
                'responsible':   {'type': 'string', 'description': 'Responsable sugerido, vacío si no hay información'},
                'start_date':    {'type': 'string', 'description': 'Fecha de inicio en formato AAAA-MM-DD'},
                'end_date':      {'type': 'string', 'description': 'Fecha de fin en formato AAAA-MM-DD'},
                'duration_days': {'type': 'integer', 'description': 'Días laborables estimados'},
                'priority':      {'type': 'string', 'enum': ['low', 'medium', 'high', 'urgent']},
                'is_milestone':  {'type': 'boolean', 'description': 'true si es un hito sin duración'},
                'depends_on':    {'type': 'array', 'items': {'type': 'string'},
                                  'description': 'Nombres de las actividades que deben terminar antes que esta'},
                'ai_notes':      {'type': 'string', 'description': 'Por qué se propone esa fecha y esa duración'},
            },
            'required': ['name', 'description', 'responsible', 'start_date', 'end_date',
                         'duration_days', 'priority', 'is_milestone', 'depends_on', 'ai_notes'],
            'additionalProperties': False,
        }},
        'resumen':     {'type': 'string', 'description': 'Explicación breve del plan propuesto'},
        'riesgos':     {'type': 'array', 'items': {'type': 'string'},
                        'description': 'Cuellos de botella o solapamientos detectados'},
        'ruta_critica': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Nombres de las actividades que marcan la duración total'},
    },
    'required': ['actividades', 'resumen', 'riesgos', 'ruta_critica'],
    'additionalProperties': False,
}

_INSTRUCCIONES_GANTT = """Eres un planificador de proyectos de un estudio contable ecuatoriano. Recibes una lista de actividades y devuelves un cronograma ejecutable.

Reglas:
- Respeta las fechas que ya vengan definidas en una actividad; complétalas sólo cuando falten.
- Trabaja con días laborables: no programes inicios ni fines en sábado o domingo.
- Ordena las actividades por dependencia lógica; una actividad no puede empezar antes de que termine aquella de la que depende.
- Estima duraciones realistas según el tipo de trabajo. Un trámite que espera respuesta de un tercero dura más de lo que toma ejecutarlo.
- Si dos actividades comparten responsable, no las solapes.
- En `ai_notes` explica en una frase de dónde sale la duración estimada.
- Marca como hito (`is_milestone`) las fechas de corte que no consumen tiempo: vencimientos, entregas, presentaciones."""


class IANoDisponible(Exception):
    """La clasificación con IA se pidió pero no hay clave configurada."""


def _hay_claude():
    return bool(ANTHROPIC_DISPONIBLE and os.getenv('ANTHROPIC_API_KEY'))


def _hay_mistral():
    return bool(os.getenv('MISTRAL_API_KEY'))


def proveedor_activo():
    """Qué motor se va a usar para clasificar: 'mistral', 'anthropic' o None."""
    if PROVEEDOR == 'mistral':
        return 'mistral' if _hay_mistral() else None
    if PROVEEDOR == 'anthropic':
        return 'anthropic' if _hay_claude() else None
    # auto: Mistral primero, porque es quien además hace el OCR de las fotos y
    # así una sola clave cubre todo el flujo de importación.
    if _hay_mistral():
        return 'mistral'
    return 'anthropic' if _hay_claude() else None


def disponible():
    """True si se puede clasificar con IA en este despliegue."""
    return proveedor_activo() is not None


def ocr_disponible():
    """El OCR (fotos y PDF escaneados) sólo lo hace Mistral."""
    return _hay_mistral()


def estado():
    """Explica al usuario qué hay disponible y qué falta, en lugar de fallar en
    silencio a mitad de una importación."""
    motor = proveedor_activo()
    if not motor:
        faltan = []
        if not _hay_mistral():
            faltan.append('MISTRAL_API_KEY')
        if not _hay_claude():
            faltan.append('ANTHROPIC_API_KEY'
                          if ANTHROPIC_DISPONIBLE else 'la librería anthropic')
        return {'disponible': False, 'ocr': False,
                'motivo': 'Falta ' + ' o '.join(faltan) + ' en el servidor'}
    return {
        'disponible': True,
        'motivo': None,
        'proveedor': motor,
        'modelo': MISTRAL_TEXTO if motor == 'mistral' else MODELO,
        # El OCR se anuncia aparte: sin él, las fotos y los PDF escaneados no
        # se pueden importar aunque la clasificación sí funcione.
        'ocr': ocr_disponible(),
        'modelo_ocr': MISTRAL_OCR if ocr_disponible() else None,
    }


def _cliente():
    if not _hay_claude():
        raise IANoDisponible(estado()['motivo'])
    return anthropic.Anthropic()


# ============================================================
#  MISTRAL
# ============================================================
def _mistral_cabeceras():
    clave = os.getenv('MISTRAL_API_KEY')
    if not clave:
        raise IANoDisponible('Falta MISTRAL_API_KEY en el servidor')
    return {'Authorization': f'Bearer {clave}', 'Content-Type': 'application/json'}


def _pedir_json_mistral(instrucciones, contenido, esquema, max_tokens=8000):
    """Una llamada a Mistral que devuelve JSON.

    Se usa `response_format: json_object` y el esquema va descrito en las
    instrucciones. El esquema estricto no está disponible en todos los modelos,
    y el resultado se normaliza igualmente aguas abajo (`_normalizar` del
    directorio tolera claves que falten), así que forzar sólo "esto es JSON" es
    lo robusto aquí."""
    cuerpo = {
        'model': MISTRAL_TEXTO,
        'response_format': {'type': 'json_object'},
        'max_tokens': max_tokens,
        'messages': [
            {'role': 'system',
             'content': instrucciones +
                        '\n\nResponde ÚNICAMENTE con un objeto JSON que cumpla '
                        'exactamente este esquema:\n' +
                        json.dumps(esquema, ensure_ascii=False)},
            {'role': 'user', 'content': contenido},
        ],
    }
    r = req_lib.post(f'{MISTRAL_API}/chat/completions', headers=_mistral_cabeceras(),
                     json=cuerpo, timeout=_TIMEOUT_MISTRAL)
    if r.status_code != 200:
        raise RuntimeError(f'Mistral respondió HTTP {r.status_code}: {r.text[:200]}')
    texto = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
    if not texto:
        raise RuntimeError('Mistral devolvió una respuesta vacía')
    return json.loads(texto)


# Formatos que el OCR sabe leer. El PDF entra como documento; el resto, como imagen.
OCR_IMAGENES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
                '.tif': 'image/tiff', '.tiff': 'image/tiff', '.heic': 'image/heic'}


def ocr_a_texto(contenido, nombre_archivo, limite_paginas=100):
    """Lee una FOTO o un PDF escaneado y devuelve sus líneas de texto.

    Es lo que permite importar lo que el cliente manda de verdad: una ficha
    fotografiada con el móvil, o un PDF que es una imagen y del que
    `extract_text` no saca ni un carácter.

    Devuelve una lista de líneas, el mismo formato que `documentos.extraer`
    entrega en `bloques`, para que la clasificación no note la diferencia."""
    if not ocr_disponible():
        raise IANoDisponible(
            'El OCR necesita MISTRAL_API_KEY en el servidor: sin ella no se '
            'pueden leer fotos ni PDF escaneados.')

    nombre = (nombre_archivo or '').lower()
    extension = '.' + nombre.rsplit('.', 1)[-1] if '.' in nombre else ''
    b64 = base64.b64encode(contenido).decode('ascii')

    if extension == '.pdf':
        documento = {'type': 'document_url',
                     'document_url': f'data:application/pdf;base64,{b64}'}
    else:
        tipo = OCR_IMAGENES.get(extension, 'image/jpeg')
        documento = {'type': 'image_url', 'image_url': f'data:{tipo};base64,{b64}'}

    r = req_lib.post(f'{MISTRAL_API}/ocr', headers=_mistral_cabeceras(),
                     json={'model': MISTRAL_OCR, 'document': documento},
                     timeout=_TIMEOUT_MISTRAL)
    if r.status_code != 200:
        raise RuntimeError(f'El OCR respondió HTTP {r.status_code}: {r.text[:200]}')

    lineas = []
    for pagina in (r.json().get('pages') or [])[:limite_paginas]:
        for linea in (pagina.get('markdown') or '').splitlines():
            limpia = linea.strip().lstrip('#').strip()
            # Se descartan los separadores de tabla que mete el markdown.
            if limpia and set(limpia) - set('|-: '):
                lineas.append(limpia)
    return lineas


def _pedir_json(instrucciones, contenido, esquema, max_tokens=16000, effort='medium'):
    """Una llamada a Claude que devuelve JSON validado contra `esquema`.

    `output_config.format` obliga al modelo a responder con esa forma exacta, de
    modo que aquí no hace falta ni parsear a mano ni reintentar por texto mal
    formado."""
    cliente = _cliente()
    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=max_tokens,
        system=instrucciones,
        output_config={'effort': effort,
                       'format': {'type': 'json_schema', 'schema': esquema}},
        messages=[{'role': 'user', 'content': contenido}],
    )
    if respuesta.stop_reason == 'refusal':
        raise RuntimeError('La IA rechazó procesar este contenido')
    texto = next((b.text for b in respuesta.content if b.type == 'text'), '')
    if not texto:
        raise RuntimeError('La IA devolvió una respuesta vacía')
    return json.loads(texto)


# ============================================================
#  1. CLASIFICACIÓN DE ARCHIVOS IMPORTADOS
# ============================================================
def clasificar_registros(material, sectores=None, origen='archivo', progreso=None,
                         limite_segundos=None):
    """Convierte material en bruto en registros listos para el directorio.

    `material` es una lista de trozos de texto: cada trozo puede ser una fila del
    Excel ya serializada o un bloque de texto del PDF/Word. Se procesa por lotes
    para no mandar un archivo entero en una sola petición.

    `limite_segundos` acota el trabajo total. Existe porque gunicorn mata al
    worker pasado su timeout: un archivo de 500 filas son trece llamadas al
    modelo y se pasaría de largo, con lo que el usuario no vería un aviso sino
    una petición cortada sin explicación. Con el límite se devuelve lo analizado
    hasta ese punto y un aviso diciendo cuánto quedó fuera — la vista previa ya
    enseña fila por fila lo que se va a guardar, así que un análisis parcial es
    visible y seguro.

    Devuelve (registros, avisos)."""
    if not material:
        return [], []
    fin = (time.monotonic() + limite_segundos) if limite_segundos else None

    nombres_sectores = [s.get('name') for s in (sectores or []) if s.get('name')]
    contexto_sectores = ''
    if nombres_sectores:
        contexto_sectores = ('\n\nLos sectores que existen en el sistema son: '
                             + ', '.join(nombres_sectores)
                             + '. Usa exactamente uno de esos nombres en el campo `sector` '
                               'cuando el material permita deducirlo; si no, déjalo vacío.')

    esquema = _esquema_contactos()
    registros, avisos = [], []
    lotes = [material[i:i + FILAS_POR_LOTE] for i in range(0, len(material), FILAS_POR_LOTE)]

    for indice, lote in enumerate(lotes, 1):
        if fin and time.monotonic() > fin:
            filas_fuera = sum(len(l) for l in lotes[indice - 1:])
            avisos.append(
                f'Se analizaron {len(material) - filas_fuera} de {len(material)} filas: '
                f'el archivo es grande y se alcanzó el límite de tiempo. Guarda estas y '
                f'vuelve a importar el resto en un segundo archivo.')
            break
        bloque = '\n'.join(f'[{n}] {texto}' for n, texto in enumerate(lote, 1))
        contenido = (f'Material extraído de un {origen}. Conviértelo en registros '
                     f'estructurados.{contexto_sectores}\n\n---\n{bloque}\n---')
        try:
            if proveedor_activo() == 'mistral':
                datos = _pedir_json_mistral(_INSTRUCCIONES + contexto_sectores,
                                            contenido, esquema)
            else:
                datos = _pedir_json(_INSTRUCCIONES + contexto_sectores, contenido, esquema)
            registros.extend(datos.get('registros') or [])
        except IANoDisponible:
            raise
        except Exception as e:
            avisos.append(f'Lote {indice} de {len(lotes)}: {str(e)[:160]}')
        if progreso:
            progreso(indice, len(lotes))

    return registros, avisos


# ============================================================
#  2. PLANIFICACIÓN DEL CRONOGRAMA
# ============================================================
def planificar_cronograma(actividades, contexto='', fecha_inicio=None, fecha_limite=None,
                          responsables=None, jornada=''):
    """Propone fechas, duraciones y dependencias para un conjunto de actividades.

    `actividades` es una lista de dicts con al menos `name`, y opcionalmente
    `description`, `responsible`, `start_date`, `end_date`, `priority`."""
    if not actividades:
        return {'actividades': [], 'resumen': 'No se enviaron actividades',
                'riesgos': [], 'ruta_critica': []}

    partes = []
    if fecha_inicio:
        partes.append(f'El cronograma arranca el {fecha_inicio}.')
    if fecha_limite:
        partes.append(f'Todo debe estar terminado como máximo el {fecha_limite}.')
    if responsables:
        partes.append('Responsables disponibles: ' + ', '.join(responsables) + '.')
    if jornada:
        partes.append(f'Consideraciones de jornada: {jornada}')
    if contexto:
        partes.append(f'Contexto del trabajo: {contexto}')

    listado = json.dumps(actividades, ensure_ascii=False, indent=1)
    contenido = ('\n'.join(partes) +
                 '\n\nActividades a planificar:\n' + listado)

    if proveedor_activo() == 'mistral':
        return _pedir_json_mistral(_INSTRUCCIONES_GANTT, contenido, _ESQUEMA_GANTT,
                                   max_tokens=16000)
    return _pedir_json(_INSTRUCCIONES_GANTT, contenido, _ESQUEMA_GANTT,
                       max_tokens=24000, effort='high')
