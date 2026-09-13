"""
Rutas públicas del participante.

Flujo de autenticación (dos factores ligeros)
---------------------------------------------
    1. El participante abre su link único  ->  /participant/{token}
       `token` = 32 bytes aleatorios: impracticable de adivinar.
    2. Escribe su PIN de 4 dígitos          ->  POST /participant/{token}
       El PIN se verifica contra un hash PBKDF2 y hay bloqueo tras N fallos.

Contra la enumeración: si el token no existe se responde 404 genérico, sin
pistas sobre si el participante existe o no.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Participant
from app.schemas import ParticipantResultOut
from app.security import (
    is_locked,
    lockout_expiry,
    normalize_pin,
    verify_pin,
)
from app.services import draws as draws_service
from app.services import participants as participants_service
from app.templating import render

router = APIRouter(tags=["participante"])


# ===========================================================================
# Helpers
# ===========================================================================
def _extract_token(raw: str) -> str:
    """
    Acepta que el participante pegue el link completo o solo el token.

    'http://host/participant/AbC…'  ->  'AbC…'
    'AbC…'                          ->  'AbC…'
    """
    raw = (raw or "").strip()
    if "/participant/" in raw:
        raw = raw.split("/participant/", 1)[1]
    return raw.split("?", 1)[0].split("#", 1)[0].strip().strip("/")


def _load_participant(db: Session, token: str) -> Participant:
    participant = participants_service.get_by_token(db, _extract_token(token))
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Link no válido. Verifica que lo copiaste completo.",
        )
    return participant


def _check_pin(
    db: Session, participant: Participant, pin: str, settings: Settings
) -> tuple[bool, str | None]:
    """
    Verifica el PIN aplicando la política anti fuerza bruta.

    Returns:
        `(ok, mensaje_de_error)`
    """
    if is_locked(participant.locked_until):
        return False, (
            "Demasiados intentos fallidos. Inténtalo de nuevo en unos minutos."
        )

    pin = normalize_pin(pin)
    if len(pin) != 4:
        return False, "El código debe tener exactamente 4 dígitos."

    if verify_pin(pin, participant.pin_hash, participant.pin_salt):
        participant.failed_attempts = 0
        participant.locked_until = None
        participant.view_count += 1
        if participant.first_viewed_at is None:
            participant.first_viewed_at = datetime.now(timezone.utc)
        db.commit()
        return True, None

    participant.failed_attempts += 1
    remaining = settings.max_pin_attempts - participant.failed_attempts
    if remaining <= 0:
        participant.locked_until = lockout_expiry(settings)
        participant.failed_attempts = 0
        db.commit()
        return False, (
            f"Código incorrecto. Cuenta bloqueada {settings.pin_lockout_minutes} "
            "minutos por seguridad."
        )

    db.commit()
    return False, f"Código incorrecto. Te quedan {remaining} intento(s)."


# ===========================================================================
# Vistas HTML
# ===========================================================================
@router.get("/", include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db)):
    """Portada: explica el juego y permite pegar el link personal."""
    draw = draws_service.get_active_draw(db)
    total = len(participants_service.list_participants(db))
    return render(
        request,
        "index.html",
        {"has_draw": draw is not None, "total_participants": total},
    )


@router.post("/lookup", include_in_schema=False)
def lookup(token: str = Form(...)):
    """Redirige al link personal a partir de lo que el usuario pegó."""
    clean = _extract_token(token)
    if not clean:
        return RedirectResponse(url="/?error=1", status_code=303)
    return RedirectResponse(url=f"/participant/{clean}", status_code=303)


@router.get("/participant/{token}", include_in_schema=False)
def participant_form(
    token: str, request: Request, db: Session = Depends(get_db)
):
    """Paso 2: formulario para introducir el PIN de 4 dígitos."""
    participant = _load_participant(db, token)
    draw = draws_service.get_active_draw(db)
    return render(
        request,
        "participant_login.html",
        {
            "participant": participant,
            "token": participant.access_token,
            "has_draw": draw is not None,
            "error": None,
        },
    )


@router.post("/participant/{token}", include_in_schema=False)
def participant_result(
    token: str,
    request: Request,
    pin: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Paso 3: valida el PIN y muestra a quién le toca regalar."""
    participant = _load_participant(db, token)
    draw = draws_service.get_active_draw(db)

    if draw is None:
        return render(
            request,
            "participant_login.html",
            {
                "participant": participant,
                "token": participant.access_token,
                "has_draw": False,
                "error": "El sorteo todavía no se ha realizado. Vuelve más tarde.",
            },
        )

    ok, error = _check_pin(db, participant, pin, settings)
    if not ok:
        return render(
            request,
            "participant_login.html",
            {
                "participant": participant,
                "token": participant.access_token,
                "has_draw": True,
                "error": error,
            },
        )

    assignment = draws_service.get_assignment_for(db, draw, participant)
    if assignment is None:
        return render(
            request,
            "participant_login.html",
            {
                "participant": participant,
                "token": participant.access_token,
                "has_draw": True,
                "error": (
                    "No hay asignación para ti en el sorteo actual. "
                    "Avisa al organizador."
                ),
            },
        )

    return render(
        request,
        "participant_result.html",
        {
            "participant": participant,
            "receiver": assignment.receiver,
            "draw": draw,
        },
    )


# ===========================================================================
# API JSON
# ===========================================================================
@router.get(
    "/api/v1/participant/{token}",
    response_model=ParticipantResultOut,
    summary="Consultar la asignación de un participante",
    tags=["API participante"],
)
def api_participant_result(
    token: str,
    pin: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ParticipantResultOut:
    """
    Equivalente JSON de la vista del participante.

    Ejemplo:
        GET /api/v1/participant/{token}?pin=1234
    """
    participant = _load_participant(db, token)
    draw = draws_service.get_active_draw(db)
    if draw is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El sorteo aún no se ha realizado.",
        )

    ok, error = _check_pin(db, participant, pin, settings)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error or "Código incorrecto.",
        )

    assignment = draws_service.get_assignment_for(db, draw, participant)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay asignación para este participante.",
        )

    return ParticipantResultOut(
        giver=participant.name,
        receiver=assignment.receiver.name,
        draw_id=draw.id,
    )
