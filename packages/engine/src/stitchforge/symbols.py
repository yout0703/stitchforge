from __future__ import annotations

SYMBOLS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz!?")


def symbol_for_id(symbol_id: str) -> str:
    if symbol_id.startswith("s") and symbol_id[1:].isdigit():
        position = int(symbol_id[1:]) - 1
        if 0 <= position < len(SYMBOLS):
            return SYMBOLS[position]
    raise ValueError(f"符号 ID 没有可用字形映射: {symbol_id}")
