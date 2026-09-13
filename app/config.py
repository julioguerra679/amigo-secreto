"""
Configuración central de la aplicación.

Toda la configuración se lee de variables de entorno (o de un archivo `.env`)
mediante pydantic-settings. Esto cumple el factor III de los 12-factor apps:
la configuración vive en el entorno, no en el código.

Uso:
    from app.config import get_settings
    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto (…/amigo-secreto). `__file__` está en …/amigo-secreto/app/.
BASE_DIR: Path = Path(__file__).resolve().parent.parent


def normalize_database_url(url: str) -> str:
    """
    Adapta la cadena de conexión al driver que instala este proyecto.

    Las plataformas de despliegue (Render, Railway, Heroku, Fly…) entregan la
    URL de PostgreSQL en formatos que SQLAlchemy no interpreta como queremos:

        postgres://user:pass@host/db       ← formato heredado de Heroku;
                                             SQLAlchemy 2.x ni lo reconoce
        postgresql://user:pass@host/db     ← SQLAlchemy asume psycopg2, que
                                             este proyecto NO instala

    Ambos se reescriben a `postgresql+psycopg://` (psycopg 3, el driver que
    está en requirements.txt). Si la URL ya trae un driver explícito
    (`postgresql+asyncpg://`, `postgresql+psycopg2://`…) se respeta tal cual.
    """
    url = (url or "").strip()

    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings(BaseSettings):
    """Configuración tipada y validada de la aplicación."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Identidad del evento ---------------------------------------------
    app_name: str = "Amigo Secreto"
    base_url: str = "http://127.0.0.1:8000"

    # --- Seguridad ---------------------------------------------------------
    secret_key: str = "dev-secret-key-cambiala-en-produccion"
    admin_password: str = "admin123"
    admin_password_hash: str = ""
    admin_session_minutes: int = 120
    max_pin_attempts: int = Field(default=5, ge=1)
    pin_lockout_minutes: int = Field(default=15, ge=1)

    # --- Persistencia ------------------------------------------------------
    database_url: str = "sqlite:///./data/amigo_secreto.db"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        return normalize_database_url(v)

    # --- Correo (opcional) -------------------------------------------------
    mail_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Amigo Secreto <no-reply@example.com>"
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    # --- Propiedades derivadas --------------------------------------------
    @property
    def public_base_url(self) -> str:
        """BASE_URL normalizada, sin barra final."""
        return self.base_url.rstrip("/")

    def participant_link(self, token: str) -> str:
        """Construye el link único que se entrega a un participante."""
        return f"{self.public_base_url}/participant/{token}"


@lru_cache
def get_settings() -> Settings:
    """
    Devuelve la instancia única de configuración.

    Se cachea con `lru_cache` para no releer el `.env` en cada request.
    En los tests se puede limpiar con `get_settings.cache_clear()`.
    """
    return Settings()
