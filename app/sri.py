# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Cédula, RUC y consulta al SRI.

Dos capas independientes, y en ese orden:

  1) VALIDACIÓN LOCAL (`validar_documento`). El dígito verificador de la cédula
     y del RUC se calcula sin salir del servidor: es instantáneo, no depende de
     que el SRI esté arriba, y descarta de entrada los números inventados. Esta
     capa nunca se salta.

  2) CONSULTA AL SRI (`consultar_ruc`). Sólo para RUC, y sólo si la validación
     local pasó: pedirle al SRI un número que ya sabemos mal es gastar una
     llamada. Devuelve razón social, estado, clase de contribuyente y TODAS las
     actividades económicas registradas, más los establecimientos.

Si el SRI no responde el registro se guarda igual, con `sri_verified = False`.
Un servicio externo caído no puede bloquear el alta de un cliente.
"""
import re
import requests as req_lib

# Servicio público del SRI (el mismo que consume su consulta web).
SRI_BASE = 'https://srienlinea.sri.gob.ec'
SRI_CONSOLIDADO = (SRI_BASE + '/sri-catastro-sujeto-servicio-internet/rest/'
                   'ConsolidadoContribuyente/obtenerPorNumerosRuc')
SRI_ESTABLECIMIENTOS = (SRI_BASE + '/sri-catastro-sujeto-servicio-internet/rest/'
                        'Establecimiento/consultarEstablecimientosPorNumeroRuc')
_TIMEOUT = (5, 15)   # (conexión, lectura)

# Provincias válidas: 01-24, más 30 (ecuatorianos en el exterior).
_PROVINCIAS_VALIDAS = set(range(1, 25)) | {30}


def solo_digitos(valor):
    """Deja el documento en su forma canónica: sólo dígitos.

    Los archivos que llegan del cliente traen '1712345678-001', '1712345678 ',
    '17.123.456-78'. Normalizar aquí es lo que permite que la detección de
    duplicados compare peras con peras."""
    return re.sub(r'\D', '', str(valor or ''))


def detectar_tipo(documento):
    """Adivina el tipo de documento por su forma: 10 dígitos = cédula,
    13 = RUC, cualquier otra cosa = pasaporte (alfanumérico, sin algoritmo)."""
    d = solo_digitos(documento)
    if len(d) == 13:
        return 'ruc'
    if len(d) == 10:
        return 'cedula'
    return 'pasaporte'


def _modulo_10(nueve_digitos, verificador):
    """Algoritmo de cédula y de RUC de persona natural.

    Coeficientes alternados 2 y 1; a todo producto mayor que 9 se le resta 9."""
    total = 0
    for i, ch in enumerate(nueve_digitos):
        producto = int(ch) * (2 if i % 2 == 0 else 1)
        total += producto - 9 if producto > 9 else producto
    esperado = (10 - (total % 10)) % 10
    return esperado == verificador


def _modulo_11(digitos, coeficientes, verificador):
    """Algoritmo de RUC de sociedad privada y de entidad pública."""
    total = sum(int(ch) * coef for ch, coef in zip(digitos, coeficientes))
    resto = total % 11
    esperado = 0 if resto == 0 else 11 - resto
    return esperado == verificador


def validar_cedula(documento):
    """(válido, mensaje). Cédula ecuatoriana de 10 dígitos."""
    d = solo_digitos(documento)
    if len(d) != 10:
        return False, 'La cédula debe tener 10 dígitos'
    if int(d[:2]) not in _PROVINCIAS_VALIDAS:
        return False, f'Código de provincia inválido ({d[:2]})'
    if int(d[2]) > 5:
        return False, 'El tercer dígito de una cédula debe ser menor que 6'
    if not _modulo_10(d[:9], int(d[9])):
        return False, 'Dígito verificador incorrecto'
    return True, 'Cédula válida'


def validar_ruc(documento):
    """(válido, mensaje). RUC ecuatoriano de 13 dígitos.

    El tercer dígito decide el algoritmo: <6 persona natural, 6 sector público,
    9 sociedad privada. Cualquier otro valor no corresponde a un RUC real."""
    d = solo_digitos(documento)
    if len(d) != 13:
        return False, 'El RUC debe tener 13 dígitos'
    if int(d[:2]) not in _PROVINCIAS_VALIDAS:
        return False, f'Código de provincia inválido ({d[:2]})'
    if d[10:] == '000':
        return False, 'El código de establecimiento no puede ser 000'

    tercero = int(d[2])
    if tercero < 6:
        # Persona natural: los 10 primeros dígitos son una cédula.
        ok, msg = validar_cedula(d[:10])
        return (True, 'RUC de persona natural válido') if ok else (False, msg)
    if tercero == 6:
        ok = _modulo_11(d[:8], [3, 2, 7, 6, 5, 4, 3, 2], int(d[8]))
        return (True, 'RUC de entidad pública válido') if ok else (False, 'Dígito verificador incorrecto')
    if tercero == 9:
        ok = _modulo_11(d[:9], [4, 3, 2, 7, 6, 5, 4, 3, 2], int(d[9]))
        return (True, 'RUC de sociedad privada válido') if ok else (False, 'Dígito verificador incorrecto')
    return False, 'El tercer dígito no corresponde a ningún tipo de RUC'


def validar_documento(documento, tipo=None):
    """Punto de entrada único. Devuelve
    {tipo, numero, valido, mensaje}."""
    numero = solo_digitos(documento) or str(documento or '').strip().upper()
    tipo = (tipo or detectar_tipo(documento) or '').lower()
    if tipo == 'ruc':
        valido, mensaje = validar_ruc(numero)
    elif tipo == 'cedula':
        valido, mensaje = validar_cedula(numero)
    else:
        # Pasaporte: no hay algoritmo público; sólo se exige que tenga contenido.
        numero = str(documento or '').strip().upper()
        valido = 5 <= len(numero) <= 20
        mensaje = 'Pasaporte registrado' if valido else 'El pasaporte debe tener entre 5 y 20 caracteres'
        tipo = 'pasaporte'
    return {'tipo': tipo, 'numero': numero, 'valido': valido, 'mensaje': mensaje}


# ============================================================
#  CONSULTA AL SRI
# ============================================================
def _get_json(url, params):
    try:
        r = req_lib.get(url, params=params, timeout=_TIMEOUT,
                        headers={'Accept': 'application/json',
                                 'User-Agent': 'calendarios-map/1.0'})
        if r.status_code != 200:
            return None, f'SRI respondió HTTP {r.status_code}'
        return r.json(), None
    except Exception as e:
        return None, f'No se pudo consultar al SRI: {str(e)[:120]}'


def consultar_ruc(ruc):
    """Trae del SRI todo lo que publica sobre un RUC.

    Devuelve {'ok': bool, 'error': str|None, 'datos': {...}}. `datos` trae la
    razón social, el estado, la clase de contribuyente, la lista completa de
    actividades económicas y los establecimientos, más la respuesta cruda por si
    mañana hace falta un campo que hoy no se está leyendo."""
    numero = solo_digitos(ruc)
    valido, mensaje = validar_ruc(numero)
    if not valido:
        return {'ok': False, 'error': mensaje, 'datos': {}}

    cuerpo, error = _get_json(SRI_CONSOLIDADO, {'numeroRuc': numero})
    if error:
        return {'ok': False, 'error': error, 'datos': {}}
    if not cuerpo:
        return {'ok': False, 'error': 'El SRI no tiene registros para ese RUC', 'datos': {}}

    # El servicio devuelve una lista de un elemento.
    registro = cuerpo[0] if isinstance(cuerpo, list) and cuerpo else cuerpo
    if not isinstance(registro, dict):
        return {'ok': False, 'error': 'Respuesta del SRI con formato inesperado', 'datos': {}}

    info_extra = registro.get('informacionFechasContribuyente') or {}

    # Actividades: el consolidado trae la principal; los establecimientos traen
    # las de cada local. Se juntan sin repetir para que el contacto quede con el
    # panorama completo de lo que hace esa persona o empresa.
    actividades = []
    vistas = set()

    def _agregar(descripcion, origen, codigo=None):
        texto = (descripcion or '').strip()
        if not texto or texto.upper() in vistas:
            return
        vistas.add(texto.upper())
        actividades.append({'descripcion': texto, 'origen': origen, 'codigo': codigo})

    _agregar(registro.get('actividadEconomicaPrincipal'), 'principal')

    establecimientos = []
    locales, err_locales = _get_json(SRI_ESTABLECIMIENTOS, {'numeroRuc': numero})
    if not err_locales and isinstance(locales, list):
        for local in locales:
            if not isinstance(local, dict):
                continue
            establecimientos.append({
                'numero':     local.get('numeroEstablecimiento'),
                'nombre':     local.get('nombreFantasiaComercial'),
                'estado':     local.get('estado'),
                'matriz':     local.get('matriz'),
                'provincia':  local.get('provincia'),
                'canton':     local.get('canton'),
                'parroquia':  local.get('parroquia'),
                'direccion':  local.get('direccionCompleta'),
                'actividad':  local.get('actividadEconomica') or local.get('descripcionActividadEconomica'),
            })
            _agregar(local.get('actividadEconomica') or local.get('descripcionActividadEconomica'),
                     f"establecimiento {local.get('numeroEstablecimiento') or ''}".strip())

    # Matriz: la dirección de trabajo por defecto del contacto.
    matriz = next((e for e in establecimientos if str(e.get('matriz')).upper() in ('SI', 'S', 'TRUE')),
                  establecimientos[0] if establecimientos else {})

    datos = {
        'razon_social':      registro.get('razonSocial'),
        'nombre_comercial':  registro.get('nombreComercial') or matriz.get('nombre'),
        'estado':            registro.get('estadoContribuyente'),
        'clase':             registro.get('regimen') or registro.get('categoria'),
        'tipo':              registro.get('tipoContribuyente'),
        'obligado_contabilidad': registro.get('obligadoLlevarContabilidad'),
        'agente_retencion':  registro.get('agenteRetencion'),
        'contribuyente_especial': registro.get('contribuyenteEspecial'),
        'fecha_inicio':      (info_extra.get('fechaInicioActividades') or '')[:10] or None,
        'fecha_cese':        (info_extra.get('fechaCese') or '')[:10] or None,
        'fecha_actualizacion': (info_extra.get('fechaActualizacion') or '')[:10] or None,
        'actividades':       actividades,
        'establecimientos':  establecimientos,
        'direccion_matriz':  matriz.get('direccion'),
        'provincia':         matriz.get('provincia'),
        'ciudad':            matriz.get('canton'),
        'crudo':             registro,
    }
    return {'ok': True, 'error': None, 'datos': datos}


def contacto_desde_ruc(datos):
    """Traduce la respuesta del SRI a las columnas de `contacts`.

    Se hace aquí, y no en la vista, para que el alta individual y la importación
    en bloque rellenen exactamente los mismos campos."""
    razon = (datos.get('razon_social') or '').strip()
    nombres, apellidos = '', ''
    if (datos.get('tipo') or '').upper().startswith('PERSONA'):
        # Persona natural: el SRI escribe "APELLIDO1 APELLIDO2 NOMBRE1 NOMBRE2".
        partes = razon.split()
        if len(partes) >= 4:
            apellidos, nombres = ' '.join(partes[:2]), ' '.join(partes[2:])
        elif len(partes) == 3:
            apellidos, nombres = ' '.join(partes[:2]), partes[2]
        elif len(partes) == 2:
            apellidos, nombres = partes[0], partes[1]
        else:
            nombres = razon
    return {
        'business_name':  razon,
        'trade_name':     datos.get('nombre_comercial'),
        'first_name':     nombres or None,
        'last_name':      apellidos or None,
        'ruc_state':      datos.get('estado'),
        'ruc_class':      datos.get('clase'),
        'ruc_type':       datos.get('tipo'),
        'ruc_obligado_contabilidad': datos.get('obligado_contabilidad'),
        'ruc_start_date': datos.get('fecha_inicio'),
        'ruc_end_date':   datos.get('fecha_cese'),
        'ruc_activities': datos.get('actividades') or [],
        'ruc_establishments': datos.get('establecimientos') or [],
        'ruc_raw':        datos.get('crudo'),
        'work_address':   datos.get('direccion_matriz'),
        'city':           datos.get('ciudad'),
        'province':       datos.get('provincia'),
        'sri_verified':   True,
    }
