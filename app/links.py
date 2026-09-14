"""
Construcción de los links personales de los participantes.

El problema que resuelve
------------------------
El link que recibe cada persona es el único camino a su resultado, así que si
apunta al sitio equivocado el juego entero queda inservible — y el fallo es
mudo: el servidor funciona, el sorteo se genera y el panel se ve perfecto.

Esto ocurre cuando `BASE_URL` se queda con su valor de desarrollo
(`http://127.0.0.1:8000`) al desplegar. Los links generados apuntan entonces a
*la máquina de quien los abre*, y el participante ve un "no se encuentra el
sitio" sin que nadie sospeche de la configuración del servidor.

La solución
-----------
`BASE_URL` sigue mandando cuando está configurada — es necesaria para los
correos y para cualquier link generado fuera de una petición HTTP. Pero si
está sin configurar, en lugar de producir links rotos se deduce la dirección
real a partir de la petición que está atendiendo el servidor: si el
organizador está usando el panel en `https://mi-sorteo.onrender.com`, los
links se construyen sobre esa misma dirección.

Detrás de un proxy
------------------
Render, Railway, Fly y cualquier nginx delante terminan el TLS y hablan con la
aplicación por HTTP plano. Si nos fiáramos de `request.url.scheme`
obtendríamos `http://` y los links saldrían sin cifrar. Por eso se leen las
cabeceras `X-Forwarded-Proto` y `X-Forwarded-Host`, que son las que llevan la
dirección que ve el usuario. Se toma siempre el **primer** valor de la lista:
es el del cliente original, mientras que los siguientes los añaden los proxys
intermedios.
"""

from __future__ import annotations

from fastapi import Request

from app.config import Settings

# Esquemas admitidos en X-Forwarded-Proto. Cualquier otra cosa se ignora: esa
# cabecera la controla quien hace la petición y no debe poder inventarse un
# esquema arbitrario.
_VALID_SCHEMES = frozenset({"http", "https"})


def _first(value: str | None) -> str:
    """Primer elemento de una cabecera con lista separada por comas."""
    if not value:
        return ""
    return value.split(",")[0].strip()


def request_base_url(request: Request) -> str:
    """
    Dirección pública desde la que se está sirviendo esta petición.

    Ejemplo: `https://amigo-secreto.onrender.com`
    """
    scheme = _first(request.headers.get("x-forwarded-proto")).lower()
    if scheme not in _VALID_SCHEMES:
        scheme = request.url.scheme

    host = (
        _first(request.headers.get("x-forwarded-host"))
        or request.headers.get("host", "")
        or request.url.netloc
    )

    return f"{scheme}://{host}".rstrip("/")


def resolve_base_url(request: Request | None, settings: Settings) -> str:
    """
    Dirección base que debe usarse para construir los links.

    Prioridad:
      1. `BASE_URL`, si el organizador la configuró. Manda siempre: puede que
         el sitio esté detrás de un dominio propio distinto al que ve el
         servidor.
      2. La dirección de la petición actual, si `BASE_URL` se quedó por
         defecto. Evita repartir links rotos por un despiste de configuración.
      3. `BASE_URL` tal cual, si no hay ninguna petición (por ejemplo, al
         generar un correo desde una tarea de fondo).
    """
    if not settings.base_url_is_default:
        return settings.public_base_url
    if request is not None:
        return request_base_url(request)
    return settings.public_base_url


def participant_link_from(base_url: str, token: str) -> str:
    """Link personal a partir de una dirección base ya resuelta."""
    return f"{base_url.rstrip('/')}/participant/{token}"


def participant_link(
    request: Request | None, settings: Settings, token: str
) -> str:
    """Link personal de un participante, con la dirección base correcta."""
    return participant_link_from(resolve_base_url(request, settings), token)
