"""
Servicio de participantes: alta, borrado, exclusiones y credenciales.

Todas las funciones reciben una `Session` de SQLAlchemy y **no** hacen commit
salvo que se indique: quien orquesta (el router) decide la transacción.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from difflib import get_close_matches

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
from app.textutils import clean_text, name_key, name_key_loose


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
    """
    Busca un participante por nombre, tolerando las variaciones típicas de
    quien escribe a mano: mayúsculas, `_` frente a espacios, caracteres
    invisibles y —como último recurso— acentos.
    """
    return _resolve_name(list_participants(db), name)[0]


def _resolve_name(
    participants: Sequence[Participant], name: str
) -> tuple[Participant | None, str | None]:
    """
    Resuelve un nombre contra la lista de participantes.

    Returns:
        `(participante, motivo_del_fallo)`. Si se encuentra, el motivo es None.

    Estrategia en tres pasos, de más estricto a más tolerante:
      1. Coincidencia exacta del texto ya limpio.
      2. Clave normalizada (ignora mayúsculas y `_` frente a espacios).
      3. Clave sin acentos, pero **solo si no es ambigua**: si dos personas
         distintas coinciden al quitar los acentos, se prefiere fallar a
         asignar la exclusión a quien no era.
    """
    if not name or not name.strip():
        return None, "el nombre está vacío"

    cleaned = clean_text(name)

    exact = {p.name: p for p in participants}
    if cleaned in exact:
        return exact[cleaned], None

    key = name_key(cleaned)
    by_key: dict[str, list[Participant]] = {}
    for participant in participants:
        by_key.setdefault(name_key(participant.name), []).append(participant)

    if key in by_key and len(by_key[key]) == 1:
        return by_key[key][0], None

    loose = name_key_loose(cleaned)
    by_loose: dict[str, list[Participant]] = {}
    for participant in participants:
        by_loose.setdefault(name_key_loose(participant.name), []).append(participant)

    candidates = by_loose.get(loose, [])
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        nombres = ", ".join(sorted(p.name for p in candidates))
        return None, (
            f"{name!r} coincide con varias personas si se ignoran los acentos "
            f"({nombres}). Escríbelo igual que en el listado."
        )

    # No se encontró: se construye una pista útil.
    suggestions = get_close_matches(
        key, list(by_key.keys()), n=3, cutoff=0.75
    )
    if suggestions:
        similares = ", ".join(
            sorted({by_key[s][0].name for s in suggestions})
        )
        return None, f"{name!r} no está en el listado. ¿Querías decir: {similares}?"

    return None, f"{name!r} no está en el listado de participantes."


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
    """
    Traduce exclusiones por nombre a filas `(participant_id, excluded_id)`.

    Los errores se acumulan y se informan **todos juntos**: si el listado tiene
    cinco exclusiones mal escritas, es absurdo obligar al organizador a
    descubrirlas de una en una, recargando entre cada intento.
    """
    people = list_participants(db)
    count = 0
    problems: list[str] = []

    for number, rule in enumerate(exclusions, start=1):
        giver, giver_error = _resolve_name(people, rule.giver)
        receiver, receiver_error = _resolve_name(people, rule.receiver)

        for error in (giver_error, receiver_error):
            if error:
                problems.append(f"Exclusión {number}: {error}")

        if giver is None or receiver is None:
            continue
        if giver.id == receiver.id:
            problems.append(
                f"Exclusión {number}: {rule.giver!r} y {rule.receiver!r} son la "
                "misma persona; nadie se regala a sí mismo de todas formas."
            )
            continue

        db.add(Exclusion(participant_id=giver.id, excluded_id=receiver.id))
        count += 1
        if rule.mutual:
            db.add(Exclusion(participant_id=receiver.id, excluded_id=giver.id))
            count += 1

    if problems:
        raise ParticipantServiceError(
            "No se pudieron aplicar estas exclusiones:\n· "
            + "\n· ".join(problems)
        )

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
        # `clean_text` retira aquí los caracteres invisibles (los que inserta
        # WhatsApp, por ejemplo) antes de cualquier otra cosa: si no, se
        # pegarían al nombre y luego nada cuadraría con las exclusiones.
        line = clean_text(line)

        # Las listas copiadas suelen traer coma o punto y coma al final de cada
        # línea. Se quitan para que no generen un email vacío ni un nombre raro.
        line = line.rstrip(",;").strip()

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

    # Duplicados por clave normalizada: "Ana_María" y "ana maría" son la misma
    # persona y tener a las dos rompería el sorteo.
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for participant in participants:
        key = name_key(participant.name)
        if key in seen:
            duplicates.append(f"{seen[key]!r} y {participant.name!r}")
        else:
            seen[key] = participant.name

    if duplicates:
        raise ParticipantServiceError(
            "Hay nombres repetidos (se consideran iguales ignorando mayúsculas "
            "y guiones bajos): " + "; ".join(duplicates)
        )

    return participants


def parse_exclusions_text(raw: str) -> list[ExclusionIn]:
    """
    Parsea exclusiones, una por línea:

        Ana - Luis        (mutua: ni Ana a Luis ni Luis a Ana)
        Ana -> Luis       (solo Ana no le regala a Luis)
    """
    rules: list[ExclusionIn] = []

    for line_number, line in enumerate(raw.splitlines(), start=1):
        # Mismo saneado que en el listado de participantes: sin esto, un
        # carácter invisible haría que el nombre no cuadrara con nadie.
        line = clean_text(line).rstrip(",;").strip()

        if not line or line.startswith("#"):
            continue

        # Se aceptan varios guiones porque los teclados y los correctores
        # sustituyen el guion normal por rayas tipográficas sin avisar.
        if "->" in line or "→" in line:
            separator = "->" if "->" in line else "→"
            left, _, right = line.partition(separator)
            mutual = False
        else:
            separator = next((s for s in ("—", "–", "-") if s in line), None)
            if separator is None:
                raise ParticipantServiceError(
                    f"Línea {line_number}: exclusión mal formada ({line!r}). "
                    "Usa 'Ana - Luis' (mutua) o 'Ana -> Luis' (en un sentido)."
                )
            left, _, right = line.partition(separator)
            mutual = True

        left, right = left.strip(), right.strip()
        if not left or not right:
            raise ParticipantServiceError(
                f"Línea {line_number}: falta un nombre en la exclusión ({line!r})."
            )
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
