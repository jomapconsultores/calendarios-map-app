# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los quipux de CuencaDOC, en la computadora y con sus plazos a la vista.

El sistema de gestión documental del Municipio de Cuenca guarda lo que hay que
hacer y para cuándo, pero lo guarda ADENTRO: para saber qué se debe esta semana
hay que entrar, elegir el perfil, abrir bandeja por bandeja y leer documento por
documento. Y los adjuntos —que suelen ser el trabajo de verdad: la matriz, el
formulario, el instructivo— se quedan allí, o acaban en la carpeta de descargas
con nombres como `descarga(3).pdf`, que a los dos días no dicen nada.

Lo que hace este paquete, para las dos áreas de Marco Antonio Posligua San
Martín —OBSERVATORIO DE SEGURIDAD CIUDADANA y GESTIÓN DE PLANIFICACIÓN Y
PROYECTOS—, en una sola pasada:

  1. Entra a CuencaDOC con la credencial guardada en el llavero de Windows.
  2. Recorre las dos áreas y todas sus bandejas.
  3. Se trae la ficha de cada documento: quién, qué, cuándo, y el plazo.
  4. Descarga los adjuntos y los deja con NOMBRE PROPIO, clasificados por área,
     año y bandeja, junto a un índice con el enlace a cada documento en Quipux.
  5. Convierte en actividades del cronograma lo que tiene plazo, para que entre
     en el semáforo y en el aviso de incumplimiento que ya existen.

Sobre la clave: no se escribe en ningún archivo de este repositorio. Vive en el
Administrador de credenciales de Windows, cifrada por la cuenta de Windows del
propio usuario, igual que las contraseñas del correo. Se da de alta una vez con
`python -m quipux alta`.

Aviso de alcance: esto lee la cuenta de UNA persona y sólo hace lo que esa
persona haría a mano —abrir, mirar y descargar lo suyo—. No firma, no reasigna,
no responde y no borra nada en CuencaDOC. Lo único que escribe está en esta
computadora.
"""

from .sesion import Quipux, ErrorQuipux          # noqa: F401
from .credenciales import guardar, leer, borrar  # noqa: F401

__all__ = ['Quipux', 'ErrorQuipux', 'guardar', 'leer', 'borrar']
