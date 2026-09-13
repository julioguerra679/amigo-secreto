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

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import BASE_DIR, get_settings
from app.database import get_db, init_db
from app.links import (
    participant_link_from,
    request_base_url,
    resolve_base_url,
)
from app.routers import admin, participant
from app.security import describe_admin_auth, looks_like_password_hash
from app.services import draws as draws_service
from app.services import participants as participants_service
from app.templating import render

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("amigo_secreto")

settings = get_settings()


def _check_admin_credentials() -> None:
    """
    Diagnostica la configuración del acceso de administrador **al arrancar**,
    para que un error de configuración se vea en los logs del despliegue en
    vez de descubrirse al no poder entrar al panel.
    """
    configured_hash = (settings.admin_password_hash or "").strip()
    plain = settings.admin_password or ""

    if configured_hash and looks_like_password_hash(configured_hash):
        logger.info("Acceso de administrador: hash PBKDF2 ✔")
        return

    if configured_hash:
        logger.warning(
            "⚠️  ADMIN_PASSWORD_HASH no parece un hash PBKDF2 (se esperaba "
            "algo como 'pbkdf2_sha256$200000$<salt>$<hash>'). Se usará ese "
            "valor como contraseña en texto plano: podrás entrar al panel "
            "escribiéndolo tal cual. Para hacerlo bien, genera el hash con: "
            'python -m app.security hash-password "tu-contraseña"'
        )
        return

    if plain:
        logger.warning(
            "Acceso de administrador mediante ADMIN_PASSWORD en texto plano. "
            "En producción usa ADMIN_PASSWORD_HASH."
        )
        return

    logger.error(
        "🚫 No hay contraseña de administrador configurada: el panel será "
        "inaccesible. Define ADMIN_PASSWORD_HASH o ADMIN_PASSWORD."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Crea el esquema de la base de datos al arrancar."""
    init_db()
    logger.info("Base de datos lista (%s)", settings.database_url)
    _check_admin_credentials()
    if settings.base_url_is_default:
        logger.warning(
            "⚠️  BASE_URL sin configurar. Los links se deducirán de cada "
            "petición (correcto si se usa el panel desde la URL pública), "
            "pero los correos saldrían apuntando a 127.0.0.1. Define "
            "BASE_URL con tu dirección pública."
        )
    else:
        logger.info("BASE_URL: %s", settings.public_base_url)
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
    """
    Endpoint de salud para balanceadores y plataformas de despliegue.

    El campo `version` sirve además para comprobar **qué código está vivo**:
    si tras un despliegue sigue apareciendo la versión antigua, la plataforma
    no ha publicado tus cambios (o estás mirando otro servicio).
    """
    return {"status": "ok", "version": __version__}


@app.get(
    "/admin/diagnostics",
    tags=["infra"],
    summary="Diagnóstico de configuración (no revela secretos)",
)
def diagnostics(request: Request, db: Session = Depends(get_db)) -> dict:
    """
    Responde **qué configuración recibió realmente el servidor**.

    Existe porque el fallo más frustrante al desplegar es "la contraseña no
    entra" sin saber si el problema está en el hash, en la variable de entorno
    o en que la plataforma sirve una versión antigua del código.

    Seguridad: no devuelve el hash, ni el salt, ni la contraseña, ni fragmento
    alguno de ellos — solo formatos, longitudes y banderas. Con esto nadie
    puede autenticarse. Aun así, puedes apagarlo con `DIAGNOSTICS_ENABLED=false`
    cuando termines de depurar.
    """
    if not settings.diagnostics_enabled:
        raise StarletteHTTPException(
            status_code=404, detail="Diagnóstico desactivado."
        )

    engine = settings.database_url.split(":", 1)[0].split("+", 1)[0]
    dotenv = BASE_DIR / ".env"

    # --- Estado de los datos ------------------------------------------------
    # Si el sitio se desplegó con SQLite sobre un disco efímero, tras cada
    # reinicio estos contadores vuelven a cero y todos los links dejan de
    # funcionar de golpe. Verlo aquí ahorra horas de búsqueda a ciegas.
    participants = participants_service.list_participants(db)
    draw = draws_service.get_active_draw(db)

    base = resolve_base_url(request, settings)
    # ⚠️ El ejemplo lleva un token FICTICIO a propósito. Usar aquí el de un
    # participante real convertiría este endpoint público en una filtración:
    # el token es la mitad de sus credenciales.
    ejemplo = participant_link_from(base, "TOKEN-DE-EJEMPLO-NO-REAL")

    links: dict = {
        "base_que_se_esta_usando": base,
        "BASE_URL_configurada": (
            None if settings.base_url_is_default else settings.public_base_url
        ),
        "direccion_real_de_esta_peticion": request_base_url(request),
        "ejemplo_de_link_generado": ejemplo,
    }
    if settings.base_url_is_default:
        links["diagnostico"] = (
            "BASE_URL no está configurada. Los links se construyen con la "
            "dirección de cada petición, así que salen bien si el organizador "
            "usa el panel desde la URL pública. Configúrala de todas formas: "
            "los correos se generan fuera de una petición y sin ella saldrían "
            "apuntando a 127.0.0.1."
        )
    elif settings.public_base_url != request_base_url(request):
        links["diagnostico"] = (
            f"BASE_URL dice {settings.public_base_url!r} pero esta petición "
            f"llegó a {request_base_url(request)!r}. Si la primera no es tu "
            "dirección pública real, los links repartidos no abrirán."
        )
    else:
        links["diagnostico"] = "BASE_URL coincide con la dirección real ✔"

    return {
        "version": __version__,
        "app_name": settings.app_name,
        "links_de_participantes": links,
        "datos": {
            "participantes_registrados": len(participants),
            "sorteo_activo": draw.id if draw else None,
            "asignaciones": (
                len(draws_service.get_assignments(db, draw)) if draw else 0
            ),
            "diagnostico": (
                "Sin participantes: si antes los había, la base de datos se "
                "vació (típico de SQLite sobre un disco efímero). Vuelve a "
                "cargar la lista y usa PostgreSQL."
                if not participants
                else "Hay datos cargados ✔"
            ),
        },
        "motor_de_base_de_datos": engine,
        "archivo_dotenv_presente": dotenv.exists(),
        "secret_key_por_defecto": settings.secret_key.startswith("dev-secret-key"),
        "acceso_admin": describe_admin_auth(settings),
    }
