# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Leer el documento para saber qué hay que entregar y para cuándo.

La tabla de la bandeja dice el asunto, el número y a veces una fecha máxima de
respuesta. Eso basta para ordenar, no para trabajar: «ACTUALIZACIÓN MATRIZ DE
REQUERIMIENTOS CIUDADANOS PRIORIZADOS, CORTE AGOSTO 2026» no dice qué hay que
hacer, ni con qué, ni a quién se le manda. Eso está dentro del documento, en
un párrafo del tipo «solicito remitir hasta el 15 de septiembre la matriz
actualizada, en formato Excel, a esta Dirección».

Este módulo lee ese texto y saca los COMPROMISOS: cada cosa concreta que hay
que entregar, con su fecha, su destinatario y, sobre todo, la FRASE TEXTUAL de
la que se dedujo. Esa cita es lo que hace que el resultado sea verificable —
sin ella, una lista generada por un modelo es una lista que hay que creerse.

Reglas que se le imponen al modelo, y por qué:

  * No inventar fechas. Si el documento no dice cuándo, se devuelve sin fecha.
    Una fecha inventada en una lista de plazos es peor que ninguna: se cumple
    la equivocada y se incumple la de verdad.
  * Distinguir lo que hay que HACER de lo que sólo se informa. La mitad de lo
    que llega a una bandeja es «para su conocimiento», y meterlo como tarea
    convierte la lista en ruido que se deja de mirar.
  * Copiar la frase, no resumirla. Es lo que permite comprobar en dos segundos
    si el modelo entendió bien.

Sin clave de IA esto no funciona, y no pasa nada: la fecha máxima de respuesta
que da el propio sistema se sigue leyendo igual. Lo que se pierde es el detalle
de qué entregar, no el plazo.
"""
import json
import re

ESQUEMA = {
    "compromisos": [
        {
            "que": "acción concreta que hay que hacer, en infinitivo",
            "entregable": "qué se entrega (informe, matriz, certificación...) o ''",
            "para_cuando": "AAAA-MM-DD, o null si el documento no lo dice",
            "a_quien": "persona o unidad a la que hay que responder, o ''",
            "cita": "la frase TEXTUAL del documento de la que sale esto",
            "es_para_mi": "true si lo tiene que hacer quien recibe el documento",
            "urgente": "true si el documento lo marca como urgente o prioritario",
        }
    ]
}

INSTRUCCIONES = """Eres un asistente de un coordinador de planificación de un
municipio del Ecuador. Lees documentos oficiales (oficios, memorandos,
circulares) que le llegan por el sistema de gestión documental y extraes lo que
él tiene que HACER.

Reglas estrictas:

1. Extrae SÓLO obligaciones concretas para quien recibe el documento. Lo que
   sea «para su conocimiento», un acuse de recibo o una respuesta a algo que él
   pidió NO es un compromiso: devuelve la lista vacía.

2. NO inventes fechas. Si el documento no dice cuándo, `para_cuando` va en
   null. Si dice un plazo relativo («en el término de 5 días»), calcúlalo desde
   la fecha del documento que se te indica, y dilo en la cita.
   En el Ecuador «término» son días HÁBILES y «plazo» son días corridos.

3. `cita` debe ser una frase copiada LITERALMENTE del documento, no un resumen.
   Si no puedes citar la frase, no incluyas ese compromiso.

4. Escribe `que` en infinitivo y en concreto: «Remitir la matriz de
   requerimientos ciudadanos actualizada al corte de agosto», no «Atender el
   oficio».

5. Si el mismo documento pide varias cosas con fechas distintas, devuelve un
   compromiso por cada una.

Responde ÚNICAMENTE con un objeto JSON."""


class SinIA(Exception):
    """No hay motor de IA configurado. No es un error: es una función menos."""


def disponible():
    try:
        from app import ia
        return ia.disponible()
    except Exception:
        return False


def _pedir_a_claude(instrucciones, contenido, max_tokens=2000):
    from app import ia
    cliente = ia._cliente()
    r = cliente.messages.create(
        model=ia.MODELO, max_tokens=max_tokens,
        system=instrucciones + '\n\nEsquema:\n' + json.dumps(ESQUEMA, ensure_ascii=False),
        messages=[{'role': 'user', 'content': contenido}])
    texto = ''.join(b.text for b in r.content if getattr(b, 'type', '') == 'text')
    # El modelo a veces envuelve el JSON en un bloque de código.
    m = re.search(r'\{.*\}', texto, re.S)
    if not m:
        raise RuntimeError('la IA no devolvió JSON')
    return json.loads(m.group(0))


def _pedir_a_mistral(instrucciones, contenido):
    from app import ia
    return ia._pedir_json_mistral(instrucciones, contenido, ESQUEMA, max_tokens=2000)


def leer_compromisos(documento, texto, limite=12000):
    """Qué hay que entregar según este documento. Devuelve (compromisos, aviso).

    Nunca lanza: un documento que la IA no sabe leer no puede tumbar la pasada
    entera. Devuelve lista vacía y el motivo, que queda apuntado."""
    if not texto or len(texto.strip()) < 60:
        return [], 'el documento no trae texto que leer'
    if not disponible():
        return [], 'sin IA configurada'

    contexto = (
        f"Documento: {documento.get('tipo','')} {documento.get('numero','')}\n"
        f"Asunto: {documento.get('asunto','')}\n"
        f"De: {documento.get('de','')}\n"
        f"Fecha del documento: {(documento.get('fecha_doc') or '')[:10]}\n"
        f"Fecha máxima de respuesta según el sistema: {documento.get('vence') or 'no consta'}\n"
        f"Lo recibe: Marco Antonio Posligua San Martín, "
        f"Coordinador de Planificación y Proyectos / Observatorio de Seguridad Ciudadana\n"
        f"\n--- TEXTO DEL DOCUMENTO ---\n{texto[:limite]}")

    try:
        from app import ia
        motor = ia.proveedor_activo()
        datos = (_pedir_a_mistral(INSTRUCCIONES, contexto) if motor == 'mistral'
                 else _pedir_a_claude(INSTRUCCIONES, contexto))
    except Exception as e:
        return [], f'la IA no pudo leerlo: {str(e)[:150]}'

    salida = []
    for c in (datos.get('compromisos') or [])[:10]:
        if not isinstance(c, dict):
            continue
        que = (c.get('que') or '').strip()
        cita = (c.get('cita') or '').strip()
        # Sin frase que lo respalde no entra. Es la regla que separa «lo dice el
        # documento» de «lo dedujo un modelo», y es lo único que hace que esta
        # lista se pueda auditar sin abrir los ciento y pico documentos.
        if not que or not cita:
            continue
        fecha = (c.get('para_cuando') or '') or ''
        if fecha and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(fecha).strip()):
            fecha = ''
        salida.append({
            'que': que[:300],
            'entregable': (c.get('entregable') or '')[:120],
            'para_cuando': fecha,
            'a_quien': (c.get('a_quien') or '')[:120],
            'cita': cita[:500],
            'es_para_mi': bool(c.get('es_para_mi', True)),
            'urgente': bool(c.get('urgente')),
        })
    return salida, None
