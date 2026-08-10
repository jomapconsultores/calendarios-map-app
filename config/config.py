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

    # ── Sesión: endurecida y con cierre por inactividad a los 20 minutos ──
    # No había ninguna directiva de sesión en todo el proyecto. La cookie era de
    # navegador —sin caducidad— así que la sesión duraba lo que durase el
    # navegador abierto; y con «restaurar pestañas» de Chrome y Edge, en la
    # práctica, indefinidamente. El temporizador de JavaScript de base.html no
    # cerraba nada: bastaba recargar para reiniciarlo.
    #
    # Flask firma la cookie con una marca de tiempo y la rechaza en el SERVIDOR
    # si supera este plazo, así que no se puede evadir desde el navegador. Para
    # que la ventana sea DESLIZANTE hacen falta las tres cosas juntas: este
    # plazo, session.permanent = True (before_request de app/__init__.py) y
    # SESSION_REFRESH_EACH_REQUEST, que reemite la cookie en cada petición.
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=20)
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # En desarrollo local por http: FLASK_INSECURE_COOKIES=1
    SESSION_COOKIE_SECURE = os.getenv('FLASK_INSECURE_COOKIES') != '1'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = os.getenv('FLASK_INSECURE_COOKIES') != '1'
    # El "recuérdame" de flask_login revive la sesión saltándose lo anterior:
    # se acota al mismo plazo para que no sea una puerta trasera.
    REMEMBER_COOKIE_DURATION = timedelta(minutes=20)
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
    TIMEZONE = 'America/Guayaquil'
    # Sincronización de navegadores (Avast ⇄ Brave). ACTIVA por defecto: el acceso
    # ya está blindado a un único administrador dueño (OWNER_EMAIL) + rol admin.
    # En la web funciona el flujo CSV de contraseñas; marcadores y lectura directa
    # de perfiles sólo funcionan en la máquina Windows local. Para desactivarla
    # por completo en un despliegue, pon BROWSER_SYNC_ENABLED=0 en su entorno.
    BROWSER_SYNC_ENABLED = os.getenv('BROWSER_SYNC_ENABLED', '1') == '1'
    BROWSER_SYNC_OWNER_EMAIL = os.getenv('BROWSER_SYNC_OWNER_EMAIL', 'jomapconsultores@gmail.com').lower()