"""
Servicio de sorteos: orquesta el algoritmo puro con la persistencia.

Regla de oro para la reproducibilidad
-------------------------------------
El algoritmo depende del **orden** de la lista de participantes que recibe
(el generador aleatorio consume decisiones en ese orden). Por eso aquí SIEMPRE
se usa el orden canónico: ids ascendentes (`_canonical_ids`). Si no se
respetara, re-ejecutar con la misma semilla podría dar otro resultado y la
verificación fallaría sin motivo.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Draw, Participant
from app.schemas import (
    AssignmentOut,
    ValidationIssue,
    ValidationReport,
)
from app.services import participants as participants_service
from app.services.assignment import (
    ALGORITHM_NAME,
    AssignmentResult,
    generate_assignments,
    new_seed,
    validate_assignments,
)


class DrawServiceError(RuntimeError):
    """Error de negocio al gestionar sorteos."""


# ===========================================================================
# Consultas
# ===========================================================================
def get_active_draw(db: Session) -> Draw | None:
    """El sorteo vigente, o `None` si aún no se ha hecho ninguno."""
    return db.scalar(
        select(Draw).where(Draw.is_active.is_(True)).order_by(Draw.id.desc())
    )


def get_draw_history(db: Session) -> list[Draw]:
    """Todos los sorteos, del más reciente al más antiguo."""
    return list(db.scalars(select(Draw).order_by(Draw.id.desc())))


def get_assignments(db: Session, draw: Draw) -> list[Assignment]:
    return list(
        db.scalars(
            select(Assignment)
            .where(Assignment.draw_id == draw.id)
            .order_by(Assignment.id)
        )
    )


def get_assignment_for(
    db: Session, draw: Draw, participant: Participant
) -> Assignment | None:
    """La asignación de un participante concreto dentro de un sorteo."""
    return db.scalar(
        select(Assignment).where(
            Assignment.draw_id == draw.id,
            Assignment.giver_id == participant.id,
        )
    )


def _canonical_ids(db: Session) -> list[int]:
    """Ids de participantes en orden canónico (ascendente)."""
    return [p.id for p in participants_service.list_participants(db)]


# ===========================================================================
# Generación
# ===========================================================================
def create_draw(
    db: Session,
    seed: str | None = None,
    notes: str | None = None,
) -> tuple[Draw, AssignmentResult]:
    """
    Ejecuta el sorteo y lo persiste, desactivando el anterior.

    No borra el histórico: cada regeneración queda registrada como un `Draw`
    nuevo con `is_active=False` para los antiguos. Eso permite auditar cuántas
    veces se regeneró y con qué semilla.
    """
    ids = _canonical_ids(db)
    if len(ids) < 3:
        raise DrawServiceError(
            "Se necesitan al menos 3 participantes para sortear sin regalos "
            f"cruzados. Actualmente hay {len(ids)}."
        )

    exclusions = participants_service.exclusion_map(db)
    result = generate_assignments(ids, exclusions, seed=seed or new_seed())

    # Cinturón y tirantes: auditamos lo que acabamos de generar antes de
    # guardarlo. Si el algoritmo tuviera un bug, no llega a la base de datos.
    report = validate_assignments(result.pairs, ids, exclusions)
    if not report.ok:
        raise DrawServiceError(
            "El sorteo generado no pasó la validación interna: "
            + "; ".join(i.message for i in report.issues)
        )

    # Desactiva los sorteos anteriores.
    for previous in db.scalars(select(Draw).where(Draw.is_active.is_(True))):
        previous.is_active = False

    draw = Draw(
        seed=result.seed,
        algorithm=result.algorithm or ALGORITHM_NAME,
        participant_count=len(ids),
        is_active=True,
        notes=notes,
    )
    db.add(draw)
    db.flush()

    for giver_id, receiver_id in result.pairs:
        db.add(
            Assignment(draw_id=draw.id, giver_id=giver_id, receiver_id=receiver_id)
        )
    db.flush()

    return draw, result


# ===========================================================================
# Validación / auditoría
# ===========================================================================
def validate_draw(db: Session, draw: Draw) -> ValidationReport:
    """
    Audita un sorteo **leyendo la base de datos**, no la memoria.

    Además de las reglas R1–R4 comprueba la *reproducibilidad*: re-ejecuta el
    algoritmo con la semilla guardada y compara. Si no coincide, alguien tocó
    la tabla `assignments` a mano (o cambió el padrón después del sorteo).
    """
    assignments = get_assignments(db, draw)
    pairs = [(a.giver_id, a.receiver_id) for a in assignments]
    ids = _canonical_ids(db)
    exclusions = participants_service.exclusion_map(db)

    result = validate_assignments(pairs, ids, exclusions)
    issues = [
        ValidationIssue(code=i.code, message=i.message, detail=i.detail or None)
        for i in result.issues
    ]

    # --- Comprobación extra: reproducibilidad ------------------------------
    if draw.participant_count == len(ids):
        try:
            expected = set(
                generate_assignments(ids, exclusions, seed=draw.seed).pairs
            )
            if expected != set(pairs):
                issues.append(
                    ValidationIssue(
                        code="not_reproducible",
                        message=(
                            "Las asignaciones guardadas no coinciden con las que "
                            f"produce la semilla {draw.seed!r}. Es posible que se "
                            "hayan modificado manualmente."
                        ),
                    )
                )
        except Exception as exc:  # pragma: no cover - defensivo
            issues.append(
                ValidationIssue(
                    code="reproduction_failed",
                    message=f"No se pudo reproducir el sorteo: {exc}",
                )
            )
    else:
        issues.append(
            ValidationIssue(
                code="participants_changed",
                message=(
                    f"El sorteo se hizo con {draw.participant_count} participantes "
                    f"y ahora hay {len(ids)}. Debes regenerar el sorteo."
                ),
            )
        )

    return ValidationReport(ok=not issues, checked=len(pairs), issues=issues)


# ===========================================================================
# Presentación
# ===========================================================================
def assignments_to_out(assignments: list[Assignment]) -> list[AssignmentOut]:
    return [
        AssignmentOut(
            giver_id=a.giver_id,
            giver=a.giver.name,
            receiver_id=a.receiver_id,
            receiver=a.receiver.name,
        )
        for a in assignments
    ]
