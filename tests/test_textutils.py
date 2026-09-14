"""
Tests de la normalización de nombres.

Regresión concreta: una lista copiada de WhatsApp trae `U+2060` (WORD JOINER)
delante de algunas líneas. Es invisible, así que el organizador veía
"Daniela_Restrepo" en el listado y escribía "Daniela_Restrepo" en la exclusión,
pero los dos textos no eran iguales para el ordenador y la carga fallaba con un
mensaje incomprensible.
"""

from __future__ import annotations

import pytest

from app.services.participants import (
    parse_exclusions_text,
    parse_participants_text,
)
from app.textutils import (
    clean_text,
    describe_invisibles,
    name_key,
    name_key_loose,
    strip_accents,
)

WJ = "⁠"  # WORD JOINER, el que inserta WhatsApp


# ===========================================================================
# Limpieza de caracteres invisibles
# ===========================================================================
@pytest.mark.parametrize(
    "invisible",
    ["⁠", "​", "﻿", "‎", "­", "⁣"],
)
def test_invisible_characters_are_removed(invisible: str) -> None:
    assert clean_text(f"{invisible}Laura") == "Laura"
    assert clean_text(f"Lau{invisible}ra") == "Laura"
    assert clean_text(f"Laura{invisible}") == "Laura"


def test_whatsapp_list_matches_after_cleaning() -> None:
    """El caso exacto que falló: el invisible está en una línea y en otra no."""
    en_el_listado = f"{WJ}Daniela_Restrepo"
    en_la_exclusion = "Daniela_Restrepo"

    assert en_el_listado != en_la_exclusion          # antes: no cruzaban
    assert clean_text(en_el_listado) == clean_text(en_la_exclusion)
    assert name_key(en_el_listado) == name_key(en_la_exclusion)


def test_unusual_spaces_become_normal_spaces() -> None:
    assert clean_text("Ana María") == "Ana María"
    assert clean_text("Ana   María") == "Ana María"
    assert clean_text("  Ana María  ") == "Ana María"


def test_decomposed_accents_are_unified() -> None:
    """macOS produce NFD y Windows NFC: se ven igual pero no coinciden."""
    nfc = "Calderón"
    nfd = "Calderón"

    assert nfc != nfd
    assert clean_text(nfc) == clean_text(nfd)


def test_describe_invisibles_reports_what_cannot_be_seen() -> None:
    assert describe_invisibles(f"{WJ}Laura") == ["U+2060"]
    assert describe_invisibles("Laura") == []


# ===========================================================================
# Claves de comparación
# ===========================================================================
def test_name_key_ignores_case_and_separators() -> None:
    variantes = ["Ana_María", "ana maría", "ANA-MARÍA", f"{WJ}Ana_María "]
    claves = {name_key(v) for v in variantes}
    assert len(claves) == 1


def test_name_key_keeps_accents() -> None:
    """La clave estricta NO debe confundir a personas distintas."""
    assert name_key("Calderón") != name_key("Calderon")


def test_loose_key_ignores_accents() -> None:
    assert name_key_loose("Juan_Carlos_Calderón") == name_key_loose(
        "juan carlos calderon"
    )


def test_strip_accents() -> None:
    assert strip_accents("Héctor_Ángel_Salomé") == "Hector_Angel_Salome"


def test_different_people_keep_different_keys() -> None:
    distintos = ["Estela_Tia", "Estella_Beto", "Danna_Guerra", "Mariana_Guerra"]
    assert len({name_key(n) for n in distintos}) == len(distintos)


# ===========================================================================
# Parseo del listado real
# ===========================================================================
LISTA_WHATSAPP = f"""Lina_Restrepo,
{WJ}Daniela_Restrepo,
{WJ}Eduardo_Castro,
Danna_Guerra,
Alexandra_Calderón,
{WJ}adalberto,
{WJ}cenelia,
{WJ}Salomé_Restrepo"""


def test_parses_a_whatsapp_list_with_trailing_commas() -> None:
    people = parse_participants_text(LISTA_WHATSAPP)
    names = [p.name for p in people]

    assert len(people) == 8
    assert "Daniela_Restrepo" in names          # sin el invisible
    assert f"{WJ}Daniela_Restrepo" not in names
    assert not any("," in n for n in names)     # sin la coma final
    assert all(not describe_invisibles(n) for n in names)


def test_exclusions_parse_with_invisibles_and_dash_variants() -> None:
    rules = parse_exclusions_text(
        f"""Daniela_Restrepo - {WJ}Eduardo_Castro
        adalberto — cenelia
        Alexandra_Calderón –  Lina_Restrepo
        Danna_Guerra -> Lina_Restrepo"""
    )
    assert len(rules) == 4
    assert rules[0].giver == "Daniela_Restrepo"
    assert rules[0].receiver == "Eduardo_Castro"
    assert rules[0].mutual is True
    assert rules[3].mutual is False           # '->' es en un solo sentido


def test_duplicate_names_detected_across_spelling_variants() -> None:
    from app.services.participants import ParticipantServiceError

    with pytest.raises(ParticipantServiceError, match="repetidos"):
        parse_participants_text("Ana_María\nana maría\nLuis")
