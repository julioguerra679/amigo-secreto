"""
Capa de persistencia: motor SQLAlchemy + sesiones + creación del esquema.

Se usa SQLite por simplicidad de despliegue (un solo archivo, sin servidor).
Si mañana se migra a PostgreSQL basta con cambiar `DATABASE_URL`; el resto
del código no cambia porque todo pasa por el ORM.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BASE_DIR, get_settings

settings = get_settings()


class Base(DeclarativeBase):
    """Clase base declarativa de la que heredan todos los modelos."""


def _build_engine() -> Engine:
    """
    Crea el engine de SQLAlchemy.

    * **SQLite**: se permite el acceso desde varios hilos y se asegura que la
      carpeta del archivo `.db` exista.
    * **PostgreSQL**: se activa `pool_pre_ping`, que prueba la conexión antes
      de usarla. Es imprescindible en planes gratuitos, donde el proveedor
      cierra las conexiones inactivas y, sin esta opción, la primera petición
      tras un rato de calma fallaría con "server closed the connection".
    """
    url = settings.database_url

    connect_args: dict = {}
    if url.startswith("sqlite"):
        # FastAPI atiende requests en un threadpool; SQLite por defecto
        # prohíbe usar una conexión desde otro hilo.
        connect_args["check_same_thread"] = False

        # Asegura que la carpeta del archivo .db exista antes de conectar.
        if ":memory:" not in url:
            # sqlite:///./data/x.db  ->  ./data/x.db
            raw_path = url.split("sqlite:///", 1)[-1]
            db_path = Path(raw_path)
            if not db_path.is_absolute():
                db_path = BASE_DIR / raw_path.lstrip("./")
            db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(
        url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
        # En SQLite el pool es irrelevante; en PostgreSQL estos valores son
        # conservadores y encajan en el límite de conexiones de un plan free.
        pool_recycle=1800,
    )


engine: Engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """
    Activa las claves foráneas en SQLite (desactivadas por defecto) y el
    modo WAL, que mejora la concurrencia lectura/escritura.
    """
    if "sqlite3" not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def init_db() -> None:
    """Crea las tablas si no existen. Se llama al arrancar la app."""
    from app import models  # noqa: F401  (registra los modelos en Base.metadata)

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Dependencia de FastAPI que entrega una sesión por request y la cierra
    siempre al terminar, incluso si la vista lanza una excepción.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
