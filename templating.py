"""
Configuración única de Jinja2.

Se centraliza aquí para que todos los routers compartan el mismo entorno
(mismos filtros, mismas variables globales) y para poder inyectar valores
comunes —como el nombre de la app— sin repetirlos en cada vista.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, get_settings

TEMPLATES_DIR = BASE_DIR / "app" / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_datetime(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Filtro Jinja `|datetime` — formatea fechas en UTC de forma legible."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime(fmt) + " UTC"


templates.env.filters["datetime"] = _format_datetime


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
):
    """
    Atajo para renderizar una plantilla con el contexto común ya incluido.

    Uso:
        return render(request, "index.html", {"foo": 1})
    """
    settings = get_settings()
    base: dict[str, Any] = {
        "request": request,
        "app_name": settings.app_name,
        "year": datetime.now(timezone.utc).year,
    }
    base.update(context or {})
    return templates.TemplateResponse(
        request, template, base, status_code=status_code
    )
