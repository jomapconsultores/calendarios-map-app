# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los correos de todas las cuentas, en un solo sitio y atados a lo demás.

Hay nueve buzones —siete de Gmail, el Hotmail y el institucional del Consejo— y
cada uno se mira por su lado. Lo que llega por uno no se ve desde los otros, y
nada de lo que llega se cruza con lo que ya lleva esta plataforma: el oficio
DGPG-2050-2026 está en el Quipux con su plazo, y el correo en que alguien
pregunta por él está en otra bandeja, sin que nadie los ponga juntos.

Este paquete hace, en una sola pasada y desde esta computadora:

  1. Entra a las nueve cuentas con las credenciales que ya están dadas de alta
     para el conector de correo (`~/.claude/tools/gmail-mcp`): la contraseña de
     aplicación de Gmail en el llavero de Windows y el token de Microsoft en su
     caché cifrada. Aquí no se da de alta nada nuevo ni se guarda ninguna clave.
  2. Se trae lo nuevo de cada buzón —todo el correo en Gmail; recibidos,
     enviados y archivo en Microsoft— desde la última pasada.
  3. Lo guarda en un SQLite, un mensaje una sola vez aunque haya llegado a tres
     cuentas: lo reconoce por su `Message-ID`.
  4. Lo relaciona con el resto:
       · quipux       el número de un documento de CuencaDOC citado en el correo
       · cita         la invitación de calendario que el correo trae dentro
       · plazo        una fecha de entrega escrita en el texto
       · plataforma   los avisos que manda esta misma plataforma

Aviso de alcance: esto LEE. No marca como leído, no mueve, no borra y no
contesta nada en ningún buzón: las carpetas se abren en solo lectura. Lo único
que escribe está en esta computadora.
"""
