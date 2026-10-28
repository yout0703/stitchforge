from __future__ import annotations

import pytest
from stitchforge.symbols import MAX_SYMBOLS, SYMBOLS, symbol_for_id


def test_all_64_symbol_ids_have_unique_printable_glyphs() -> None:
    glyphs = [symbol_for_id(f"s{index:03d}") for index in range(1, 65)]
    assert len(SYMBOLS) == 64
    assert len(set(glyphs)) == 64
    assert all(glyph.isprintable() and not glyph.isspace() for glyph in glyphs)


def test_extended_symbols_are_unique_and_keep_existing_mapping() -> None:
    glyphs = [symbol_for_id(f"s{index:03d}") for index in range(1, MAX_SYMBOLS + 1)]
    assert glyphs[:64] == list(SYMBOLS)
    assert len(set(glyphs)) == MAX_SYMBOLS
    assert glyphs[64] == "AA"
    assert glyphs[76] == "AM"
    assert all(g.isascii() and g.isprintable() and len(g) <= 2 for g in glyphs)


@pytest.mark.parametrize("symbol_id", ["s000", "s4097", "custom", "s-1"])
def test_out_of_profile_symbol_ids_are_rejected(symbol_id: str) -> None:
    with pytest.raises(ValueError, match="没有可用字形映射"):
        symbol_for_id(symbol_id)
