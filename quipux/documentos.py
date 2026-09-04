# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Leer la bandeja, abrir cada documento y bajarse lo que trae adjunto.

La bandeja de CuencaDOC es una tabla HTML con encabezados en español —De,
Asunto, Fecha Documento, Número Documento, Nro. Trámite…— y cada fila lleva un
enlace de la forma:

    mostrar_documento("20260001491136975931", "DGPG-2050-2026", "2")
                       └ id interno            └ número oficial   └ bandeja

Ese id de veinte dígitos es lo que identifica al documento en todo el sistema:
sirve para abrir la ficha, para bajar los anexos y para armar el enlace que se
guarda en el índice.

Las columnas se localizan POR SU TÍTULO, no por su posición. Es más trabajo,
pero una tabla que cambia de orden es de las averías que no se notan: seguiría
funcionando y guardando el número de trámite en el campo del asunto.

Sobre el plazo: Quipux no tiene un campo «fecha de entrega» que se pueda leer
sin más. El plazo aparece de tres maneras, y se buscan las tres, por orden de
confianza: el trámite con fecha de vencimiento propia; la fecha límite escrita
en el reasignamiento; y, en último lugar, lo que diga el texto del documento
(«en el término de 5 días», «hasta el 15 de octubre»). Lo que sale del texto se
marca como TAL en el índice — una fecha deducida de una frase no puede pesar lo
mismo que una que el sistema dio por buena.
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from lxml import html as lxml_html

# Cómo se llama cada cosa en la tabla, y cómo la llamamos aquí. Se aceptan
# varios nombres porque no todas las bandejas titulan igual la misma columna.
COLUMNAS = {
    'de':          ('de', 'remitente', 'para'),
    'asunto':      ('asunto',),
    'fecha_doc':   ('fecha documento', 'fecha'),
    'numero':      ('numero documento', 'número documento', 'no. documento'),
    'referencia':  ('no. referencia', 'nro. referencia', 'referencia'),
    'tipo':        ('tipo documento', 'tipo'),
    'tramite':     ('nro. tramite', 'nro. trámite', 'no. tramite', 'numero tramite'),
    'anterior':    ('usuario anterior',),
    'categoria':   ('categoria', 'categoría'),
    'ultima':      ('fecha ultima accion', 'fecha última acción', 'ultima accion'),
    'vence':       ('fecha vencimiento', 'vencimiento', 'fecha maxima', 'fecha límite'),
}

# Extensiones que se dan por buenas al bajar un adjunto. No es una lista de
# seguridad —el municipio no manda virus— sino de cordura: si lo que baja es un
# .html, casi siempre es la página de sesión caducada disfrazada de archivo.
EXTENSIONES = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt',
               '.ods', '.csv', '.txt', '.jpg', '.jpeg', '.png', '.gif', '.zip',
               '.rar', '.7z', '.xml', '.p7m', '.dwg', '.kmz', '.kml')

RE_MOSTRAR = re.compile(
    r'mostrar_documento\s*\(\s*[\'"](\d+)[\'"]\s*,\s*[\'"]([^\'"]*)[\'"]', re.I)

# «en el término de 5 días», «en un plazo de 10 días hábiles»…
RE_PLAZO_DIAS = re.compile(
    r'(?:t[eé]rmino|plazo|lapso)\s+(?:m[aá]ximo\s+)?de\s+(\d{1,3})\s*'
    r'\(?\s*\d*\s*\)?\s*d[ií]as?\s*(h[aá]biles|laborables|t[eé]rmino)?', re.I)

# «hasta el 15 de octubre de 2026», «hasta el 2026-10-15», «hasta el 15/10/2026»
RE_HASTA_ISO = re.compile(r'hasta\s+el\s+(\d{4})-(\d{2})-(\d{2})', re.I)
RE_HASTA_DMY = re.compile(r'hasta\s+el\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})', re.I)
RE_HASTA_TXT = re.compile(
    r'hasta\s+el\s+(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de[l]?\s+(\d{4})', re.I)

MESES = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
         'julio': 7, 'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10,
         'noviembre': 11, 'diciembre': 12}


# ============================================================
#  LA BANDEJA
# ============================================================
def _norm(t):
    t = re.sub(r'\s+', ' ', (t or '')).strip().lower()
    for a, b in (('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')):
        t = t.replace(a, b)
    return t


def _mapa_de_columnas(tabla):
    """Título de columna → posición. Devuelve {} si esta tabla no es la bandeja."""
    filas = tabla.xpath('.//tr')
    for fila in filas[:4]:
        celdas = fila.xpath('./th|./td')
        titulos = [_norm(c.text_content()) for c in celdas]
        if not any('asunto' == t for t in titulos):
            continue
        mapa = {}
        for i, titulo in enumerate(titulos):
            for campo, alias in COLUMNAS.items():
                if campo in mapa:
                    continue
                if any(_norm(a) == titulo for a in alias):
                    mapa[campo] = i
        if 'asunto' in mapa:
            return mapa
    return {}


