"""
==============================================================================
 ALGORITMO DE ASIGNACIÓN DEL AMIGO SECRETO
==============================================================================

Este módulo es **puro**: no sabe nada de FastAPI, de HTTP ni del ORM. Recibe
identificadores y devuelve identificadores. Eso lo hace trivial de testear y
de reutilizar.

------------------------------------------------------------------------------
1. EL PROBLEMA
------------------------------------------------------------------------------
Dado un conjunto P de n participantes, buscamos una función f: P → P tal que:

    (R1) f es una biyección          → cada persona regala 1 vez y recibe 1 vez
    (R2) f(x) ≠ x   ∀x              → nadie se regala a sí mismo
    (R3) f(f(x)) ≠ x ∀x             → no hay parejas recíprocas (A→B y B→A)
    (R4) f(x) ∉ Excl(x)             → se respetan las exclusiones del admin

(R1)+(R2) es lo que en combinatoria se llama un **desarreglo**
(*derangement*). (R3) añade la condición de que la permutación no tenga
ciclos de longitud 2.

------------------------------------------------------------------------------
2. POR QUÉ *NO* USAMOS "BARAJAR Y REINTENTAR"
------------------------------------------------------------------------------
La solución ingenua es: barajar la lista, emparejar, y si algo viola una regla
volver a barajar. Problemas:

  * La probabilidad de que una permutación aleatoria sea un desarreglo tiende
    a 1/e ≈ 36.8 %. Añadiendo (R3) y exclusiones, la tasa de éxito cae y el
    número de reintentos se dispara.
  * Con exclusiones densas el bucle puede no terminar nunca: no hay cota.
  * El sesgo del muestreo es difícil de razonar.

------------------------------------------------------------------------------
3. NUESTRO ENFOQUE: UN ÚNICO CICLO HAMILTONIANO
------------------------------------------------------------------------------
Idea clave: si ordenamos a los n participantes en **un solo ciclo**

        p₀ → p₁ → p₂ → … → p_{n-1} → p₀

y definimos f(p_i) = p_{(i+1) mod n}, entonces:

  * (R1) se cumple por construcción: recorrer un ciclo que visita todos los
         nodos exactamente una vez es una biyección.
  * (R2) se cumple si n ≥ 2: el sucesor de p_i nunca es p_i.
  * (R3) se cumple si **n ≥ 3**: en un ciclo de longitud n, f(f(p_i)) = p_{i+2},
         y p_{i+2} = p_i exigiría n | 2, es decir n ≤ 2.

  ⇒ Las tres primeras reglas dejan de ser condiciones que hay que *comprobar*
    y pasan a ser propiedades *estructurales*. Es imposible generar una
    asignación inválida respecto de R1–R3.

Solo queda (R4), las exclusiones. Construimos el ciclo con **backtracking
aleatorizado**: en cada paso elegimos el siguiente nodo al azar entre los
candidatos permitidos; si llegamos a un callejón sin salida, deshacemos el
último paso y probamos otra rama. Esto es una búsqueda de ciclo hamiltoniano
en el grafo dirigido de "regalos permitidos".

    G = (V, E)   con   V = participantes
                       E = {(a, b) : a ≠ b  y  b ∉ Excl(a)}

Complejidad
-----------
  * Sin exclusiones: O(n) efectivo — la primera rama siempre funciona, así que
    equivale a una única barajada de Fisher-Yates. Sin reintentos.
  * Con exclusiones: el peor caso teórico es exponencial (Hamiltonian Cycle es
    NP-completo), pero el grafo de un amigo secreto real es densísimo
    (cada nodo tiene n-1-|Excl| salidas), así que en la práctica se resuelve
    de inmediato. Aun así acotamos el trabajo con `max_operations` y fallamos
    con un error claro en vez de colgarnos.

Aleatoriedad y reproducibilidad
-------------------------------
Usamos `random.Random(seed)`, una instancia *propia* de Mersenne Twister:
  * No toca el estado global de `random`, así que no interfiere con nada más.
  * Con la misma semilla + mismos participantes + mismas exclusiones, el
    resultado es **idéntico**. Guardamos la semilla en la tabla `draws`, de
    modo que cualquiera puede re-ejecutar y verificar el sorteo a posteriori.
  * Para la semilla aleatoria usamos `secrets`, no `random`.

Sesgo estadístico (honestidad intelectual)
------------------------------------------
Este método muestrea uniformemente entre los (n-1)! ciclos hamiltonianos
posibles, no entre *todos* los desarreglos sin 2-ciclos. Es una restricción
deliberada y deseable: un único ciclo garantiza que la cadena de regalos
conecta a todo el grupo (no se parte en subgrupos aislados), que es
exactamente lo que se espera de un amigo secreto. (n-1)! sigue siendo un
espacio enorme: con 10 personas son 362 880 sorteos distintos.

------------------------------------------------------------------------------
4. VERIFICACIÓN
------------------------------------------------------------------------------
`validate_assignments()` audita un resultado **ya generado** sin confiar en
cómo se generó: comprueba R1, R2, R3, R4 y además que el grafo resultante sea
un ciclo único. El panel de admin expone esta función; los tests la usan como
oráculo.
==============================================================================
"""

