# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""La clave de CuencaDOC vive en el llavero de Windows, no en un archivo.

Un sistema que entra solo necesita la contraseña guardada en alguna parte, y
ahí es donde se cometen los errores que después no tienen arreglo: dejarla en
un `.env` que acaba en un repositorio, o en un `config.json` que se copia con
la carpeta a otro equipo.

El Administrador de credenciales de Windows la cifra con la cuenta de Windows
del propio usuario: otro usuario de la misma máquina no la puede leer, y
copiada a otro equipo no sirve de nada. Es lo mismo que ya se hace con las
contraseñas de las cuentas de correo, así que tampoco es una pieza nueva que
haya que aprender a cuidar.
"""
import getpass

import keyring

SERVICIO = 'cuencadoc-quipux'
# Bajo esta clave se guarda QUIÉN es el usuario, para no tener que teclearlo
# cada vez ni dejarlo escrito en el código.
CLAVE_USUARIO = '__usuario__'


def guardar(usuario, contrasenia):
    """Da de alta la credencial. Devuelve el usuario guardado."""
    usuario = (usuario or '').strip()
    if not usuario or not contrasenia:
        raise ValueError('Hacen falta usuario y contraseña.')
    keyring.set_password(SERVICIO, CLAVE_USUARIO, usuario)
    keyring.set_password(SERVICIO, usuario, contrasenia)
    return usuario


def leer(usuario=None):
    """Devuelve (usuario, contraseña). Lanza si no hay nada dado de alta.

    El mensaje dice qué hacer, no sólo que falta algo: quien se encuentre esto
    a las siete de la mañana necesita el comando, no el diagnóstico."""
    usuario = (usuario or keyring.get_password(SERVICIO, CLAVE_USUARIO) or '').strip()
    if not usuario:
        raise RuntimeError(
            'No hay ninguna credencial de CuencaDOC guardada.\n'
            'Dala de alta una sola vez con:  python -m quipux alta')
    contrasenia = keyring.get_password(SERVICIO, usuario)
    if not contrasenia:
        raise RuntimeError(
            f'El usuario «{usuario}» está registrado pero sin contraseña.\n'
            'Vuelve a darlo de alta con:  python -m quipux alta')
    return usuario, contrasenia


def borrar(usuario=None):
    """Quita la credencial del llavero. Para cuando cambia la clave o el equipo."""
    usuario = usuario or keyring.get_password(SERVICIO, CLAVE_USUARIO)
    if not usuario:
        return False
    for clave in (usuario, CLAVE_USUARIO):
        try:
            keyring.delete_password(SERVICIO, clave)
        except Exception:
            pass
    return True


def hay_credencial():
    try:
        leer()
        return True
    except Exception:
        return False


def usuario_guardado():
    """Quién está dado de alta, sin tocar la contraseña."""
    try:
        return keyring.get_password(SERVICIO, CLAVE_USUARIO) or ''
    except Exception:
        return ''


def pedir_por_consola():
    """Alta interactiva. La contraseña NO se ve al teclearla ni queda en el
    historial de la consola, que es la otra forma habitual de que se escape."""
    print('Alta de la credencial de CuencaDOC (https://dq.cuenca.gob.ec)')
    print('Se guarda cifrada en el Administrador de credenciales de Windows.\n')
    # Se enseña el usuario que ya está puesto. Cuando algo no entra, la primera
    # duda es si el usuario es el que uno cree; verlo escrito ahorra la mitad de
    # las veces tener que teclearlo otra vez, y en la otra mitad delata el fallo.
    actual = usuario_guardado()
    if actual:
        print(f'Ahora mismo está guardado el usuario: {actual}')
        usuario = input('Usuario (Enter para dejar el mismo): ').strip() or actual
    else:
        usuario = input('Usuario: ').strip()
    contrasenia = getpass.getpass('Contraseña (no se muestra): ')
    if not usuario or not contrasenia:
        print('\nCancelado: hacen falta las dos cosas.')
        return None
    guardar(usuario, contrasenia)
    print(f'\nGuardada la credencial de «{usuario}».')
    return usuario
