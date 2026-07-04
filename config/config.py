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