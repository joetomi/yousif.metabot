import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # Flask configuration
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key or secret_key.strip() == "":
        secret_key = "dev-secret-key-1234567890"
    SECRET_KEY = secret_key
    
    # Database configuration
    db_url = os.getenv("DATABASE_URL", "sqlite:///database.db")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Webhook defaults
    verify_token = os.getenv("VERIFY_TOKEN", "")
    if not verify_token or verify_token.strip() == "" or "placeholder" in verify_token.lower():
        verify_token = "my_verify_token_123"
    DEFAULT_VERIFY_TOKEN = verify_token
    
    app_id = os.getenv("APP_ID", "")
    if not app_id or app_id.strip() == "" or "placeholder" in app_id.lower():
        app_id = "1551224673018427"
    DEFAULT_APP_ID = app_id
    
    app_secret = os.getenv("APP_SECRET", "")
    if not app_secret or app_secret.strip() == "" or "placeholder" in app_secret.lower():
        app_secret = "d6aaee78477460221f880b3e328dca25"
    DEFAULT_APP_SECRET = app_secret
    
    page_access_token = os.getenv("PAGE_ACCESS_TOKEN", "")
    if not page_access_token or page_access_token.strip() == "" or "placeholder" in page_access_token.lower():
        page_access_token = ""
    DEFAULT_PAGE_ACCESS_TOKEN = page_access_token
    
    page_id = os.getenv("PAGE_ID", "")
    if not page_id or page_id.strip() == "" or "placeholder" in page_id.lower():
        page_id = ""
    DEFAULT_PAGE_ID = page_id
    
    tunnel_url = os.getenv("TUNNEL_URL", "")
    if not tunnel_url or tunnel_url.strip() == "" or "placeholder" in tunnel_url.lower():
        tunnel_url = "https://ready-otters-happen.loca.lt"
    DEFAULT_TUNNEL_URL = tunnel_url
    
    # Gemini AI defaults
    DEFAULT_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_GEMINI_SYSTEM_INSTRUCTION = os.getenv("GEMINI_SYSTEM_INSTRUCTION", "أنت مساعد ذكي ولطيف، أجب على استفسار العميل باحترافية واختصار.")
