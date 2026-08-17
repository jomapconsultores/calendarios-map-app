# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
import os
from datetime import timedelta
from dotenv import load_dotenv
load_dotenv()

class Config:
    APP_NAME = 'calendarios-map'
    SECRET_KEY = os.getenv('SECRET_KEY')

    # ── Sesión: se mantiene abierta, no cierra sola a los 20 minutos ──
    #
    # Estaba en 20 minutos de inactividad, y con eso el sistema echaba fuera a
    # todo el mundo a media mañana. Para un equipo de tres personas que trabajan
    # con esto abierto todo el día, el cierre agresivo no protegía de nada real:
    # sólo obligaba a volver a teclear la clave una y otra vez.
    #
    # Ahora la sesión dura una jornada completa y es DESLIZANTE: cada petición
    # renueva el plazo, así que trabajando no se corta nunca. Y con el
    # "recuérdame" de flask_login, cerrar el navegador o el móvil tampoco pierde
    # la sesión durante 30 días.
    #
    # Ambos plazos se pueden acortar sin tocar código, con SESSION_HOURS y
    # REMEMBER_DAYS en el entorno, por si algún día se quiere endurecer.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.getenv('SESSION_HOURS', '12')))
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # En desarrollo local por http: FLASK_INSECURE_COOKIES=1
    SESSION_COOKIE_SECURE = os.getenv('FLASK_INSECURE_COOKIES') != '1'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = os.getenv('FLASK_INSECURE_COOKIES') != '1'
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=int(os.getenv('REMEMBER_DAYS', '30')))
    SUPABASE_URL = os.getenv('SUPABASE_URL', '')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY', '')
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
    GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', '')
    # Microsoft To-Do (Azure AD app)
    MS_CLIENT_ID     = os.getenv('MS_CLIENT_ID', '')
    MS_CLIENT_SECRET = os.getenv('MS_CLIENT_SECRET', '')
    MS_REDIRECT_URI  = os.getenv('MS_REDIRECT_URI', '')
    # Secreto para disparar la sincronización automática To-Do ⇄ Sistema desde un cron externo
    CRON_SECRET = os.getenv('CRON_SECRET', '')

    # ── Correo saliente: avisos de incumplimiento del calendario de vencimientos ──
    # Sin SMTP_HOST la aplicación funciona igual y el aviso queda desactivado (se
    # ve en la pantalla de Proyectos, no falla en silencio). Con Gmail hace falta
    # una "contraseña de aplicación", no la clave normal de la cuenta.
    SMTP_HOST     = os.getenv('SMTP_HOST', '')
    SMTP_PORT     = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER     = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM     = os.getenv('SMTP_FROM', '') or os.getenv('SMTP_USER', '')
    SMTP_SSL      = os.getenv('SMTP_SSL', '0') == '1'   # 1 = puerto 465 directo
    # A quién se le reclama el incumplimiento y a qué hora se revisa (local).
    AVISO_EMAIL   = os.getenv('AVISO_EMAIL', 'jomapconsultores@gmail.com')
    AVISO_HORA    = int(os.getenv('AVISO_HORA', '8'))
    TIMEZONE = 'America/Guayaquil'
    # Sincronización de navegadores (Avast ⇄ Brave). ACTIVA por defecto: el acceso
    # ya está blindado a un único administrador dueño (OWNER_EMAIL) + rol admin.
    # En la web funciona el flujo CSV de contraseñas; marcadores y lectura directa
    # de perfiles sólo funcionan en la máquina Windows local. Para desactivarla
    # por completo en un despliegue, pon BROWSER_SYNC_ENABLED=0 en su entorno.
    BROWSER_SYNC_ENABLED = os.getenv('BROWSER_SYNC_ENABLED', '1') == '1'
    BROWSER_SYNC_OWNER_EMAIL = os.getenv('BROWSER_SYNC_OWNER_EMAIL', 'jomapconsultores@gmail.com').lower()