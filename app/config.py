import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "NexusDude"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "production"
    PORT: int = 8000
    CORS_ORIGINS: str = "*"
    
    # Base de datos SQLite
    DATABASE_PATH: str = "/app/data/nexusdude.db"
    
    # Autenticación compartida con Nexus (SSO Delegado)
    NEXUS_JWT_SECRET: str = "nexus_orchestrator_secure_jwt_secret_key_2026_prod"
    NEXUS_ORCHESTRATOR_URL: str = "http://10.9.8.52:5001"
    
    # NetBox
    NETBOX_URL: str = "http://netbox:8080"
    NETBOX_EXTERNAL_URL: str = "http://10.9.8.52:8089"
    NETBOX_TOKEN: str = "49oUJOzZDVDrtNu32fYUU2YAbsaklSsB0haEIdP6"
    
    # Zabbix
    ZABBIX_URL: str = "https://10.9.1.7:8082"
    ZABBIX_TOKEN: str = "30c4fe2150817b9985106157674f28f5ce635337ec251822959583489d05c666"
    ZABBIX_USER: str = "Admin"
    ZABBIX_PASS: str = "zabbix"
    ZABBIX_POLL_INTERVAL: int = 30

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
