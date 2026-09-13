"""
Tests de la API y del flujo completo end-to-end.

Cubren: autenticación del admin, carga de participantes, generación del
sorteo, consulta del participante, bloqueo por fuerza bruta y regeneración.
"""

from __future__ import annotations

from tests.conftest import ADMIN_HEADERS, sample_people


# ===========================================================================
# Infraestructura
# ===========================================================================
def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_home_page_renders(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Amigo Secreto" in response.text


# ===========================================================================
# Autenticación del administrador
# ===========================================================================
def test_admin_endpoints_require_auth(client) -> None:
    assert client.get("/admin/assignments").status_code == 401
    assert client.post("/admin/generate_assignments", json={}).status_code == 401
    assert (
        client.post("/admin/upload_participants", json={"participants": []}).status_code
        in (401, 422)
    )


def test_admin_wrong_password_is_rejected(client) -> None:
    response = client.get(
        "/admin/assignments", headers={"X-Admin-Password": "incorrecta"}
    )
    assert response.status_code == 401


def test_admin_login_sets_session_cookie(client) -> None:
    response = client.post(
        "/admin/login",
        data={"password": "test-admin-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "amigo_secreto_admin" in response.cookies


def test_admin_login_with_bad_password_shows_error(client) -> None:
    response = client.post("/admin/login", data={"password": "nope"})
    assert response.status_code == 200
    assert "incorrecta" in response.text.lower()


# ===========================================================================
# Carga de participantes
# ===========================================================================
def _upload(client, n: int = 6, exclusions=None):
    payload = {
        "participants": sample_people(n),
        "exclusions": exclusions or [],
        "replace_existing": True,
    }
    return client.post(
        "/admin/upload_participants", json=payload, headers=ADMIN_HEADERS
    )


def test_upload_participants_returns_credentials(client) -> None:
    response = _upload(client, 6)
    assert response.status_code == 201

    body = response.json()
    assert body["created"] == 6
    assert len(body["credentials"]) == 6

    for cred in body["credentials"]:
        assert len(cred["pin"]) == 4 and cred["pin"].isdigit()
        assert "/participant/" in cred["link"]

    # Los tokens y los PIN deben ser distintos entre participantes.
    tokens = {c["link"] for c in body["credentials"]}
    assert len(tokens) == 6


def test_upload_rejects_duplicate_names(client) -> None:
    payload = {
        "participants": [{"name": "Ana"}, {"name": "ana"}],
        "replace_existing": True,
    }
    response = client.post(
        "/admin/upload_participants", json=payload, headers=ADMIN_HEADERS
    )
    assert response.status_code == 422


# ===========================================================================
# Generación del sorteo
# ===========================================================================
def test_generate_assignments_full_flow(client) -> None:
    _upload(client, 8)

    response = client.post(
        "/admin/generate_assignments",
        json={"seed": "test-seed"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200

    body = response.json()
    assert body["draw"]["seed"] == "test-seed"
    assert body["draw"]["participant_count"] == 8
    assert len(body["assignments"]) == 8
    assert body["validation"]["ok"] is True

    mapping = {a["giver_id"]: a["receiver_id"] for a in body["assignments"]}
    for giver, receiver in mapping.items():
        assert giver != receiver                 # R2
        assert mapping[receiver] != giver        # R3
    assert sorted(mapping.values()) == sorted(mapping.keys())  # R1


def test_generate_requires_three_participants(client) -> None:
    _upload(client, 2)
    response = client.post(
        "/admin/generate_assignments", json={}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 409
    assert "3 participantes" in response.json()["detail"]


def test_regenerate_creates_new_draw_and_deactivates_old(client) -> None:
    _upload(client, 6)
    first = client.post(
        "/admin/generate_assignments", json={"seed": "a"}, headers=ADMIN_HEADERS
    ).json()
    second = client.post(
        "/admin/generate_assignments", json={"seed": "b"}, headers=ADMIN_HEADERS
    ).json()

    assert second["draw"]["id"] != first["draw"]["id"]

    active = client.get("/admin/assignments", headers=ADMIN_HEADERS).json()
    assert active["draw"]["id"] == second["draw"]["id"]
    assert active["draw"]["seed"] == "b"


def test_assignments_endpoint_before_draw_is_404(client) -> None:
    _upload(client, 4)
    assert client.get("/admin/assignments", headers=ADMIN_HEADERS).status_code == 404


def test_validate_endpoint_reports_ok(client) -> None:
    _upload(client, 6)
    client.post("/admin/generate_assignments", json={}, headers=ADMIN_HEADERS)

    response = client.get("/admin/validate", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["checked"] == 6
    assert body["issues"] == []


def test_exclusions_are_honoured_end_to_end(client) -> None:
    _upload(
        client,
        6,
        exclusions=[{"giver": "Ana", "receiver": "Luis", "mutual": True}],
    )
    body = client.post(
        "/admin/generate_assignments", json={"seed": "excl"}, headers=ADMIN_HEADERS
    ).json()

    by_name = {a["giver"]: a["receiver"] for a in body["assignments"]}
    assert by_name["Ana"] != "Luis"
    assert by_name["Luis"] != "Ana"
    assert body["validation"]["ok"] is True


# ===========================================================================
# Flujo del participante
# ===========================================================================
def _token_of(credentials, name: str) -> str:
    link = next(c["link"] for c in credentials if c["name"] == name)
    return link.rsplit("/", 1)[-1]


def test_participant_can_see_their_assignment(client) -> None:
    credentials = _upload(client, 6).json()["credentials"]
    draw = client.post(
        "/admin/generate_assignments", json={"seed": "s"}, headers=ADMIN_HEADERS
    ).json()

    ana = next(c for c in credentials if c["name"] == "Ana")
    token = _token_of(credentials, "Ana")

    # Paso 1: la página pide el PIN.
    page = client.get(f"/participant/{token}")
    assert page.status_code == 200
    assert "Ana" in page.text

    # Paso 2: con el PIN correcto se revela el resultado.
    result = client.post(f"/participant/{token}", data={"pin": ana["pin"]})
    assert result.status_code == 200

    expected = next(a["receiver"] for a in draw["assignments"] if a["giver"] == "Ana")
    assert expected in result.text


def test_participant_api_returns_json(client) -> None:
    credentials = _upload(client, 5).json()["credentials"]
    client.post("/admin/generate_assignments", json={}, headers=ADMIN_HEADERS)

    ana = next(c for c in credentials if c["name"] == "Ana")
    token = _token_of(credentials, "Ana")

    response = client.get(f"/api/v1/participant/{token}", params={"pin": ana["pin"]})
    assert response.status_code == 200
    body = response.json()
    assert body["giver"] == "Ana"
    assert body["receiver"] != "Ana"


def test_participant_wrong_pin_is_rejected(client) -> None:
    credentials = _upload(client, 5).json()["credentials"]
    client.post("/admin/generate_assignments", json={}, headers=ADMIN_HEADERS)

    ana = next(c for c in credentials if c["name"] == "Ana")
    token = _token_of(credentials, "Ana")
    wrong = "0000" if ana["pin"] != "0000" else "1111"

    response = client.get(f"/api/v1/participant/{token}", params={"pin": wrong})
    assert response.status_code == 401


def test_unknown_token_is_404(client) -> None:
    response = client.get("/participant/token-que-no-existe")
    assert response.status_code == 404


def test_participant_locked_after_too_many_attempts(client) -> None:
    credentials = _upload(client, 5).json()["credentials"]
    client.post("/admin/generate_assignments", json={}, headers=ADMIN_HEADERS)

    ana = next(c for c in credentials if c["name"] == "Ana")
    token = _token_of(credentials, "Ana")
    wrong = "0000" if ana["pin"] != "0000" else "1111"

    # MAX_PIN_ATTEMPTS = 5 por defecto.
    for _ in range(5):
        client.get(f"/api/v1/participant/{token}", params={"pin": wrong})

    # Incluso con el PIN correcto, ahora está bloqueado.
    blocked = client.get(f"/api/v1/participant/{token}", params={"pin": ana["pin"]})
    assert blocked.status_code == 401
    assert "intentos" in blocked.json()["detail"].lower()


def test_participant_before_draw_gets_conflict(client) -> None:
    credentials = _upload(client, 5).json()["credentials"]
    ana = next(c for c in credentials if c["name"] == "Ana")
    token = _token_of(credentials, "Ana")

    response = client.get(f"/api/v1/participant/{token}", params={"pin": ana["pin"]})
    assert response.status_code == 409


# ===========================================================================
# Panel HTML
# ===========================================================================
def test_dashboard_redirects_without_session(client) -> None:
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_dashboard_full_html_flow(client) -> None:
    client.post("/admin/login", data={"password": "test-admin-password"})

    upload = client.post(
        "/admin/participants",
        data={
            "participants_raw": "Ana, ana@mail.com\nLuis\nCarla\nDiego",
            "exclusions_raw": "Ana - Luis",
        },
    )
    assert upload.status_code == 200
    assert "4 participantes" in upload.text

    draw = client.post("/admin/draw", data={"seed": "html-seed", "notes": ""})
    assert draw.status_code == 200
    assert "html-seed" in draw.text

    validation = client.post("/admin/validate", data={})
    assert validation.status_code == 200
    assert "correcto" in validation.text

    csv_response = client.get("/admin/credentials.csv")
    assert csv_response.status_code == 200
    assert "nombre,email,pin,link" in csv_response.text


def test_reset_credentials_invalidates_old_link(client) -> None:
    credentials = _upload(client, 5).json()["credentials"]
    client.post("/admin/generate_assignments", json={}, headers=ADMIN_HEADERS)

    ana = next(c for c in credentials if c["name"] == "Ana")
    old_token = _token_of(credentials, "Ana")

    client.post("/admin/login", data={"password": "test-admin-password"})
    reset = client.post(f"/admin/participants/{ana['id']}/reset", data={})
    assert reset.status_code == 200

    # El link viejo ya no existe.
    assert client.get(f"/participant/{old_token}").status_code == 404


# ===========================================================================
# Endpoint de diagnóstico
# ===========================================================================
def test_diagnostics_endpoint_reports_configuration(client) -> None:
    response = client.get("/admin/diagnostics")
    assert response.status_code == 200

    body = response.json()
    assert "version" in body
    assert "base_url_configurada" in body
    assert body["motor_de_base_de_datos"] in {"sqlite", "postgresql"}
    assert "modo" in body["acceso_admin"]


def test_diagnostics_endpoint_needs_no_admin_session(client) -> None:
    """Debe poder consultarse justo cuando no consigues entrar al panel."""
    assert client.get("/admin/diagnostics").status_code == 200


def test_diagnostics_endpoint_does_not_leak_credentials(client) -> None:
    body = client.get("/admin/diagnostics").text
    assert "test-admin-password" not in body
