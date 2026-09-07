# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los oficios que se bajaron a mano, leídos como si los hubiera traído el robot.

El servidor no alcanza CuencaDOC y el acceso automático pide el texto de una
imagen cada vez que el Municipio decide pedirlo. Mientras eso siga así, la vía
corta es descargar los oficios desde el navegador, dejarlos en una carpeta y que
el programa los lea de ahí.

Lo que este módulo NO hace es una segunda versión del sistema. Saca de cada PDF
los mismos campos que el recolector saca de la ficha —número, asunto, quién lo
manda, fecha y plazo— y arma el mismo registro, para que a partir de ahí todo
siga por el camino de siempre: `deducir_plazo` calcula para cuándo es,
`almacen` lo guarda y `planificacion` lo lleva al cronograma. Dos puertas, una
sola casa.

Lo que se pierde respecto a la recolección automática, y conviene tener presente
al mirar el resultado:

  * el enlace al documento dentro de Quipux, que sólo existe si está impreso en
    el propio oficio;
  * la bandeja de la que venía, que en el sistema se sabe por dónde apareció y
    aquí no consta en ninguna parte;
  * la fecha de vencimiento QUE DA EL SISTEMA. Aquí el plazo sale de leer el
    texto, así que se marca como deducido —`seguro: False`— y en el cronograma
    entra con prioridad media en vez de alta. No es lo mismo que el sistema diga
    «vence el 12» a que una frase diga «en el término de cinco días», y quien lo
    mire tiene derecho a saber cuál de las dos está viendo.

Uso:
    python -m quipux.carpeta                     # lee ~/Dropbox/QUIPUX
    python -m quipux.carpeta --ver               # enseña lo que entendió, sin guardar
    python -m quipux.carpeta C:\\otra\\carpeta   # otra carpeta
