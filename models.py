"""
Modelos de datos (ORM SQLAlchemy 2.0).

Esquema relacional
------------------
    participants            exclusions              draws
    ┌──────────────┐        ┌────────────────┐      ┌──────────────┐
    │ id (PK)      │◄───────┤ participant_id │      │ id (PK)      │
    │ name         │◄───────┤ excluded_id    │      │ seed         │
    │ email        │        └────────────────┘      │ algorithm    │
    │ pin_hash     │                                │ is_active    │
    │ pin_salt     │        assignments             │ created_at   │
    │ access_token │        ┌──────────────┐        └──────────────┘
    │ …            │◄───────┤ giver_id     │              ▲
    └──────────────┘◄───────┤ receiver_id  │              │
                            │ draw_id      ├──────────────┘
                            └──────────────┘

Decisiones de diseño
--------------------
* El PIN **nunca** se guarda en claro: se almacena `PBKDF2-HMAC-SHA256`
  con salt individual por participante (ver `app/security.py`).
* `access_token` es un secreto largo (32 bytes URL-safe) que forma el link
  único. Junto con el PIN de 4 dígitos da dos factores: "algo que recibes"
  (el link) + "algo que sabes" (el PIN).
* Un `Draw` es una *versión* del sorteo. Regenerar no borra el historial:
  desactiva el anterior (`is_active = False`) y crea uno nuevo. Esto hace el
  sistema auditable.
* `exclusions` permite prohibir pares concretos (p. ej. una pareja que no
  quiere regalarse entre sí). Es opcional.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """`datetime` actual en UTC y *aware* (con zona horaria)."""
    return datetime.now(timezone.utc)


class Participant(Base):
    """Una persona inscrita en el juego."""

    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)

    # --- Credenciales -----------------------------------------------------
    pin_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    pin_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    access_token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # --- Anti fuerza bruta -------------------------------------------------
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- Trazabilidad ------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    first_viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Relaciones --------------------------------------------------------
    exclusions: Mapped[list[Exclusion]] = relationship(
        "Exclusion",
        foreign_keys="Exclusion.participant_id",
        back_populates="participant",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - solo debug
        return f"<Participant id={self.id} name={self.name!r}>"


class Exclusion(Base):
    """
    Restricción "A NO puede regalarle a B".

    Se aplica en una sola dirección; el servicio la registra en ambos
    sentidos cuando el admin pide una exclusión mutua.
    """

    __tablename__ = "exclusions"
    __table_args__ = (
        UniqueConstraint("participant_id", "excluded_id", name="uq_exclusion_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    excluded_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )

    participant: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[participant_id], back_populates="exclusions"
    )
    excluded: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[excluded_id]
    )


class Draw(Base):
    """
    Una ejecución del sorteo.

    Guardar la `seed` hace el sorteo **reproducible**: con la misma semilla,
    la misma lista de participantes y las mismas exclusiones, el algoritmo
    devuelve exactamente la misma asignación. Es la base de la verificación.
    """

    __tablename__ = "draws"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(
        String(64), nullable=False, default="single-hamiltonian-cycle-v1"
    )
    participant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    assignments: Mapped[list[Assignment]] = relationship(
        "Assignment",
        back_populates="draw",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - solo debug
        return f"<Draw id={self.id} active={self.is_active} seed={self.seed!r}>"


class Assignment(Base):
    """Arista del sorteo: `giver` le regala a `receiver`."""

    __tablename__ = "assignments"
    __table_args__ = (
        # Dentro de un sorteo, cada persona da exactamente una vez…
        UniqueConstraint("draw_id", "giver_id", name="uq_assignment_giver"),
        # …y recibe exactamente una vez. La BD refuerza la invariante.
        UniqueConstraint("draw_id", "receiver_id", name="uq_assignment_receiver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draw_id: Mapped[int] = mapped_column(
        ForeignKey("draws.id", ondelete="CASCADE"), nullable=False, index=True
    )
    giver_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    receiver_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )

    draw: Mapped[Draw] = relationship("Draw", back_populates="assignments")
    giver: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[giver_id]
    )
    receiver: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[receiver_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - solo debug
        return f"<Assignment {self.giver_id} -> {self.receiver_id}>"
