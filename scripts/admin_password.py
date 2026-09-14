#!/usr/bin/env python3
"""
Generador y verificador de la contraseña de administrador.

Funciona en Windows, macOS y Linux, y **no depende del resto del proyecto**:
puedes copiar solo este archivo a cualquier máquina con Python 3.

Por qué existe
--------------
Generar el hash con un `python -c "…"` de una sola línea es frágil: cada shell
trata los `$` y las comillas de forma distinta. En PowerShell y CMD, el escape
`\\$` que necesita bash se queda tal cual y produce un hash inválido, que luego
no deja entrar a nadie. Este script pide la contraseña de forma interactiva, así
que no hay comillas ni escapes de por medio.

Uso
---
    # Generar el hash (te lo pide sin mostrarlo en pantalla)
    python scripts/admin_password.py generar

    # Comprobar que un hash corresponde a una contraseña
    python scripts/admin_password.py verificar

    # Revisar si un valor pegado tiene el formato correcto
    python scripts/admin_password.py revisar
"""

from __future__ import annotations

import getpass
import hashlib
import secrets
import sys

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 200_000
SALT_BYTES = 16


# ---------------------------------------------------------------------------
# Núcleo criptográfico (idéntico al de app/security.py)
# ---------------------------------------------------------------------------
def make_hash(password: str, salt: str | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt or secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return f"{ALGORITHM}${iterations}${salt}${digest.hex()}"


def clean(value: str) -> str:
    """Quita comillas, escapes de shell y espacios de un valor pegado."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return "".join(value.replace("\\$", "$").split())


def check(password: str, stored: str) -> bool:
    stored = clean(stored)
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), expected)


def inspect(value: str) -> list[str]:
    """Devuelve la lista de problemas encontrados en un valor pegado."""
    raw = (value or "").strip()
    problems: list[str] = []

    if not raw:
        return ["El valor está vacío."]

    if "\\" in raw:
        problems.append(
            "Contiene barras invertidas '\\'. Se generó con el comando de bash "
            "en PowerShell o CMD. Vuelve a generarlo con este script."
        )
    if raw[0] in {'"', "'"}:
        problems.append("Empieza por comillas: quítalas al pegarlo en el panel.")
    if any(c.isspace() for c in raw):
        problems.append(
            "Contiene espacios o saltos de línea: probablemente se partió al "
            "copiarlo desde la terminal."
        )

    parts = clean(raw).split("$")
    if len(parts) != 4:
        problems.append(
            f"Debería tener 4 partes separadas por '$' y tiene {len(parts)}. "
            "Falta parte del hash o se perdieron los '$' al pegarlo."
        )
        return problems

    algorithm, iterations, salt, digest = parts
    if algorithm != ALGORITHM:
        problems.append(f"El algoritmo debería ser '{ALGORITHM}' y es '{algorithm}'.")
    if not iterations.isdigit():
        problems.append(f"Las iteraciones deberían ser un número y son '{iterations}'.")
    if len(salt) != 32:
        problems.append(f"El salt debería tener 32 caracteres y tiene {len(salt)}.")
    if len(digest) != 64:
        problems.append(
            f"El hash debería tener 64 caracteres y tiene {len(digest)}: está cortado."
        )
    for name, value_ in (("salt", salt), ("hash", digest)):
        try:
            bytes.fromhex(value_)
        except ValueError:
            problems.append(f"El {name} tiene caracteres que no son hexadecimales.")

    return problems


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def ask_password(prompt: str = "Contraseña: ") -> str:
    """Pide la contraseña sin mostrarla y sin pasar por el shell."""
    password = getpass.getpass(prompt)
    if not password:
        print("\n❌ La contraseña no puede estar vacía.", file=sys.stderr)
        raise SystemExit(1)
    return password


def cmd_generar() -> int:
    print("Genera el valor de ADMIN_PASSWORD_HASH.\n")
    password = ask_password()
    repeat = getpass.getpass("Repite la contraseña: ")

    if password != repeat:
        print("\n❌ Las contraseñas no coinciden. No se generó nada.", file=sys.stderr)
        return 1

    value = make_hash(password)

    # Verificación inmediata: nunca entregues un hash sin comprobarlo.
    if not check(password, value):
        print("\n❌ Error interno: el hash generado no verifica.", file=sys.stderr)
        return 1

    print("\n✅ Hash generado y verificado.\n")
    print("Copia ESTA LÍNEA COMPLETA en la variable ADMIN_PASSWORD_HASH:\n")
    print(value)
    print()
    print(f"   ({len(value)} caracteres, 3 símbolos '$', sin espacios)")
    print("\nRecuerda dejar ADMIN_PASSWORD vacía.")
    return 0


def cmd_verificar() -> int:
    print("Comprueba que un hash corresponde a una contraseña.\n")
    stored = input("Pega aquí el hash: ").strip()

    problems = inspect(stored)
    if problems:
        print("\n⚠️  El valor pegado tiene problemas de formato:")
        for problem in problems:
            print(f"   · {problem}")
        print()

    password = ask_password()

    if check(password, stored):
        print("\n✅ CORRECTO: ese hash corresponde a esa contraseña.")
        print("   Si aun así el panel no te deja entrar, el problema no es el")
        print("   hash: revisa GET /admin/diagnostics en tu servidor.")
        return 0

    print("\n❌ NO COINCIDEN: ese hash no corresponde a esa contraseña.")
    print("   Genera uno nuevo con:  python scripts/admin_password.py generar")
    return 1


def cmd_revisar() -> int:
    print("Revisa el formato de un valor sin necesitar la contraseña.\n")
    stored = input("Pega aquí el valor de ADMIN_PASSWORD_HASH: ").strip()

    problems = inspect(stored)
    if not problems:
        print("\n✅ El formato es válido.")
        print("   Si la contraseña no entra, es que el hash no corresponde a")
        print("   esa contraseña. Usa 'verificar' para confirmarlo.")
        return 0

    print("\n❌ Problemas encontrados:")
    for problem in problems:
        print(f"   · {problem}")
    return 1


COMMANDS = {
    "generar": cmd_generar,
    "verificar": cmd_verificar,
    "revisar": cmd_revisar,
    # Alias en inglés, por comodidad.
    "generate": cmd_generar,
    "verify": cmd_verificar,
    "check": cmd_revisar,
}


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""

    if command not in COMMANDS:
        print(__doc__)
        print("Comandos: generar | verificar | revisar")
        return 2

    try:
        return COMMANDS[command]()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
