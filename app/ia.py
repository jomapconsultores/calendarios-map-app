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
import json
import os

try:
    import anthropic
    ANTHROPIC_DISPONIBLE = True
except ImportError:                                   # pragma: no cover
    ANTHROPIC_DISPONIBLE = False

MODELO = os.getenv('ANTHROPIC_MODEL', 'claude-opus-5')

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


def disponible():
    """True si se puede llamar a la IA en este despliegue."""
    return bool(ANTHROPIC_DISPONIBLE and os.getenv('ANTHROPIC_API_KEY'))


def estado():
    """Explica al usuario por qué la IA está o no disponible, en lugar de
    fallar en silencio a mitad de una importación."""
    if not ANTHROPIC_DISPONIBLE:
        return {'disponible': False,
                'motivo': 'Falta la librería anthropic en el servidor (pip install anthropic)'}
    if not os.getenv('ANTHROPIC_API_KEY'):
        return {'disponible': False,
                'motivo': 'Falta ANTHROPIC_API_KEY en el archivo .env del servidor'}
    return {'disponible': True, 'motivo': None, 'modelo': MODELO}


def _cliente():
    if not disponible():
        raise IANoDisponible(estado()['motivo'])
    return anthropic.Anthropic()


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
def clasificar_registros(material, sectores=None, origen='archivo', progreso=None):
    """Convierte material en bruto en registros listos para el directorio.

    `material` es una lista de trozos de texto: cada trozo puede ser una fila del
    Excel ya serializada o un bloque de texto del PDF/Word. Se procesa por lotes
    para no mandar un archivo entero en una sola petición.

    Devuelve (registros, avisos)."""
    if not material:
        return [], []

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
        bloque = '\n'.join(f'[{n}] {texto}' for n, texto in enumerate(lote, 1))
        contenido = (f'Material extraído de un {origen}. Conviértelo en registros '
                     f'estructurados.{contexto_sectores}\n\n---\n{bloque}\n---')
        try:
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

    return _pedir_json(_INSTRUCCIONES_GANTT, contenido, _ESQUEMA_GANTT,
                       max_tokens=24000, effort='high')