"""
import os
import re
import sys
import zipfile
from datetime import date, datetime

from . import documentos as docs

CARPETA_POR_DEFECTO = os.path.join(os.path.expanduser('~'), 'Dropbox', 'QUIPUX')

MESES = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
         'julio': 7, 'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10,
         'noviembre': 11, 'diciembre': 12}

# El número de un documento de Quipux: siglas de la entidad, año, secuencia y una
# letra al final que dice de qué tipo es (O de oficio, M de memorando…).
RE_NUMERO = re.compile(r'\b([A-Z][A-Z0-9]{2,}(?:-[A-Z0-9]+){1,6}-\d{4}-\d{3,6}-[A-Z])\b')
RE_FECHA_LARGA = re.compile(
    r'\b(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de[l]?\s+)?(\d{4})', re.I)
RE_ASUNTO = re.compile(r'asunto\s*:\s*(.+)', re.I)
RE_DE = re.compile(r'^\s*(?:de|remitente)\s*:\s*(.+)$', re.I | re.M)


def _texto_del_pdf(ruta):
    """Todo el texto del PDF. Si no se puede leer, cadena vacía y a seguir."""
    try:
        from pypdf import PdfReader
        lector = PdfReader(ruta)
        partes = []
        for pagina in lector.pages:
            try:
                partes.append(pagina.extract_text() or '')
            except Exception:
                continue
        return re.sub(r'[ \t]+', ' ', '\n'.join(partes)).strip()
    except Exception:
        return ''


def _fecha_larga(texto):
    """«Cuenca, 6 de septiembre de 2026» -> date(2026, 9, 6)."""
    m = RE_FECHA_LARGA.search(texto or '')
    if not m:
        return None
    mes = MESES.get(m.group(2).lower().strip())
    if not mes:
        return None
    try:
        return date(int(m.group(3)), mes, int(m.group(1)))
    except ValueError:
        return None


def _primera_linea_util(texto, saltar):
    for linea in (texto or '').splitlines():
        limpia = linea.strip(' .:-')
        if len(limpia) > 12 and not any(p in limpia.lower() for p in saltar):
            return limpia
    return ''


def leer_oficio(ruta):
    """Un PDF -> el mismo registro que devuelve la ficha del sistema."""
    texto = _texto_del_pdf(ruta)
    nombre = os.path.basename(ruta)

    # El número puede estar en el texto o en el propio nombre del archivo; el
    # del texto manda, porque el nombre lo pone quien descarga.
    m = RE_NUMERO.search(texto) or RE_NUMERO.search(nombre)
    numero = m.group(1) if m else os.path.splitext(nombre)[0][:60]

    asunto = ''
    m = RE_ASUNTO.search(texto)
    if m:
        asunto = m.group(1).strip()
        # El asunto de un oficio cabe en una línea; lo que sigue ya es el
        # cuerpo, y arrastrarlo entero deja títulos de mil caracteres.
        asunto = re.split(r'\s{3,}|\n', asunto)[0].strip()[:300]
    if not asunto:
        asunto = _primera_linea_util(
            texto, ('quipux', 'cuenca', 'gad ', 'municipal', 'oficio nro', 'señor'))[:300]

    m = RE_DE.search(texto)
    remitente = m.group(1).strip()[:120] if m else ''

    fecha_doc = _fecha_larga(texto)

    return {
        'id': numero,
        'numero': numero,
        'asunto': asunto or nombre,
        'de': remitente,
        'fecha_doc': fecha_doc.isoformat() if fecha_doc else '',
        'vence': '',          # el sistema lo daría; de un PDF no consta
        'area': '',           # se rellena con la carpeta que lo contiene
        'bandeja': 'Cargados a mano',
        'enlace': '',
        'archivo': ruta,
        'texto': texto,
    }


def _descomprimir(carpeta):
    """Deja abiertos los ZIP que haya, cada uno en su propia subcarpeta."""
    abiertos = []
    for nombre in sorted(os.listdir(carpeta)):
        if not nombre.lower().endswith('.zip'):
            continue
        destino = os.path.join(carpeta, os.path.splitext(nombre)[0])
        if os.path.isdir(destino):
            continue
        try:
            with zipfile.ZipFile(os.path.join(carpeta, nombre)) as z:
                z.extractall(destino)
            abiertos.append(nombre)
        except Exception as e:
            print(f'  no se pudo abrir {nombre}: {str(e)[:100]}')
    return abiertos


def leer_carpeta(carpeta=None, registro=print):
    """Todos los oficios de la carpeta, con su plazo ya deducido.

    El área sale del nombre de la subcarpeta que los contiene: así, separar en
    «OBSERVATORIO» y «PLANIFICACION» al descargar es todo lo que hace falta para
    que cada documento acabe en el proyecto que le toca."""
    carpeta = carpeta or CARPETA_POR_DEFECTO
    if not os.path.isdir(carpeta):
        raise RuntimeError(f'No existe la carpeta {carpeta}')

    abiertos = _descomprimir(carpeta)
    if abiertos:
        registro(f'  {len(abiertos)} zip abierto(s)')

    encontrados = []
    for raiz, _, archivos in os.walk(carpeta):
        for nombre in sorted(archivos):
            if nombre.lower().endswith('.pdf'):
                encontrados.append(os.path.join(raiz, nombre))

    documentos, vistos = [], set()
    for ruta in encontrados:
        doc = leer_oficio(ruta)
        # El área: la primera carpeta por debajo de la raíz, si la hay.
        relativa = os.path.relpath(os.path.dirname(ruta), carpeta)
        if relativa not in ('.', ''):
            doc['area'] = relativa.split(os.sep)[0].replace('_', ' ').strip()
        doc['area'] = doc['area'] or 'CuencaDOC'

        # El mismo oficio descargado dos veces es un oficio, no dos.
        if doc['numero'] in vistos:
            registro(f"  repetido, se omite: {doc['numero']}")
            continue
        vistos.add(doc['numero'])

        fecha, origen, seguro = docs.deducir_plazo(doc, doc.get('texto', ''))
        doc['plazo'] = {'fecha': fecha.isoformat() if fecha else '',
                        'origen': origen, 'seguro': seguro}
        doc['estado'] = 'abierto'
        doc['nuevo'] = True
        doc['n_adjuntos'] = 0
        documentos.append(doc)

    return documentos


def _resumen(documentos):
    con_plazo = [d for d in documentos if (d.get('plazo') or {}).get('fecha')]
    print('\n' + '=' * 60)
    print(f'Oficios leídos   : {len(documentos)}')
    print(f'Con plazo        : {len(con_plazo)}')
    sin_asunto = sum(1 for d in documentos if not d.get('asunto'))
    if sin_asunto:
        print(f'Sin asunto claro : {sin_asunto}  (habrá que mirarlos)')
    print('=' * 60)
    for d in documentos[:40]:
        plazo = (d.get('plazo') or {}).get('fecha') or '—'
        marca = '' if (d.get('plazo') or {}).get('seguro') else ' (deducido)'
        print(f"  {d['numero'][:34]:<34} {plazo:<11}{marca}")
        print(f"      {(d.get('asunto') or '')[:96]}")
    if len(documentos) > 40:
        print(f'  … y {len(documentos) - 40} más')


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    solo_ver = '--ver' in argv
    resto = [a for a in argv if not a.startswith('--')]
    carpeta = resto[0] if resto else None

    documentos = leer_carpeta(carpeta)
    _resumen(documentos)

    if solo_ver:
        print('\n(--ver: no se guardó nada)')
        return 0

    from . import almacen, planificacion
    almacen.guardar(documentos)
    creadas, actualizadas = almacen.crear_tareas(documentos)
    print(f'\nEn el archivo local: {len(documentos)} documento(s)')
    print(f'Tareas             : {creadas} nueva(s), {actualizadas} al día')

    db = planificacion.cliente_de_la_plataforma()
    if db is None:
        print('Plataforma         : sin conexión; lo local queda guardado igual')
        return 0
    pub = planificacion.publicar(db, documentos)
    print(f"Publicados         : {pub.get('subidos', 0)}"
          + (f" (error: {pub['error']})" if pub.get('error') else ''))
    crono = planificacion.volcar(db, documentos)
    print(f"Cronograma         : {crono.get('creadas', 0)} nueva(s), "
          f"{crono.get('actualizadas', 0)} con plazo cambiado")
    return 0


if __name__ == '__main__':
    sys.exit(main())
