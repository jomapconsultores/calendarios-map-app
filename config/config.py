import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    APP_NAME = 'calendarios-map'
    SECRET_KEY = os.getenv('SECRET_KEY')
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