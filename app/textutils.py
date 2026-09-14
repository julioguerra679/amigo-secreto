"""
Normalización de nombres escritos por personas.

Por qué existe este módulo
--------------------------
Los listados de participantes casi nunca se teclean: se copian de WhatsApp, de
un correo, de una nota del móvil o de un Excel. Ese viaje deja restos
invisibles y variaciones tipográficas que rompen cualquier comparación literal
de cadenas:

* **Caracteres invisibles.** WhatsApp inserta `U+2060` (WORD JOINER) delante de
  algunos elementos de una lista. No se ven, no ocupan espacio y no se pueden
  borrar a ojo, pero hacen que `"Laura"` y `"\\u2060Laura"` sean textos
  distintos. También aparecen `U+200B` (espacio de ancho cero), `U+FEFF` (BOM),
  `U+00AD` (guion blando) y las marcas de dirección `U+200E`/`U+200F`.
* **Acentos compuestos.** `"ó"` puede ser un único carácter (`U+00F3`, forma
  NFC) o una `o` seguida de una tilde combinante (`U+006F U+0301`, forma NFD).
  macOS tiende a producir NFD y Windows NFC: se ven idénticos y no coinciden.
* **Espacios raros.** Espacio duro (`U+00A0`), espacio fino, tabuladores.
* **Mayúsculas y separadores.** `"Ana_María"`, `"ana maría"` y `"Ana María"`
  son la misma persona para quien escribe la lista.

Estrategia
----------
Se guarda el nombre **tal como lo escribió el organizador** (solo limpio de
invisibles, para que se muestre bien), y se compara mediante *claves*
normalizadas. Así el usuario ve "Ana_María" en la tabla, pero el sistema
reconoce que la exclusión que dice "ana maría" habla de ella.

Hay dos niveles de clave, de más estricto a más tolerante:

    name_key()        minúsculas, sin invisibles, `_` y espacios equivalentes
    name_key_loose()  lo anterior, además sin acentos ni diacríticos

El nivel laxo solo se usa como último recurso y nunca cuando resultaría
ambiguo (dos personas distintas con la misma clave laxa).
"""

from __future__ import annotations

import unicodedata

# Caracteres invisibles que se eliminan siempre. Todos son de categoría Unicode
# "Cf" (format) salvo el guion blando, que es "Pd" pero se comporta igual de
# traicioneramente al copiar y pegar.
INVISIBLE_CHARS = frozenset(
    {
        "­",  # SOFT HYPHEN
        "​",  # ZERO WIDTH SPACE
        "‌",  # ZERO WIDTH NON-JOINER
        "‍",  # ZERO WIDTH JOINER
        "‎",  # LEFT-TO-RIGHT MARK
        "‏",  # RIGHT-TO-LEFT MARK
        "⁠",  # WORD JOINER  ← el que inserta WhatsApp
        "⁡",  # FUNCTION APPLICATION
        "⁢",  # INVISIBLE TIMES
        "⁣",  # INVISIBLE SEPARATOR
        "⁤",  # INVISIBLE PLUS
        "﻿",  # ZERO WIDTH NO-BREAK SPACE (BOM)
    }
)

# Espacios de todo tipo que se convierten en un espacio normal.
UNUSUAL_SPACES = frozenset(
    {
        " ",  # NO-BREAK SPACE
        " ",
        " ", " ", " ", " ", " ", " ",
        " ", " ", " ", " ", " ",
        " ",  # NARROW NO-BREAK SPACE
        " ",
        "　",  # IDEOGRAPHIC SPACE
    }
)


def clean_text(value: str) -> str:
    """
    Deja un texto en su forma canónica visible.

    Quita los caracteres invisibles, convierte los espacios exóticos en
    espacios normales, colapsa los espacios repetidos y normaliza los acentos
    a la forma NFC (un solo carácter por letra acentuada).

    El resultado es lo que se guarda y se muestra: conserva mayúsculas,
    acentos y guiones bajos tal como los escribió el organizador.
    """
    if not value:
        return ""

    # NFC primero: unifica "o + tilde combinante" en "ó".
    value = unicodedata.normalize("NFC", value)

    chars = []
    for char in value:
        if char in INVISIBLE_CHARS:
            continue
        # Cualquier otro carácter de formato invisible que no esté en la lista.
        if unicodedata.category(char) == "Cf":
            continue
        chars.append(" " if char in UNUSUAL_SPACES else char)

    # Colapsa espacios y recorta.
    return " ".join("".join(chars).split())


def name_key(value: str) -> str:
    """
    Clave de comparación estricta.

    Ignora mayúsculas y trata `_`, `-` y los espacios como equivalentes, que es
    como los trata quien escribe la lista: "Ana_María", "Ana María" y
    "ana maría" son la misma persona.
    """
    cleaned = clean_text(value)
    for separator in ("_", "-", "."):
        cleaned = cleaned.replace(separator, " ")
    return " ".join(cleaned.split()).casefold()


def strip_accents(value: str) -> str:
    """Quita tildes y diéresis: 'Calderón' -> 'Calderon'."""
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def name_key_loose(value: str) -> str:
    """
    Clave de comparación tolerante: como `name_key` pero además sin acentos.

    Permite que una exclusión escrita como "Juan_Carlos_Calderon" encuentre al
    participante "Juan_Carlos_Calderón". Solo se usa cuando la clave estricta
    no encuentra nada y el resultado no es ambiguo.
    """
    return strip_accents(name_key(value))


def describe_invisibles(value: str) -> list[str]:
    """
    Lista los caracteres invisibles de un texto, en notación `U+XXXX`.

    Se usa en los mensajes de error para poder decirle al organizador qué tenía
    exactamente el nombre que falló, ya que por definición no puede verlo.
    """
    found = []
    for char in value or "":
        if char in INVISIBLE_CHARS or unicodedata.category(char) == "Cf":
            found.append(f"U+{ord(char):04X}")
    return found
