from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pdfplumber

OFFICIAL_COUNT = 489
APPROXIMATE_COUNT = 454
CODE_PATTERN = re.compile(r"(?:\d{1,4}|B5200|ECRU|BLANC)")
QUILTY_CODE_PATTERN = re.compile(r" - ([A-Za-z0-9]+)$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_official_identifiers(path: Path) -> dict[str, str]:
    """Extract the solid Article 117 identifiers from DMC's January 2021 list."""

    identifiers: dict[str, str] = {}
    column_specs = (
        (20, 55, 55, 200),
        (200, 245, 245, 390),
        (390, 430, 430, 600),
    )
    with pdfplumber.open(path) as document:
        if len(document.pages) != 3:
            raise ValueError("DMC 2021 numerical list should contain three pages")
        for page_number, page in enumerate(document.pages, start=1):
            words = page.extract_words(use_text_flow=False)
            for column, (code_x0, code_x1, name_x0, name_x1) in enumerate(column_specs):
                code_words = [
                    word
                    for word in words
                    if code_x0 <= word["x0"] < code_x1
                    and 75 <= word["top"] <= 730
                    and CODE_PATTERN.fullmatch(word["text"])
                ]
                for code_word in code_words:
                    # Page three's last column switches to variegated colors below
                    # the solid BLANC entry. Variable colors need a path model and
                    # are deliberately outside this catalog.
                    if page_number == 3 and column == 2 and code_word["top"] >= 390:
                        continue
                    name_words = [
                        word
                        for word in words
                        if name_x0 <= word["x0"] < name_x1
                        and abs(word["top"] - code_word["top"]) <= 1.0
                    ]
                    code = code_word["text"]
                    name = " ".join(
                        word["text"] for word in sorted(name_words, key=lambda item: item["x0"])
                    )
                    if not name or code in identifiers:
                        raise ValueError(f"invalid or duplicate DMC identifier {code!r}")
                    identifiers[code] = name
    if len(identifiers) != OFFICIAL_COUNT:
        raise ValueError(
            f"expected {OFFICIAL_COUNT} official solid identifiers, got {len(identifiers)}"
        )
    return identifiers


def read_quilty_approximations(path: Path) -> dict[str, str]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    swatches = document["dmc"]["swatches"]
    approximations: dict[str, str] = {}
    for swatch in swatches:
        match = QUILTY_CODE_PATTERN.search(swatch["label"])
        if match is None:
            raise ValueError(f"cannot extract DMC code from {swatch['label']!r}")
        code = match.group(1).upper()
        # QuiltySolid uses the English alias WHITE. DMC's numerical listing uses
        # BLANC for that Article 117 identity, so normalize before joining.
        if code == "WHITE":
            code = "BLANC"
        color = swatch["hex"].upper()
        if code in approximations or re.fullmatch(r"#[0-9A-F]{6}", color) is None:
            raise ValueError(f"invalid or duplicate QuiltySolid DMC swatch {code!r}")
        approximations[code] = color
    if len(approximations) != APPROXIMATE_COUNT:
        raise ValueError(
            f"expected {APPROXIMATE_COUNT} approximate swatches, got {len(approximations)}"
        )
    return approximations


def build_catalog(official_pdf: Path, quilty_json: Path) -> dict[str, Any]:
    identifiers = read_official_identifiers(official_pdf)
    approximations = read_quilty_approximations(quilty_json)
    unexpected = sorted(set(approximations) - set(identifiers))
    if unexpected:
        raise ValueError(
            f"QuiltySolid contains identifiers absent from the official list: {unexpected}"
        )

    def sort_key(code: str) -> tuple[int, int | str]:
        return (0, int(code)) if code.isdigit() else (1, code)

    materials = []
    for code in sorted(identifiers, key=sort_key):
        display_srgb = approximations.get(code)
        materials.append(
            {
                "id": f"dmc_117_{code.lower()}",
                "brand": "DMC",
                "series": "Mouline Special 117",
                "code": code,
                "name": identifiers[code],
                "display_srgb": display_srgb,
                "color_status": "approximate" if display_srgb else "unknown",
                "available": True,
                # This is only a fallback for old callers. A pattern should choose
                # strands from its fabric/coverage profile.
                "default_strands": 2,
            }
        )

    return {
        "schema_version": "0.1.0",
        "snapshot_id": "dmc_117_2021_quiltysolid_2025_07_v1",
        "source": {
            "id": "src_dmc_117_quiltysolid_2025_07",
            "locator": "https://www.quiltysolid.com/fabricSwatches.json",
            "description": (
                "DMC Article 117 identities from the official January 2021 numerical list "
                f"(SHA-256 {_sha256(official_pdf)}); approximate screen sRGB for "
                f"{APPROXIMATE_COUNT} legacy solid colors from QuiltySolid's July 2025 "
                f"MIT-licensed JSON snapshot (SHA-256 {_sha256(quilty_json)}). The remaining "
                "35 official colors are retained with unknown appearance. Names are descriptive; "
                "DMC identifies colors by code. Values are not physical stitched-swatch "
                "measurements."
            ),
            "permission_status": "redistributable",
        },
        "materials": materials,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the versioned StitchForge DMC catalog")
    parser.add_argument("--official-pdf", type=Path, required=True)
    parser.add_argument("--quilty-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = build_catalog(args.official_pdf, args.quilty_json)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
