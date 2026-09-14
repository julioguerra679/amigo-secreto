"""
Tests de la construcción de los links personales.

Regresión concreta: al desplegar sin configurar `BASE_URL`, la app seguía
usando su valor de desarrollo y repartía links apuntando a
`http://127.0.0.1:8000`. El sorteo se generaba bien, el panel se veía bien, y
cada participante recibía un enlace que en su móvil daba "no se encuentra el
sitio". Un fallo mudo, que además parecía culpa del participante.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from app.config import DEFAULT_BASE_URL, Settings
from app.links import participant_link_from, request_base_url, resolve_base_url


def _settings(base_url: str = DEFAULT_BASE_URL) -> Settings:
    return Settings(_env_file=None, secret_key="t", base_url=base_url)


def _request(
    host: str = "mi-sorteo.onrender.com",
    scheme: str = "http",
    forwarded_proto: str | None = None,
    forwarded_host: str | None = None,
) -> Request:
    """Construye una petición ASGI mínima, como la que recibiría el servidor."""
    headers = [(b"host", host.encode())]
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": "/admin/dashboard",
            "raw_path": b"/admin/dashboard",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": (host, 443),
            "client": ("10.0.0.1", 1234),
        }
    )


# ===========================================================================
# BASE_URL configurada: manda siempre
# ===========================================================================
def test_configured_base_url_wins() -> None:
    settings = _settings("https://sorteo.midominio.com")
    link = resolve_base_url(_request(), settings)
    assert link == "https://sorteo.midominio.com"


def test_configured_base_url_loses_trailing_slash() -> None:
    assert _settings("https://x.com/").public_base_url == "https://x.com"


# ===========================================================================
# BASE_URL sin configurar: se deduce de la petición
# ===========================================================================
def test_default_base_url_is_detected() -> None:
    assert _settings().base_url_is_default is True
    assert _settings("https://x.com").base_url_is_default is False


def test_unconfigured_base_url_falls_back_to_the_request() -> None:
    """El caso que rompía el sorteo: sin esto saldría 127.0.0.1."""
    base = resolve_base_url(_request(host="mi-sorteo.onrender.com"), _settings())
    assert base == "http://mi-sorteo.onrender.com"
    assert "127.0.0.1" not in base


def test_proxy_headers_give_https() -> None:
    """
    Render, Railway y Fly terminan el TLS y hablan HTTP con la app. Sin leer
    X-Forwarded-Proto, los links saldrían como http://.
    """
    request = _request(scheme="http", forwarded_proto="https")
    assert resolve_base_url(request, _settings()) == "https://mi-sorteo.onrender.com"


def test_proxy_chain_uses_the_first_value() -> None:
    """En una cadena de proxys, el primer valor es el del cliente original."""
    request = _request(forwarded_proto="https,http", forwarded_host="publico.com,interno")
    assert request_base_url(request) == "https://publico.com"


def test_invalid_forwarded_proto_is_ignored() -> None:
    """Esa cabecera la controla quien llama: no puede inventarse un esquema."""
    request = _request(scheme="http", forwarded_proto="javascript")
    assert request_base_url(request).startswith("http://")


def test_no_request_falls_back_to_settings() -> None:
    """Sin petición (por ejemplo al generar un correo) solo queda BASE_URL."""
    assert resolve_base_url(None, _settings()) == DEFAULT_BASE_URL


# ===========================================================================
# Forma del link
# ===========================================================================
@pytest.mark.parametrize("base", ["https://x.com", "https://x.com/"])
def test_link_has_exactly_one_slash(base: str) -> None:
    link = participant_link_from(base, "abc123")
    assert link == "https://x.com/participant/abc123"
    assert "//participant" not in link
