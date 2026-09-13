"""
Capa de seguridad.

Responsabilidades
-----------------
1. Generar y verificar el PIN de 4 dígitos de cada participante.
2. Generar los tokens de acceso (links únicos).
3. Autenticar al administrador y firmar/verificar su cookie de sesión.
4. Bloqueo temporal ante intentos fallidos (anti fuerza bruta).

Notas criptográficas
--------------------
* Un PIN de 4 dígitos solo tiene 10 000 combinaciones: por sí solo es débil.
  Por eso se combina con dos defensas:
    a) el token secreto de 32 bytes en la URL (el atacante ni siquiera sabe
       a qué participante atacar sin el link), y
    b) bloqueo tras N intentos fallidos.
* El PIN se guarda con PBKDF2-HMAC-SHA256, 200 000 iteraciones y salt de
  16 bytes por participante. Nunca en claro.
* Todas las comparaciones de secretos usan `secrets.compare_digest`, que es
  de tiempo constante y no filtra información por *timing*.

CLI incluida:
    python -m app.security hash-password "mi-password"
    python -m app.security secret-key
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sys
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings

logger = logging.getLogger("amigo_secreto.security")

# --- Parámetros de derivación de clave -------------------------------------
_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32

ADMIN_COOKIE_NAME = "amigo_secreto_admin"
_ADMIN_SALT = "admin-session-v1"


# ===========================================================================
# 1. PIN de participante
# ===========================================================================
def generate_pin() -> str:
    """
    Genera un PIN de 4 dígitos criptográficamente seguro (0000–9999).

    Se usa `secrets.randbelow` en lugar de `random`, que es predecible.
    """
    return f"{secrets.randbelow(10_000):04d}"


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    """
    Deriva el hash del PIN.

    Returns:
        (hash_hex, salt_hex)
    """
    salt = salt or secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM,
        pin.encode("utf-8"),
        bytes.fromhex(salt),
        _PBKDF2_ITERATIONS,
    )
    return digest.hex(), salt


def verify_pin(pin: str, pin_hash: str, salt: str) -> bool:
    """Verifica un PIN en tiempo constante."""
    if not pin or not pin_hash or not salt:
        return False
    try:
        candidate, _ = hash_pin(pin, salt)
    except ValueError:
        # `salt` malformado (no hexadecimal)
        return False
    return secrets.compare_digest(candidate, pin_hash)


def generate_access_token() -> str:
    """Token URL-safe de 32 bytes (~43 caracteres) para el link único."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def normalize_pin(raw: str | None) -> str:
    """Limpia lo que el usuario escribe en el formulario del PIN."""
    if raw is None:
        return ""
    return "".join(ch for ch in raw.strip() if ch.isdigit())[:4]


# ===========================================================================
# 2. Bloqueo por intentos fallidos
# ===========================================================================
def is_locked(locked_until: datetime | None) -> bool:
    """¿El participante está bloqueado ahora mismo?"""
    if locked_until is None:
        return False
    # SQLite devuelve datetimes naive; se asumen UTC.
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


def lockout_expiry(settings: Settings) -> datetime:
    """Momento en el que expira un bloqueo iniciado ahora."""
    return datetime.now(timezone.utc) + timedelta(
        minutes=settings.pin_lockout_minutes
    )


# ===========================================================================
# 3. Autenticación del administrador
# ===========================================================================
def looks_like_password_hash(value: str) -> bool:
    """
    ¿El valor tiene la pinta de un hash generado por `hash_admin_password`?

    Formato esperado:  pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>

    Sirve para distinguir un hash real de una contraseña escrita por error en
    la variable `ADMIN_PASSWORD_HASH`.
    """
    parts = (value or "").strip().split("$")
    return (
        len(parts) == 4
        and parts[0] == "pbkdf2_sha256"
        and parts[1].isdigit()
    )


