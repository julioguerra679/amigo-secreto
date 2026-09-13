"""
Configuración compartida de pytest.

Estrategia de aislamiento
-------------------------
Las variables de entorno se fijan **antes** de importar cualquier módulo de
`app`, porque `app.config` las lee al construir el objeto `Settings` y
`app.database` crea el engine a partir de ellas en tiempo de importación.

Cada test recibe una base de datos vacía: el fixture borra y vuelve a crear
todas las tablas. Se usa un archivo temporal en lugar de `:memory:` porque en
SQLite cada conexión a memoria abre una base distinta, y FastAPI usa un pool.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# --- Entorno de test (debe ir antes de importar `app`) ---------------------
_TMP_DIR = tempfile.mkdtemp(prefix="amigo-secreto-tests-")
_DB_PATH = Path(_TMP_DIR) / "test.db"

# Por defecto la suite corre sobre SQLite. Para verificar que todo funciona
# igual en PostgreSQL (lo que se usa al desplegar en un plan gratuito):
#     TEST_DATABASE_URL=postgresql://user@host:5432/mi_db pytest
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite:///{_DB_PATH}"
)
os.environ["SECRET_KEY"] = "clave-de-test-no-usar-en-produccion"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["ADMIN_PASSWORD_HASH"] = ""
os.environ["MAIL_ENABLED"] = "false"
os.environ["BASE_URL"] = "http://testserver"
os.environ["MAX_PIN_ATTEMPTS"] = "5"

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
ADMIN_HEADERS = {"X-Admin-Password": ADMIN_PASSWORD}


@pytest.fixture
def client() -> Iterator:
    """`TestClient` de FastAPI sobre una base de datos limpia."""
    from fastapi.testclient import TestClient

    from app.database import Base, engine
    from app.main import app
    from app.routers import admin as admin_router

    # Base de datos virgen para cada test.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # El panel cachea en memoria las últimas credenciales emitidas.
    admin_router._LAST_CREDENTIALS = []

    with TestClient(app) as test_client:
        yield test_client


def sample_people(n: int = 6) -> list[dict]:
    """Genera `n` participantes de prueba con nombres únicos."""
    names = [
        "Ana",
        "Luis",
        "Carla",
        "Diego",
        "Elena",
        "Fabio",
        "Gaby",
        "Hugo",
        "Irene",
        "Javier",
    ]
    people: list[dict] = []
    for i in range(n):
        base = names[i % len(names)]
        suffix = "" if i < len(names) else str(i // len(names))
        people.append({"name": f"{base}{suffix}", "email": None})
    return people
