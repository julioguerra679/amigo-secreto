"""
Servicio de participantes: alta, borrado, exclusiones y credenciales.

Todas las funciones reciben una `Session` de SQLAlchemy y **no** hacen commit
salvo que se indique: quien orquesta (el router) decide la transacción.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Assignment, Draw, Exclusion, Participant
from app.schemas import (
    ExclusionIn,
    ParticipantCredentialsOut,
    ParticipantIn,
)
from app.security import generate_access_token, generate_pin, hash_pin


class ParticipantServiceError(RuntimeError):
    """Error de negocio al gestionar participantes."""


# ===========================================================================
# Consultas
# ===========================================================================
def list_participants(db: Session) -> list[Participant]:
    """Todos los participantes ordenados por id (orden canónico y estable)."""
    return list(db.scalars(select(Participant).order_by(Participant.id)))


def get_by_token(db: Session, token: str) -> Participant | None:
    """Busca un participante por su token de acceso (el link único)."""
    if not token:
        return None
    return db.scalar(select(Participant).where(Participant.access_token == token))


def get_by_name(db: Session, name: str) -> Participant | None:
    return db.scalar(select(Participant).where(Participant.name == name))


def exclusion_map(db: Session) -> dict[int, set[int]]:
    """
    Exclusiones en el formato que espera el algoritmo:
    `{giver_id: {receiver_ids prohibidos}}`.
    """
    result: dict[int, set[int]] = {}
    for exclusion in db.scalars(select(Exclusion)):
        result.setdefault(exclusion.participant_id, set()).add(exclusion.excluded_id)
    return result


# ===========================================================================
# Altas
# ===========================================================================
def replace_participants(
    db: Session,
    participants: Sequence[ParticipantIn],
    exclusions: Iterable[ExclusionIn],
    settings: Settings,
    replace_existing: bool = True,
) -> tuple[list[ParticipantCredentialsOut], int]:
    """
    Carga el listado de participantes y devuelve `(credenciales, nº exclusiones)`.

    Importante: el PIN en claro solo se devuelve **aquí**, una vez. Después
    queda hasheado en la base de datos y es irrecuperable (solo regenerable).

    Si `replace_existing` es True se borra todo lo anterior (participantes,
    exclusiones y sorteos), porque cambiar el padrón invalida cualquier
    sorteo previo.
    """
    if replace_existing:
        # El orden importa: primero lo que referencia, luego lo referenciado.
        db.execute(delete(Assignment))
        db.execute(delete(Draw))
        db.execute(delete(Exclusion))
        db.execute(delete(Participant))
        db.flush()
    else:
        existing = {p.name.casefold() for p in list_participants(db)}
        collisions = [p.name for p in participants if p.name.casefold() in existing]
        if collisions:
            raise ParticipantServiceError(
                "Ya existen participantes con estos nombres: "
                + ", ".join(sorted(collisions))
            )

    credentials: list[ParticipantCredentialsOut] = []
    created: list[Participant] = []

    for item in participants:
        pin = generate_pin()
        pin_hash, pin_salt = hash_pin(pin)
        participant = Participant(
            name=item.name,
            email=str(item.email) if item.email else None,
            pin_hash=pin_hash,
            pin_salt=pin_salt,
            access_token=generate_access_token(),
        )
        db.add(participant)
        created.append(participant)
        # El PIN en claro se conserva en memoria solo para esta respuesta.
        credentials.append(
            ParticipantCredentialsOut(
                id=0, name=item.name, email=participant.email, pin=pin, link=""
            )
        )

    db.flush()  # asigna los ids autoincrementales

    # Completa ids y links ahora que la BD asignó las claves primarias.
    for participant, cred in zip(created, credentials, strict=True):
        cred.id = participant.id
        cred.link = settings.participant_link(participant.access_token)

    n_exclusions = _apply_exclusions(db, exclusions)
    db.flush()

    return credentials, n_exclusions


def _apply_exclusions(db: Session, exclusions: Iterable[ExclusionIn]) -> int:
    """Traduce exclusiones por nombre a filas `(participant_id, excluded_id)`."""
    count = 0
    for rule in exclusions:
        giver = get_by_name(db, rule.giver)
        receiver = get_by_name(db, rule.receiver)
        if giver is None or receiver is None:
            raise ParticipantServiceError(
                f"Exclusión inválida: no existe {rule.giver!r} o {rule.receiver!r}."
            )
        if giver.id == receiver.id:
            continue  # la autoexclusión ya es implícita
        db.add(Exclusion(participant_id=giver.id, excluded_id=receiver.id))
        count += 1
        if rule.mutual:
            db.add(Exclusion(participant_id=receiver.id, excluded_id=giver.id))
            count += 1
    return count


def regenerate_credentials(
    db: Session, participant: Participant, settings: Settings
) -> ParticipantCredentialsOut:
    """
    Emite un PIN y un token nuevos para un participante (p. ej. si perdió el
    correo o sospecha que alguien vio su link). Invalida el link anterior.
    """
    pin = generate_pin()
    participant.pin_hash, participant.pin_salt = hash_pin(pin)
    participant.access_token = generate_access_token()
    participant.failed_attempts = 0
    participant.locked_until = None
    db.flush()
    return ParticipantCredentialsOut(
        id=participant.id,
        name=participant.name,
        email=participant.email,
        pin=pin,
        link=settings.participant_link(participant.access_token),
    )


# ===========================================================================
# Importación / exportación
# ===========================================================================
def parse_participants_text(raw: str) -> list[ParticipantIn]:
    """
    Parsea el textarea del panel de admin.

    Formatos aceptados, una persona por línea:
        Ana
        Ana, ana@mail.com
        Ana;ana@mail.com
        Ana <ana@mail.com>

    Las líneas vacías y las que empiezan por `#` se ignoran.
    """
    participants: list[ParticipantIn] = []

    for line_number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        name, email = line, None

        if "<" in line and ">" in line:
            name, _, rest = line.partition("<")
            email = rest.partition(">")[0].strip() or None
        else:
            for separator in (",", ";", "\t", "|"):
                if separator in line:
                    name, _, rest = line.partition(separator)
                    email = rest.strip() or None
                    break

        name = name.strip()
        if not name:
            raise ParticipantServiceError(
                f"Línea {line_number}: falta el nombre ({line!r})."
            )

        try:
            participants.append(ParticipantIn(name=name, email=email))
        except Exception as exc:  # pydantic ValidationError
            raise ParticipantServiceError(
                f"Línea {line_number}: {name!r} tiene un email inválido ({email!r})."
            ) from exc

    if not participants:
        raise ParticipantServiceError("No se encontró ningún participante válido.")

    names = [p.name.casefold() for p in participants]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ParticipantServiceError(
            "Hay nombres repetidos: " + ", ".join(sorted(duplicates))
        )

    return participants


def parse_exclusions_text(raw: str) -> list[ExclusionIn]:
    """
    Parsea exclusiones, una por línea:

        Ana - Luis        (mutua: ni Ana a Luis ni Luis a Ana)
        Ana -> Luis       (solo Ana no le regala a Luis)
    """
    rules: list[ExclusionIn] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            left, _, right = line.partition("->")
            mutual = False
        elif "-" in line:
            left, _, right = line.partition("-")
            mutual = True
        else:
            raise ParticipantServiceError(
                f"Exclusión mal formada: {line!r}. Usa 'Ana - Luis' o 'Ana -> Luis'."
            )
        left, right = left.strip(), right.strip()
        if not left or not right:
            raise ParticipantServiceError(f"Exclusión incompleta: {line!r}.")
        rules.append(ExclusionIn(giver=left, receiver=right, mutual=mutual))
    return rules


def credentials_to_csv(credentials: Sequence[ParticipantCredentialsOut]) -> str:
    """Exporta las credenciales a CSV para que el admin las reparta."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["nombre", "email", "pin", "link"])
    for cred in credentials:
        writer.writerow([cred.name, cred.email or "", cred.pin, cred.link])
    return buffer.getvalue()
