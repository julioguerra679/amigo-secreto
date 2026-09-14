"""
Tests de la configuración del acceso de administrador.

Regresión concreta: escribir la contraseña en claro dentro de
`ADMIN_PASSWORD_HASH` (un error muy fácil de cometer al desplegar) dejaba el
panel inaccesible con **cualquier** contraseña y sin ninguna pista del motivo.
"""

from __future__ import annotations

from app.config import Settings
from app.security import (
    describe_admin_auth,
    hash_admin_password,
    looks_like_password_hash,
    verify_admin_password,
)


def _settings(**overrides) -> Settings:
    """Settings aislado, sin leer `.env` ni el entorno del proceso."""
    base = {
        "secret_key": "clave-de-test",
        "admin_password": "",
        "admin_password_hash": "",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# ===========================================================================
# Detección del formato
# ===========================================================================
def test_looks_like_password_hash_accepts_real_hash() -> None:
    assert looks_like_password_hash(hash_admin_password("cualquier-cosa"))


def test_looks_like_password_hash_rejects_plain_passwords() -> None:
    for value in [
        "MiClaveSegura2026",
        "",
        "admin123",
        "clave$con$dolares",          # tiene '$' pero no el formato
        "pbkdf2_sha256$abc$def$ghi",  # iteraciones no numéricas
        "otro_algoritmo$1000$aa$bb",
    ]:
        assert not looks_like_password_hash(value), value


# ===========================================================================
# Camino correcto: hash PBKDF2
# ===========================================================================
def test_hash_authenticates_correct_password() -> None:
    settings = _settings(admin_password_hash=hash_admin_password("s3creta"))
    assert verify_admin_password("s3creta", settings)
    assert not verify_admin_password("otra", settings)


def test_hash_is_salted_so_two_hashes_differ() -> None:
    a = hash_admin_password("misma-clave")
    b = hash_admin_password("misma-clave")
    assert a != b
    assert verify_admin_password("misma-clave", _settings(admin_password_hash=a))
    assert verify_admin_password("misma-clave", _settings(admin_password_hash=b))


# ===========================================================================
# El error de despliegue: contraseña en claro dentro de ADMIN_PASSWORD_HASH
# ===========================================================================
def test_plain_password_in_hash_variable_still_works() -> None:
    """Antes esto bloqueaba el panel por completo; ahora deja entrar."""
    settings = _settings(admin_password_hash="MiClaveSegura2026")
    assert verify_admin_password("MiClaveSegura2026", settings)
    assert not verify_admin_password("otra-cosa", settings)


def test_plain_password_with_dollar_signs_in_hash_variable() -> None:
    """Una contraseña con '$' no debe confundirse con un hash."""
    settings = _settings(admin_password_hash="clave$con$dolares$raros")
    assert verify_admin_password("clave$con$dolares$raros", settings)
    assert not verify_admin_password("clave", settings)


def test_truncated_hash_does_not_authenticate() -> None:
    """Un hash cortado no debe dejar entrar a nadie, ni siquiera al dueño."""
    full = hash_admin_password("s3creta")
    truncated = full[: len(full) - 10]
    settings = _settings(admin_password_hash=truncated)
    assert not verify_admin_password("s3creta", settings)
    assert not verify_admin_password(truncated, settings)


# ===========================================================================
# Otros caminos
# ===========================================================================
def test_plain_admin_password_variable_works() -> None:
    settings = _settings(admin_password="admin123")
    assert verify_admin_password("admin123", settings)
    assert not verify_admin_password("admin124", settings)


def test_hash_takes_priority_over_plain_variable() -> None:
    settings = _settings(
        admin_password="la-vieja",
        admin_password_hash=hash_admin_password("la-nueva"),
    )
    assert verify_admin_password("la-nueva", settings)
    assert not verify_admin_password("la-vieja", settings)


def test_no_credentials_configured_fails_closed() -> None:
    """Sin contraseña configurada no entra nadie, ni con la cadena vacía."""
    settings = _settings()
    assert not verify_admin_password("", settings)
    assert not verify_admin_password("lo-que-sea", settings)


def test_empty_password_never_authenticates() -> None:
    settings = _settings(admin_password_hash=hash_admin_password("s3creta"))
    assert not verify_admin_password("", settings)


# ===========================================================================
# Hashes estropeados al copiar y pegar entre terminal y panel web
# ===========================================================================
def test_hash_generated_in_powershell_still_works() -> None:
    """
    PowerShell y CMD no interpretan el escape `\\$` de bash, así que el
    one-liner de la documentación produce `pbkdf2_sha256\\$200000\\$…`.
    """
    mangled = hash_admin_password("s3creta").replace("$", "\\$")
    assert verify_admin_password("s3creta", _settings(admin_password_hash=mangled))


def test_hash_pasted_with_quotes_still_works() -> None:
    """Al copiar de un `.env` es fácil arrastrar las comillas."""
    quoted = f'"{hash_admin_password("s3creta")}"'
    assert verify_admin_password("s3creta", _settings(admin_password_hash=quoted))


def test_hash_split_across_lines_still_works() -> None:
    """La terminal parte los valores largos; el salto se cuela al copiar."""
    real = hash_admin_password("s3creta")
    wrapped = real[:40] + "\n" + real[40:]
    assert verify_admin_password("s3creta", _settings(admin_password_hash=wrapped))


def test_normalization_does_not_weaken_verification() -> None:
    """Limpiar el valor no debe hacer que pase una contraseña equivocada."""
    mangled = hash_admin_password("s3creta").replace("$", "\\$")
    settings = _settings(admin_password_hash=mangled)
    assert not verify_admin_password("otra", settings)
    assert not verify_admin_password(mangled, settings)


def test_plain_password_with_spaces_is_compared_verbatim() -> None:
    """
    La normalización solo se aplica al camino del hash: una contraseña en
    claro puede llevar espacios y debe compararse tal cual.
    """
    settings = _settings(admin_password_hash="mi clave con espacios")
    assert verify_admin_password("mi clave con espacios", settings)
    assert not verify_admin_password("miclaveconespacios", settings)


# ===========================================================================
# Diagnóstico: describe la configuración sin filtrar secretos
# ===========================================================================
def test_diagnostics_never_leak_the_secret() -> None:
    secret_hash = hash_admin_password("s3creta")
    _, _, salt, digest = secret_hash.split("$", 3)

    report = describe_admin_auth(_settings(admin_password_hash=secret_hash))
    dumped = str(report)

    assert salt not in dumped
    assert digest not in dumped
    assert "s3creta" not in dumped
    assert secret_hash not in dumped


def test_diagnostics_identify_each_configuration() -> None:
    valid = describe_admin_auth(
        _settings(admin_password_hash=hash_admin_password("s3creta"))
    )
    assert valid["modo"].startswith("hash PBKDF2")
    assert valid["iteraciones"] == 200_000
    assert valid["caracteres_del_hash"] == 64

    powershell = describe_admin_auth(
        _settings(admin_password_hash=hash_admin_password("s3creta").replace("$", "\\$"))
    )
    assert powershell["contiene_barra_invertida"] is True

    plain = describe_admin_auth(_settings(admin_password_hash="MiClave"))
    assert "NO es un hash" in plain["modo"]

    nothing = describe_admin_auth(_settings())
    assert "SIN CONFIGURAR" in nothing["modo"]


def test_diagnostics_flag_a_truncated_hash() -> None:
    full = hash_admin_password("s3creta")
    report = describe_admin_auth(_settings(admin_password_hash=full[:-20]))
    assert "cortado" in report["diagnostico"]
