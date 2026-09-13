#!/usr/bin/env python3
"""
Demo end-to-end contra un servidor en marcha.

Uso:
    # Terminal 1
    uvicorn app.main:app --reload

    # Terminal 2
    python scripts/demo.py --password admin123

Hace, en orden:
    1. Carga 6 participantes con una exclusión mutua.
    2. Genera el sorteo con una semilla fija.
    3. Imprime la tabla de asignaciones.
    4. Consulta el resultado como si fuera un participante.
    5. Audita el sorteo.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PARTICIPANTS = [
    {"name": "Ana", "email": "ana@example.com"},
    {"name": "Luis", "email": "luis@example.com"},
    {"name": "Carla", "email": None},
    {"name": "Diego", "email": None},
    {"name": "Elena", "email": None},
    {"name": "Fabio", "email": None},
]

EXCLUSIONS = [{"giver": "Ana", "receiver": "Luis", "mutual": True}]


def request(
    method: str, url: str, password: str, payload: dict | None = None
) -> dict:
    """Petición HTTP mínima con la librería estándar (sin dependencias)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Admin-Password", password)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(f"❌ {method} {url} -> {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--seed", default="demo-2026")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    # --- 1. Cargar participantes -----------------------------------------
    print("1) Cargando participantes…")
    upload = request(
        "POST",
        f"{base}/admin/upload_participants",
        args.password,
        {
            "participants": PARTICIPANTS,
            "exclusions": EXCLUSIONS,
            "replace_existing": True,
        },
    )
    print(f"   ✔ {upload['created']} participantes, "
          f"{upload['exclusions']} exclusiones\n")

    print("   Credenciales (guárdalas: el PIN no se puede volver a leer):")
    for cred in upload["credentials"]:
        print(f"     {cred['name']:<8} PIN {cred['pin']}  {cred['link']}")
    print()

    # --- 2. Generar el sorteo --------------------------------------------
    print(f"2) Generando sorteo con semilla {args.seed!r}…")
    draw = request(
        "POST",
        f"{base}/admin/generate_assignments",
        args.password,
        {"seed": args.seed, "notes": "Sorteo de demostración"},
    )
    print(f"   ✔ Sorteo #{draw['draw']['id']} "
          f"({draw['draw']['participant_count']} personas, "
          f"{draw['attempts']} pasos de búsqueda)\n")

    # --- 3. Mostrar asignaciones -----------------------------------------
    print("3) Asignaciones:")
    for item in draw["assignments"]:
        print(f"     {item['giver']:<8} → {item['receiver']}")
    print()

    # --- 4. Consultar como participante ----------------------------------
    ana = next(c for c in upload["credentials"] if c["name"] == "Ana")
    token = ana["link"].rsplit("/", 1)[-1]
    query = urllib.parse.urlencode({"pin": ana["pin"]})
    print("4) Consultando como Ana (link + PIN)…")
    with urllib.request.urlopen(
        f"{base}/api/v1/participant/{token}?{query}", timeout=15
    ) as response:
        result = json.loads(response.read())
    print(f"   ✔ A {result['giver']} le toca regalarle a {result['receiver']}\n")

    # --- 5. Auditoría -----------------------------------------------------
    print("5) Validando el sorteo…")
    report = request("GET", f"{base}/admin/validate", args.password)
    if report["ok"]:
        print(f"   ✔ Sorteo válido ({report['checked']} asignaciones "
              "comprobadas, sin regalos cruzados)")
    else:
        print("   ✖ Problemas encontrados:")
        for issue in report["issues"]:
            print(f"     - [{issue['code']}] {issue['message']}")


if __name__ == "__main__":
    main()
