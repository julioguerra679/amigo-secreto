"""
Tests del algoritmo de asignación (módulo puro, sin base de datos).

Estos tests son el contrato del corazón de la aplicación: si alguno falla,
el sorteo no es fiable.
"""

from __future__ import annotations

import pytest

from app.services.assignment import (
    NotEnoughParticipants,
    NoValidAssignment,
    generate_assignments,
    reproduce,
    validate_assignments,
)


# ===========================================================================
# Propiedades fundamentales (R1–R3)
# ===========================================================================
@pytest.mark.parametrize("n", [3, 4, 5, 7, 10, 25, 60])
def test_no_self_assignment(n: int) -> None:
    """R2: nadie se regala a sí mismo, para cualquier tamaño de grupo."""
    ids = list(range(1, n + 1))
    result = generate_assignments(ids)
    assert all(giver != receiver for giver, receiver in result.pairs)


@pytest.mark.parametrize("n", [3, 4, 5, 7, 10, 25, 60])
def test_no_reciprocal_pairs(n: int) -> None:
    """R3: no existen regalos cruzados A→B y B→A."""
    ids = list(range(1, n + 1))
    mapping = dict(generate_assignments(ids).pairs)
    for giver, receiver in mapping.items():
        assert mapping[receiver] != giver, (
            f"Regalo cruzado entre {giver} y {receiver}"
        )


@pytest.mark.parametrize("n", [3, 4, 5, 7, 10, 25, 60])
def test_is_a_bijection(n: int) -> None:
    """R1: cada persona regala exactamente una vez y recibe exactamente una."""
    ids = list(range(1, n + 1))
    pairs = generate_assignments(ids).pairs
    givers = [g for g, _ in pairs]
    receivers = [r for _, r in pairs]
    assert sorted(givers) == ids
    assert sorted(receivers) == ids


@pytest.mark.parametrize("n", [3, 6, 12])
def test_result_is_a_single_cycle(n: int) -> None:
    """La cadena de regalos conecta a todo el grupo en un único círculo."""
    ids = list(range(1, n + 1))
    result = generate_assignments(ids)
    mapping = dict(result.pairs)

    visited = 1
    start = ids[0]
    current = mapping[start]
    while current != start:
        current = mapping[current]
        visited += 1
    assert visited == n


def test_stress_many_draws_all_valid() -> None:
    """500 sorteos seguidos: todos deben pasar el validador independiente."""
    ids = list(range(1, 9))
    for _ in range(500):
        result = generate_assignments(ids)
        report = validate_assignments(result.pairs, ids)
        assert report.ok, [i.message for i in report.issues]


# ===========================================================================
# Casos límite
# ===========================================================================
@pytest.mark.parametrize("n", [0, 1, 2])
def test_fewer_than_three_participants_is_rejected(n: int) -> None:
    """Con 2 personas el regalo cruzado es inevitable: debe fallar explícito."""
    with pytest.raises(NotEnoughParticipants):
        generate_assignments(list(range(n)))


def test_duplicate_ids_are_rejected() -> None:
    from app.services.assignment import AssignmentError

    with pytest.raises(AssignmentError):
        generate_assignments([1, 2, 3, 3])


# ===========================================================================
# Reproducibilidad (R: auditoría)
# ===========================================================================
def test_same_seed_produces_same_result() -> None:
    ids = list(range(1, 11))
    a = generate_assignments(ids, seed="navidad-2026")
    b = generate_assignments(ids, seed="navidad-2026")
    assert a.pairs == b.pairs
    assert a.cycle == b.cycle


def test_different_seeds_usually_differ() -> None:
    """Con 10 personas hay 9! = 362 880 ciclos: 20 semillas no deben coincidir."""
    ids = list(range(1, 11))
    results = {generate_assignments(ids, seed=str(s)).pairs for s in range(20)}
    assert len(results) > 15


def test_reproduce_matches_generation() -> None:
    ids = list(range(1, 8))
    original = generate_assignments(ids, seed="abc123")
    assert reproduce(ids, "abc123") == original.pairs


# ===========================================================================
# Exclusiones (R4)
# ===========================================================================
def test_exclusions_are_respected() -> None:
    """Ana (1) y Luis (2) son pareja: no pueden regalarse entre ellos."""
    ids = [1, 2, 3, 4, 5, 6]
    exclusions = {1: {2}, 2: {1}}

    for seed in range(50):  # varias semillas: la restricción siempre se cumple
        result = generate_assignments(ids, exclusions, seed=str(seed))
        mapping = dict(result.pairs)
        assert mapping[1] != 2
        assert mapping[2] != 1
        assert validate_assignments(result.pairs, ids, exclusions).ok


def test_multiple_exclusions_still_solvable() -> None:
    ids = list(range(1, 9))
    exclusions = {1: {2, 3}, 2: {1, 4}, 5: {6}, 6: {5, 7}}
    result = generate_assignments(ids, exclusions, seed="x")
    assert validate_assignments(result.pairs, ids, exclusions).ok


def test_impossible_exclusions_raise() -> None:
    """Si alguien excluye a todos los demás, debe fallar con mensaje claro."""
    ids = [1, 2, 3, 4]
    exclusions = {1: {2, 3, 4}}
    with pytest.raises(NoValidAssignment):
        generate_assignments(ids, exclusions)


def test_nobody_can_give_to_target_raises() -> None:
    """Si nadie puede regalarle a X, tampoco hay solución."""
    ids = [1, 2, 3, 4]
    exclusions = {1: {4}, 2: {4}, 3: {4}}
    with pytest.raises(NoValidAssignment):
        generate_assignments(ids, exclusions)


# ===========================================================================
# El validador como oráculo independiente
# ===========================================================================
def test_validator_detects_self_gift() -> None:
    report = validate_assignments([(1, 1), (2, 3), (3, 2)], [1, 2, 3])
    assert not report.ok
    assert any(i.code == "self_gift" for i in report.issues)


def test_validator_detects_reciprocal_pair() -> None:
    # 1↔2 cruzado y 3→4→3 también; el validador debe verlos.
    report = validate_assignments([(1, 2), (2, 1), (3, 4), (4, 3)], [1, 2, 3, 4])
    assert not report.ok
    codes = [i.code for i in report.issues]
    assert codes.count("reciprocal_pair") == 2


def test_validator_detects_duplicate_receiver() -> None:
    report = validate_assignments([(1, 3), (2, 3), (3, 1)], [1, 2, 3])
    assert not report.ok
    assert any(i.code == "duplicate_receiver" for i in report.issues)


def test_validator_detects_excluded_pair() -> None:
    report = validate_assignments(
        [(1, 2), (2, 3), (3, 1)], [1, 2, 3], exclusions={1: {2}}
    )
    assert not report.ok
    assert any(i.code == "excluded_pair" for i in report.issues)


def test_validator_detects_split_into_subgroups() -> None:
    """Dos ciclos de 3: válido como desarreglo, pero fragmenta al grupo."""
    pairs = [(1, 2), (2, 3), (3, 1), (4, 5), (5, 6), (6, 4)]
    report = validate_assignments(pairs, [1, 2, 3, 4, 5, 6])
    assert not report.ok
    assert any(i.code == "not_single_cycle" for i in report.issues)


def test_validator_accepts_a_correct_draw() -> None:
    report = validate_assignments([(1, 2), (2, 3), (3, 1)], [1, 2, 3])
    assert report.ok
    assert report.checked == 3
    assert report.issues == []
