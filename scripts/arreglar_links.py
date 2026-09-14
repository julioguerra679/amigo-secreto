#!/usr/bin/env python3
"""
Repara links de participantes que apuntan a `localhost`.

Para qué sirve
--------------
Si la aplicación se desplegó sin configurar `BASE_URL`, los links que se
repartieron se construyeron sobre `http://127.0.0.1:8000` y no abren en
ningún sitio.

La parte importante: **el token sigue siendo válido**. Lo único incorrecto es
la dirección del principio. Así que basta con cambiar esa parte para que los
links vuelvan a funcionar — sin volver a cargar la lista, sin regenerar nada y
**sin que cambie el código de 4 dígitos de nadie**.

Uso
---
    # Desde el CSV que descargaste del panel:
    python scripts/arreglar_links.py credenciales.csv https://tu-app.onrender.com

    # O pegando los links por teclado (termina con Ctrl+D, o Ctrl+Z en Windows):
    python scripts/arreglar_links.py - https://tu-app.onrender.com

Genera `credenciales_arregladas.csv` y muestra por pantalla la lista lista
para repartir.
"""

from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

# Direcciones "locales" que hay que sustituir por la pública.
LOCAL_PATTERN = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?::\d+)?",
    re.IGNORECASE,
)


def fix_link(link: str, public_base: str) -> tuple[str, bool]:
    """
    Devuelve `(link_corregido, se_cambió)`.

    Solo se toca el principio de la URL; el token se conserva intacto, que es
    lo que garantiza que el link siga siendo el de esa misma persona.
    """
    link = (link or "").strip()
    fixed, count = LOCAL_PATTERN.subn(public_base.rstrip("/"), link)
    return fixed, count > 0


def process_csv(text: str, public_base: str) -> tuple[list[dict], int]:
    """Procesa el CSV descargado del panel (nombre, email, pin, link)."""
    rows = list(csv.DictReader(io.StringIO(text)))
    changed = 0

    for row in rows:
        link = row.get("link") or ""
        row["link"], was_changed = fix_link(link, public_base)
        changed += was_changed

    return rows, changed


def process_plain(text: str, public_base: str) -> tuple[list[dict], int]:
    """Procesa texto suelto: una línea por link."""
    rows: list[dict] = []
    changed = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fixed, was_changed = fix_link(line, public_base)
        changed += was_changed
        rows.append({"nombre": "", "email": "", "pin": "", "link": fixed})

    return rows, changed


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2

    source, public_base = argv[1], argv[2]

    if not public_base.startswith(("http://", "https://")):
        print(
            f"❌ La dirección pública debe empezar por https:// — recibí {public_base!r}",
            file=sys.stderr,
        )
        return 1

    if LOCAL_PATTERN.match(public_base):
        print(
            "❌ La dirección pública no puede ser localhost: ese es justo el "
            "problema que estamos arreglando.",
            file=sys.stderr,
        )
        return 1

    # --- Leer la entrada --------------------------------------------------
    if source == "-":
        print("Pega los links (Ctrl+D para terminar, Ctrl+Z en Windows):\n")
        text = sys.stdin.read()
        rows, changed = process_plain(text, public_base)
    else:
        path = Path(source)
        if not path.exists():
            print(f"❌ No encuentro el archivo {source!r}.", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8-sig")
        if "link" in text.splitlines()[0]:
            rows, changed = process_csv(text, public_base)
        else:
            rows, changed = process_plain(text, public_base)

    if not rows:
        print("No encontré ningún link que procesar.", file=sys.stderr)
        return 1

    # --- Resultado --------------------------------------------------------
    print(f"\n✅ {len(rows)} links procesados, {changed} corregidos.\n")
    print("Listos para repartir (el código de cada persona NO cambia):\n")

    for row in rows:
        nombre = row.get("nombre") or ""
        pin = row.get("pin") or ""
        etiqueta = f"{nombre:<24} PIN {pin}  " if nombre else ""
        print(f"   {etiqueta}{row['link']}")

    salida = Path("credenciales_arregladas.csv")
    with salida.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["nombre", "email", "pin", "link"])
        for row in rows:
            writer.writerow(
                [
                    row.get("nombre", ""),
                    row.get("email", ""),
                    row.get("pin", ""),
                    row["link"],
                ]
            )

    print(f"\nGuardado en: {salida.resolve()}")

    if changed == 0:
        print(
            "\n⚠️  Ningún link apuntaba a localhost. Si aun así no funcionan, "
            "el problema es otro: comprueba /admin/diagnostics."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
