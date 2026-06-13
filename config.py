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
    DEFAULT_VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token_123")
    DEFAULT_APP_SECRET = os.getenv("APP_SECRET", "")
    DEFAULT_PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
    DEFAULT_PAGE_ID = os.getenv("PAGE_ID", "")
    DEFAULT_TUNNEL_URL = os.getenv("TUNNEL_URL", "https://ready-otters-happen.loca.lt")