def hash_admin_password(password: str, salt: str | None = None) -> str:
    """
    Hash de la contraseña del admin en formato portable:

        pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>
    """
    salt = salt or secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_admin_password(password: str, settings: Settings | None = None) -> bool:
    """
    Comprueba la contraseña del admin.

    Prioridad: `ADMIN_PASSWORD_HASH` (recomendado) sobre `ADMIN_PASSWORD`
    (texto plano, aceptable solo en desarrollo).

    Tolerancia a un error muy común
    --------------------------------
    Mucha gente escribe su contraseña *tal cual* en `ADMIN_PASSWORD_HASH`, en
    vez del hash. Antes eso dejaba el panel inaccesible con cualquier
    contraseña, sin ninguna pista de por qué. Ahora se detecta el formato: si
    el valor no es un hash PBKDF2 se trata como contraseña en claro y se
    registra un aviso en los logs.

    Esto no debilita nada: comparar contra un valor en claro es exactamente lo
    que hace `ADMIN_PASSWORD`, y la alternativa —rechazarlo— solo conseguía
    dejar fuera al propio administrador.
    """
    settings = settings or get_settings()

    configured = (settings.admin_password_hash or "").strip()

    if configured and looks_like_password_hash(configured):
        try:
            algorithm, iterations, salt, expected = configured.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                _PBKDF2_ALGORITHM,
                password.encode("utf-8"),
                bytes.fromhex(salt),
                int(iterations),
            )
        except (ValueError, TypeError):
            logger.error(
                "ADMIN_PASSWORD_HASH tiene formato de hash PBKDF2 pero está "
                "corrupto o incompleto. ¿Se copió entero, con todos los '$'?"
            )
            return False
        return secrets.compare_digest(digest.hex(), expected)

    if configured:
        # Valor presente pero que no es un hash: se asume contraseña en claro.
        logger.warning(
            "ADMIN_PASSWORD_HASH no contiene un hash PBKDF2, así que se está "
            "usando como contraseña en texto plano. Funciona, pero para "
            "producción genera el hash con: "
            'python -m app.security hash-password "tu-contraseña"'
        )
        return hmac.compare_digest(
            password.encode("utf-8"), configured.encode("utf-8")
        )

    plain = settings.admin_password or ""
    if not plain:
        # Sin contraseña configurada no se permite entrar: fallar cerrado.
        logger.error(
            "No hay contraseña de administrador configurada: define "
            "ADMIN_PASSWORD_HASH (recomendado) o ADMIN_PASSWORD."
        )
        return False
    return hmac.compare_digest(password.encode("utf-8"), plain.encode("utf-8"))


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=_ADMIN_SALT)


def create_admin_session_token(settings: Settings | None = None) -> str:
    """Cookie firmada (no cifrada) con marca de tiempo."""
    settings = settings or get_settings()
    return _serializer(settings).dumps({"role": "admin"})


def validate_admin_session_token(
    token: str, settings: Settings | None = None
) -> bool:
    """Verifica firma y antigüedad del token de sesión."""
    settings = settings or get_settings()
    try:
        data = _serializer(settings).loads(
            token, max_age=settings.admin_session_minutes * 60
        )
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(data, dict) and data.get("role") == "admin"


def is_admin_request(request: Request, settings: Settings) -> bool:
    """¿La request trae una cookie de admin válida?"""
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    return bool(token) and validate_admin_session_token(token, settings)


# --- Dependencias de FastAPI ----------------------------------------------
def require_admin(
    request: Request, settings: Settings = Depends(get_settings)
) -> bool:
    """
    Dependencia para endpoints JSON del admin.

    Acepta dos mecanismos:
      * cookie de sesión (uso desde el navegador), o
      * cabecera `X-Admin-Password` (uso desde scripts / curl / CI).
    """
    if is_admin_request(request, settings):
        return True

    header_password = request.headers.get("x-admin-password")
    if header_password and verify_admin_password(header_password, settings):
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de administrador inválidas o sesión expirada.",
        headers={"WWW-Authenticate": "X-Admin-Password"},
    )


# ===========================================================================
# CLI de utilidades
# ===========================================================================
def _main(argv: list[str]) -> int:  # pragma: no cover - utilidad manual
    if len(argv) >= 2 and argv[1] == "hash-password":
        if len(argv) < 3:
            print('Uso: python -m app.security hash-password "tu-password"')
            return 2
        print(hash_admin_password(argv[2]))
        return 0
    if len(argv) >= 2 and argv[1] == "secret-key":
        print(secrets.token_urlsafe(48))
        return 0

    print(
        "Comandos disponibles:\n"
        '  python -m app.security hash-password "tu-password"\n'
        "  python -m app.security secret-key"
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv))