from __future__ import annotations

import random
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# Presupuesto por defecto de pasos de backtracking antes de rendirse.
DEFAULT_MAX_OPERATIONS = 200_000

ALGORITHM_NAME = "single-hamiltonian-cycle-v1"


class AssignmentError(RuntimeError):
    """Error base del módulo de asignación."""


class NotEnoughParticipants(AssignmentError):
    """Se necesitan al menos 3 participantes para cumplir R3."""


class NoValidAssignment(AssignmentError):
    """Las exclusiones hacen imposible (o inviable) encontrar un ciclo."""


@dataclass(frozen=True)
class AssignmentResult:
    """Resultado de un sorteo."""

    # Orden del ciclo: cycle[i] regala a cycle[i+1], y el último al primero.
    cycle: tuple[int, ...]
    # Lista de aristas (giver_id, receiver_id).
    pairs: tuple[tuple[int, int], ...]
    seed: str
    attempts: int
    algorithm: str = ALGORITHM_NAME


@dataclass
class Issue:
    """Un problema detectado por el validador."""

    code: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    ok: bool
    checked: int
    issues: list[Issue] = field(default_factory=list)


# ===========================================================================
# Utilidades
# ===========================================================================
def new_seed() -> str:
    """Semilla aleatoria de 16 caracteres hexadecimales, con `secrets`."""
    return secrets.token_hex(8)


def _normalize_exclusions(
    participant_ids: Sequence[int],
    exclusions: Mapping[int, Iterable[int]] | None,
) -> dict[int, set[int]]:
    """
    Convierte el mapa de exclusiones a `{id: set(ids prohibidos)}`, añadiendo
    siempre la autoexclusión e ignorando ids que no participan.
    """
    valid = set(participant_ids)
    normalized: dict[int, set[int]] = {pid: {pid} for pid in participant_ids}

    if exclusions:
        for giver, forbidden in exclusions.items():
            if giver not in valid:
                continue
            normalized[giver].update(f for f in forbidden if f in valid)

    return normalized


def _assert_feasible(
    participant_ids: Sequence[int], forbidden: Mapping[int, set[int]]
) -> None:
    """
    Chequeo barato de infactibilidad antes de gastar tiempo en backtracking.

    Si alguien tiene prohibido regalarle a *todos* los demás, o si nadie puede
    regalarle a X, no existe ciclo hamiltoniano. Es una condición necesaria
    (no suficiente), pero atrapa los casos obvios con un mensaje útil.
    """
    n = len(participant_ids)

    for pid in participant_ids:
        out_degree = n - len(forbidden[pid] & set(participant_ids))
        if out_degree <= 0:
            raise NoValidAssignment(
                f"El participante {pid} tiene excluidos a todos los demás: "
                "no puede regalarle a nadie."
            )

    for target in participant_ids:
        in_degree = sum(
            1 for pid in participant_ids if target not in forbidden[pid]
        )
        if in_degree <= 0:
            raise NoValidAssignment(
                f"Nadie puede regalarle al participante {target}: está "
                "excluido por todo el grupo."
            )


