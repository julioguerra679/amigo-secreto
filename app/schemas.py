"""
Esquemas Pydantic: contrato de entrada/salida de la API JSON.

Separar los modelos ORM (`app/models.py`) de los esquemas de API evita
filtrar campos sensibles (`pin_hash`, `pin_salt`) por accidente.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.textutils import clean_text, name_key


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
class ParticipantIn(BaseModel):
    """Un participante tal como lo envía el admin al cargarlo."""

    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        # `clean_text` quita los caracteres invisibles que arrastran las listas
        # copiadas de WhatsApp o del correo, y unifica acentos y espacios.
        v = clean_text(v)
        if not v:
            raise ValueError("El nombre no puede estar vacío")
        return v


class ExclusionIn(BaseModel):
    """Prohíbe que `giver` le regale a `receiver` (por nombre)."""

    giver: str
    receiver: str
    mutual: bool = True  # por defecto la prohibición va en ambos sentidos


class UploadParticipantsIn(BaseModel):
    """Cuerpo de `POST /admin/upload_participants`."""

    participants: list[ParticipantIn] = Field(..., min_length=1)
    exclusions: list[ExclusionIn] = Field(default_factory=list)
    replace_existing: bool = True

    @field_validator("participants")
    @classmethod
    def _unique_names(cls, v: list[ParticipantIn]) -> list[ParticipantIn]:
        seen: set[str] = set()
        for p in v:
            # Se compara por clave normalizada: "Ana_María" y "ana maría"
            # son la misma persona y deben rechazarse como duplicado.
            key = name_key(p.name)
            if key in seen:
                raise ValueError(f"Nombre duplicado en la carga: {p.name!r}")
            seen.add(key)
        return v


class GenerateAssignmentsIn(BaseModel):
    """Cuerpo de `POST /admin/generate_assignments`."""

    # Semilla explícita -> sorteo reproducible. Si es None se genera una
    # aleatoria y se guarda, así el sorteo sigue siendo auditable.
    seed: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------
class ParticipantOut(BaseModel):
    """Vista pública de un participante (sin material criptográfico)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
    created_at: datetime


class ParticipantCredentialsOut(BaseModel):
    """
    Credenciales que el admin debe repartir. El PIN en claro solo existe
    en esta respuesta, en el momento de la creación: después ya no se puede
    recuperar (solo regenerar), porque en la BD está hasheado.
    """

    id: int
    name: str
    email: str | None
    pin: str
    link: str


class UploadParticipantsOut(BaseModel):
    created: int
    exclusions: int
    credentials: list[ParticipantCredentialsOut]


class AssignmentOut(BaseModel):
    giver_id: int
    giver: str
    receiver_id: int
    receiver: str


class DrawOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seed: str
    algorithm: str
    participant_count: int
    is_active: bool
    created_at: datetime
    notes: str | None = None


class ValidationIssue(BaseModel):
    code: str
    message: str
    detail: dict | None = None


class ValidationReport(BaseModel):
    """Resultado de auditar un sorteo. `ok=True` ⇒ sorteo válido."""

    ok: bool
    checked: int
    issues: list[ValidationIssue] = Field(default_factory=list)


class GenerateAssignmentsOut(BaseModel):
    draw: DrawOut
    assignments: list[AssignmentOut]
    validation: ValidationReport
    attempts: int


class ParticipantResultOut(BaseModel):
    """Lo que ve el participante tras autenticarse."""

    giver: str
    receiver: str
    draw_id: int
