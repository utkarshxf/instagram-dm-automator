import os
import logging
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # API Settings
    PROJECT_NAME: str = "Instagram DM Automator API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "prod_secret_key_to_be_replaced_in_env")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # MongoDB
    MONGO_URL: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    MONGO_DB: str = os.getenv("MONGO_DB", "ig_automator_db")
    
    # Redis (Arq & Rate Limiting)
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")
    REDIS_USERNAME: Optional[str] = os.getenv("REDIS_USERNAME", "default")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    JSON_LOGS: bool = os.getenv("JSON_LOGS", "False").lower() == "true"

    # Firebase
    FIREBASE_SERVICE_ACCOUNT_BASE64: Optional[str] = os.getenv("FIREBASE_SERVICE_ACCOUNT_BASE64")
    FIREBASE_STORAGE_BUCKET: str = os.getenv("FIREBASE_STORAGE_BUCKET", "flashcall-1d5e2.appspot.com")
    
    # CORS
    BACKEND_CORS_ORIGINS: list[str] = ["*"] # Adjust in production
    
    model_config = SettingsConfigDict(case_sensitive=True, env_file=".env")

settings = Settings()