# ===========================================================================
# Generación
# ===========================================================================
def generate_assignments(
    participant_ids: Sequence[int],
    exclusions: Mapping[int, Iterable[int]] | None = None,
    seed: str | None = None,
    max_operations: int = DEFAULT_MAX_OPERATIONS,
) -> AssignmentResult:
    """
    Genera una asignación válida de amigo secreto.

    Args:
        participant_ids: ids únicos de los participantes (mínimo 3).
        exclusions: `{giver_id: [receiver_ids prohibidos]}`. Opcional.
        seed: semilla para reproducibilidad. Si es `None` se genera una.
        max_operations: presupuesto de pasos de backtracking.

    Returns:
        `AssignmentResult` con el ciclo, las parejas y la semilla usada.

    Raises:
        NotEnoughParticipants: si hay menos de 3.
        NoValidAssignment: si las exclusiones son imposibles de satisfacer
            o se agota el presupuesto de búsqueda.
    """
    ids = list(dict.fromkeys(participant_ids))  # deduplica preservando orden

    if len(ids) != len(participant_ids):
        raise AssignmentError("La lista de participantes contiene ids repetidos.")
    if len(ids) < 3:
        raise NotEnoughParticipants(
            "Se requieren al menos 3 participantes para garantizar que no "
            "existan regalos cruzados (A→B y B→A). Con 2 personas el cruce "
            "es matemáticamente inevitable."
        )

    seed = seed or new_seed()
    rng = random.Random(seed)

    forbidden = _normalize_exclusions(ids, exclusions)
    _assert_feasible(ids, forbidden)

    cycle, attempts = _find_hamiltonian_cycle(ids, forbidden, rng, max_operations)

    # f(p_i) = p_{(i+1) mod n}
    pairs = tuple(
        (cycle[i], cycle[(i + 1) % len(cycle)]) for i in range(len(cycle))
    )

    return AssignmentResult(
        cycle=tuple(cycle),
        pairs=pairs,
        seed=seed,
        attempts=attempts,
    )


def _find_hamiltonian_cycle(
    ids: list[int],
    forbidden: Mapping[int, set[int]],
    rng: random.Random,
    max_operations: int,
) -> tuple[list[int], int]:
    """
    Backtracking aleatorizado e **iterativo** (sin recursión, para no depender
    del límite de pila de Python con grupos grandes).

    Estructura de la pila: por cada nivel guardamos el nodo elegido y la lista
    de candidatos que aún no se han probado en ese nivel. Al retroceder,
    seguimos probando por donde íbamos.
    """
    n = len(ids)

    # El primer nodo se fija: cualquier rotación del ciclo produce la misma
    # función f, así que fijarlo no pierde soluciones y divide el espacio de
    # búsqueda por n. Se elige al azar para no favorecer siempre al mismo.
    start = rng.choice(ids)

    path: list[int] = [start]
    used: set[int] = {start}
    # candidates_stack[i] = candidatos pendientes para el nivel i+1
    candidates_stack: list[list[int]] = [_candidates(start, ids, used, forbidden, rng)]

    operations = 0

    while path:
        operations += 1
        if operations > max_operations:
            raise NoValidAssignment(
                "No se encontró una asignación válida dentro del presupuesto "
                f"de búsqueda ({max_operations} pasos). Las exclusiones "
                "configuradas son demasiado restrictivas."
            )

        # ¿Ciclo completo? Falta cerrar el último → primero.
        if len(path) == n:
            last = path[-1]
            if start not in forbidden[last]:
                return path, operations
            # No se puede cerrar: retroceder.
            _backtrack(path, used, candidates_stack)
            continue

        pending = candidates_stack[-1]
        if not pending:
            # Rama agotada: retroceder un nivel.
            _backtrack(path, used, candidates_stack)
            continue

        nxt = pending.pop()
        path.append(nxt)
        used.add(nxt)
        candidates_stack.append(_candidates(nxt, ids, used, forbidden, rng))

    raise NoValidAssignment(
        "No existe ninguna asignación que cumpla todas las restricciones. "
        "Revisa las exclusiones configuradas."
    )


def _backtrack(
    path: list[int], used: set[int], candidates_stack: list[list[int]]
) -> None:
    """Deshace el último paso de la búsqueda."""
    candidates_stack.pop()
    node = path.pop()
    used.discard(node)


def _candidates(
    node: int,
    ids: list[int],
    used: set[int],
    forbidden: Mapping[int, set[int]],
    rng: random.Random,
) -> list[int]:
    """
    Sucesores permitidos de `node` aún no visitados, en orden aleatorio.

    La aleatorización por nivel es lo que hace que el sorteo sea realmente
    aleatorio y no siempre el mismo ciclo.
    """
    options = [x for x in ids if x not in used and x not in forbidden[node]]
    rng.shuffle(options)
    return options


