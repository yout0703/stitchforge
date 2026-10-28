from __future__ import annotations

SYMBOLS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz!?")
MAX_SYMBOLS = 4096


def symbol_for_id(symbol_id: str) -> str:
    if symbol_id.startswith("s") and symbol_id[1:].isdigit():
        position = int(symbol_id[1:]) - 1
        if 0 <= position < MAX_SYMBOLS:
            if position < len(SYMBOLS):
                return SYMBOLS[position]
            first, second = divmod(position - len(SYMBOLS), len(SYMBOLS))
            return SYMBOLS[first] + SYMBOLS[second]
    raise ValueError(f"符号 ID 没有可用字形映射: {symbol_id}")