def leer_bandeja(html, base):
    """Los documentos de una página de bandeja, con lo que la tabla dice de cada
    uno. Lo que no está en la tabla —el texto, los anexos— se pide después: son
    una petición por documento y no siempre hace falta."""
    doc = lxml_html.fromstring(html)
    doc.make_links_absolute(base, resolve_base_href=False)
    for tabla in doc.xpath('//table'):
        mapa = _mapa_de_columnas(tabla)
        if not mapa:
            continue
        salida = []
        for fila in tabla.xpath('.//tr'):
            celdas = fila.xpath('./td')
            if len(celdas) <= mapa['asunto']:
                continue
            enlaces = ' '.join(
                (a.get('href') or '') + ' ' + (a.get('onclick') or '')
                for a in fila.xpath('.//a'))
            m = RE_MOSTRAR.search(enlaces)
            if not m:
                continue
            registro = {'id': m.group(1), 'numero': m.group(2).strip()}
            for campo, i in mapa.items():
                if i < len(celdas):
                    registro[campo] = re.sub(r'\s+', ' ', celdas[i].text_content()).strip()
            registro['tiene_anexos'] = bool(
                fila.xpath('.//img[contains(translate(@src,"ANEXO","anexo"),"anexo")'
                           ' or contains(translate(@title,"ANEXO","anexo"),"anexo")'
                           ' or contains(translate(@src,"CLIP","clip"),"clip")]'))
            salida.append(registro)
        if salida:
            return salida
    return []


def enlaces_de_paginas(html, base):
    """Las demás páginas de la bandeja.

    Sin esto sólo se vería la primera: la bandeja de Reasignados tiene ciento y
    pico documentos y enseña veinte. Quedarse con la primera página y dar la
    pasada por completa es la manera más silenciosa de perder ochenta
    compromisos."""
    doc = lxml_html.fromstring(html)
    salida, vistos = [], set()
    for a in doc.xpath('//a[@href]'):
        href = a.get('href') or ''
        texto = re.sub(r'\s+', ' ', a.text_content()).strip()
        if not (texto.isdigit() or texto.lower() in ('siguiente', '>', '>>', 'último', 'ultimo')):
            continue
        destino = None
        m = re.search(r'paginador_reload_div\s*\(\s*[\'"]([^\'"]+)', href)
        if m:
            destino = m.group(1)
        elif 'cuerpo.php' in href or 'pagina' in href.lower():
            destino = href
        if destino and destino not in vistos:
            vistos.add(destino)
            salida.append({'texto': texto, 'destino': destino})
    return salida


# ============================================================
#  LA FICHA DEL DOCUMENTO
# ============================================================
def leer_ficha(html, base):
    """Lo que trae la pantalla del documento: su texto y sus anexos."""
    doc = lxml_html.fromstring(html)
    doc.make_links_absolute(base, resolve_base_href=False)
    texto = re.sub(r'[ \t]+', ' ', doc.text_content())
    texto = re.sub(r'\n\s*\n+', '\n', texto).strip()

    anexos, vistos = [], set()
    for a in doc.xpath('//a[@href]'):
        href = (a.get('href') or '').strip()
        nombre = re.sub(r'\s+', ' ', a.text_content()).strip()
        junto = href + ' ' + (a.get('onclick') or '')
        parece = (any(e in href.lower() for e in EXTENSIONES)
                  or any(e in nombre.lower() for e in EXTENSIONES)
                  or 'descargar' in junto.lower()
                  or 'anexo' in junto.lower())
        if not parece or href.lower().startswith('javascript:void'):
            continue
        # El enlace real puede ir dentro de un javascript:descargar('...')
        m = re.search(r'[\'"]((?:https?:)?/?[^\'"]*?\.(?:%s))[\'"]'
                      % '|'.join(e.strip('.') for e in EXTENSIONES), junto, re.I)
        url = m.group(1) if m else href
        if url.startswith('javascript:'):
            continue
        url = urljoin(base, url)
        if url in vistos:
            continue
        vistos.add(url)
        anexos.append({'nombre': nombre or url.rsplit('/', 1)[-1], 'url': url})
    return {'texto': texto, 'anexos': anexos}


# ============================================================
#  EL PLAZO
# ============================================================
def _dia_habil(desde, dias):
    """Suma días hábiles. En la administración pública «término» son días
    hábiles y «plazo» son corridos; contar unos por otros mueve la fecha casi
    una semana, que es justo lo que separa cumplir de incumplir."""
    d, contados = desde, 0
    while contados < dias:
        d += timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def _fecha(valor):
    for patron in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(str(valor)[:10].strip(), patron).date()
        except Exception:
            continue
    return None


def deducir_plazo(registro, ficha_texto=''):
    """Para cuándo hay que tenerlo. Devuelve (fecha, de dónde salió, seguro).

    `seguro` distingue lo que dice el sistema de lo que se dedujo leyendo el
    documento. Las dos cosas sirven; mezclarlas, no: una fecha sacada de una
    frase puede estar mal, y quien la mire tiene derecho a saber cuál es cuál.
    """
    del_sistema = registro.get('vence')
    f = _fecha(del_sistema) if del_sistema else None
    if f:
        return f, 'fecha de vencimiento del trámite', True

    texto = ' '.join(filter(None, [registro.get('asunto', ''), ficha_texto or '']))
    if not texto:
        return None, '', False

    base_fecha = _fecha(registro.get('fecha_doc')) or date.today()

    m = RE_HASTA_ISO.search(texto)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), \
                   'fecha escrita en el documento', False
        except ValueError:
            pass
    m = RE_HASTA_DMY.search(texto)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))), \
                   'fecha escrita en el documento', False
        except ValueError:
            pass
    m = RE_HASTA_TXT.search(texto)
    if m and _norm(m.group(2)) in MESES:
        try:
            return date(int(m.group(3)), MESES[_norm(m.group(2))], int(m.group(1))), \
                   'fecha escrita en el documento', False
        except ValueError:
            pass

    m = RE_PLAZO_DIAS.search(texto)
    if m:
        dias = int(m.group(1))
        habiles = bool(m.group(2))
        fecha = _dia_habil(base_fecha, dias) if habiles else base_fecha + timedelta(days=dias)
        como = f"{dias} días {'hábiles' if habiles else 'corridos'} desde el documento"
        return fecha, como, False

    return None, '', False