# ===========================================================================
# Validación / auditoría
# ===========================================================================
def validate_assignments(
    pairs: Sequence[tuple[int, int]],
    participant_ids: Sequence[int] | None = None,
    exclusions: Mapping[int, Iterable[int]] | None = None,
) -> ValidationResult:
    """
    Audita una asignación ya existente, sin asumir cómo fue generada.

    Comprueba, en este orden:
      1. `self_gift`       — nadie se regala a sí mismo (R2)
      2. `duplicate_giver` — cada persona regala una sola vez (R1)
      3. `duplicate_receiver` — cada persona recibe una sola vez (R1)
      4. `missing_giver` / `missing_receiver` — cobertura total (R1)
      5. `reciprocal_pair` — no hay regalos cruzados A→B y B→A (R3)
      6. `excluded_pair`   — se respetan las exclusiones (R4)
      7. `not_single_cycle` — el grafo es un único ciclo (aviso de calidad)
    """
    issues: list[Issue] = []
    pairs = list(pairs)

    givers = [g for g, _ in pairs]
    receivers = [r for _, r in pairs]
    mapping: dict[int, int] = {}

    # 1 y 2
    for giver, receiver in pairs:
        if giver == receiver:
            issues.append(
                Issue(
                    code="self_gift",
                    message=f"El participante {giver} se asignó a sí mismo.",
                    detail={"participant_id": giver},
                )
            )
        if giver in mapping:
            issues.append(
                Issue(
                    code="duplicate_giver",
                    message=f"El participante {giver} aparece como emisor más de una vez.",
                    detail={"participant_id": giver},
                )
            )
        else:
            mapping[giver] = receiver

    # 3
    seen_receivers: set[int] = set()
    for receiver in receivers:
        if receiver in seen_receivers:
            issues.append(
                Issue(
                    code="duplicate_receiver",
                    message=f"El participante {receiver} recibe más de un regalo.",
                    detail={"participant_id": receiver},
                )
            )
        seen_receivers.add(receiver)

    # 4
    expected = set(participant_ids) if participant_ids is not None else set(givers)
    for pid in sorted(expected - set(givers)):
        issues.append(
            Issue(
                code="missing_giver",
                message=f"El participante {pid} no tiene asignado a quién regalar.",
                detail={"participant_id": pid},
            )
        )
    for pid in sorted(expected - seen_receivers):
        issues.append(
            Issue(
                code="missing_receiver",
                message=f"Nadie le regala al participante {pid}.",
                detail={"participant_id": pid},
            )
        )

    # 5 — regalos cruzados
    reported: set[frozenset[int]] = set()
    for giver, receiver in pairs:
        if mapping.get(receiver) == giver and giver != receiver:
            key = frozenset((giver, receiver))
            if key not in reported:
                reported.add(key)
                issues.append(
                    Issue(
                        code="reciprocal_pair",
                        message=(
                            f"Regalo cruzado detectado entre {giver} y {receiver} "
                            "(se regalan mutuamente)."
                        ),
                        detail={"a": giver, "b": receiver},
                    )
                )

    # 6 — exclusiones
    if exclusions:
        for giver, receiver in pairs:
            if receiver in set(exclusions.get(giver, ())):
                issues.append(
                    Issue(
                        code="excluded_pair",
                        message=(
                            f"El participante {giver} tiene prohibido regalarle "
                            f"a {receiver}, pero fue asignado."
                        ),
                        detail={"giver": giver, "receiver": receiver},
                    )
                )

    # 7 — ¿un único ciclo? Solo tiene sentido si R1 se cumple.
    structural_codes = {
        "self_gift",
        "duplicate_giver",
        "duplicate_receiver",
        "missing_giver",
        "missing_receiver",
    }
    if pairs and not any(i.code in structural_codes for i in issues):
        length = _cycle_length(mapping)
        if length != len(mapping):
            issues.append(
                Issue(
                    code="not_single_cycle",
                    message=(
                        "La asignación se divide en varios subgrupos cerrados "
                        f"(el ciclo que contiene al primer participante tiene "
                        f"{length} de {len(mapping)} personas)."
                    ),
                    detail={"cycle_length": length, "total": len(mapping)},
                )
            )

    return ValidationResult(ok=not issues, checked=len(pairs), issues=issues)


def _cycle_length(mapping: Mapping[int, int]) -> int:
    """Longitud del ciclo que contiene al primer nodo del mapa."""
    if not mapping:
        return 0
    start = next(iter(mapping))
    current = mapping[start]
    length = 1
    while current != start:
        current = mapping[current]
        length += 1
        if length > len(mapping):  # salvaguarda
            break
    return length


def reproduce(
    participant_ids: Sequence[int],
    seed: str,
    exclusions: Mapping[int, Iterable[int]] | None = None,
) -> tuple[tuple[int, int], ...]:
    """
    Re-ejecuta el algoritmo con una semilla conocida.

    Sirve para **verificar** a posteriori que las asignaciones guardadas en la
    base de datos son exactamente las que produce el algoritmo publicado: si
    alguien manipuló la tabla `assignments` a mano, esto lo delata.
    """
    return generate_assignments(participant_ids, exclusions, seed=seed).pairs
