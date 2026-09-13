"""
Punto de entrada de la aplicación.

Arranque local:
    uvicorn app.main:app --reload

Documentación interactiva (OpenAPI):
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import BASE_DIR, get_settings
from app.database import init_db
from app.routers import admin, participant
from app.templating import render

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("amigo_secreto")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Crea el esquema de la base de datos al arrancar."""
    init_db()
    logger.info("Base de datos lista (%s)", settings.database_url)
    if settings.secret_key.startswith("dev-secret-key"):
        logger.warning(
            "SECRET_KEY por defecto en uso. Define una propia antes de "
            "desplegar en producción."
        )
    yield
    logger.info("Aplicación detenida.")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Aplicación para organizar un Amigo Secreto: carga de participantes, "
        "sorteo sin autoasignaciones ni regalos cruzados, links personales con "
        "código de 4 dígitos y panel de administración."
    ),
    lifespan=lifespan,
)

# Archivos estáticos (CSS).
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "app" / "static")),
    name="static",
)

# Routers.
app.include_router(participant.router)
app.include_router(admin.router)


# ===========================================================================
# Manejadores de error: HTML para el navegador, JSON para la API
# ===========================================================================
def _wants_json(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/api") or path.startswith("/docs") or path.startswith("/openapi"):
        return True
    if path.startswith("/admin") and not _is_html_admin_path(path):
        return True
    return "application/json" in request.headers.get("accept", "")


def _is_html_admin_path(path: str) -> bool:
    html_paths = (
        "/admin",
        "/admin/login",
        "/admin/logout",
        "/admin/dashboard",
        "/admin/participants",
        "/admin/draw",
        "/admin/validate",
        "/admin/credentials.csv",
    )
    return path in html_paths or path.endswith("/reset")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}
        )
    return render(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """
    Errores de validación de Pydantic en formato JSON.

    `exc.errors()` puede incluir en `ctx` objetos `Exception` que no son
    serializables a JSON; se convierten a texto antes de responder.
    """
    errors = []
    for error in exc.errors():
        clean = {k: v for k, v in error.items() if k != "ctx"}
        if "ctx" in error:
            clean["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        errors.append(clean)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder({"detail": errors}),
    )


@app.get("/health", tags=["infra"], summary="Health check")
def health() -> dict[str, str]:
    """Endpoint de salud para balanceadores y plataformas de despliegue."""
    return {"status": "ok", "version": __version__}
