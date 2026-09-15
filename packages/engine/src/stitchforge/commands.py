from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .validation import validate_document


class CommandValidationError(ValueError):
    pass


class LockConflictError(CommandValidationError):
    def __init__(self, indices: list[int]) -> None:
        self.indices = indices
        super().__init__(f"修改触及 {len(indices)} 个 locked 针格")


@dataclass(frozen=True, slots=True)
class CellPatch:
    index: int
    before: int
    after: int

    def as_dict(self) -> dict[str, int]:
        return {"index": self.index, "before": self.before, "after": self.after}


@dataclass(frozen=True, slots=True)
class CommandResult:
    document: dict[str, Any]
    patch: tuple[CellPatch, ...]
    inverse_patch: tuple[CellPatch, ...]


def _locked_indices(document: dict[str, Any]) -> set[int]:
    width = document["grid"]["width"]
    locked: set[int] = set()
    for region in document["regions"]:
        if region["policy"] != "locked":
            continue
        for span in region["spans"]:
            start = span["row"] * width + span["start_col"]
            locked.update(range(start, start + span["end_col"] - span["start_col"]))
    return locked


def validate_locked_transition(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Protect physical stitch semantics at every document persistence boundary."""
    locked = _locked_indices(before)
    if not locked:
        return
    fabric_fields = ("type", "count", "count_unit", "worked_over", "display_srgb")
    if before["grid"] != after["grid"] or any(
        before["fabric"][field] != after["fabric"][field] for field in fabric_fields
    ):
        raise LockConflictError(sorted(locked))

    def meanings(document: dict[str, Any]) -> dict[int, Any]:
        materials = {item["id"]: item for item in document["materials"]}
        return {
            0: None,
            **{
                recipe["index"]: (
                    document["full_stitches"]["top_leg"],
                    [
                        (materials[component["material_id"]], component["strands"])
                        for component in recipe["components"]
                    ],
                )
                for recipe in document["recipes"]
            },
        }

    old_meanings, new_meanings = meanings(before), meanings(after)
    retained_locks = _locked_indices(after)
    conflicts = [
        index
        for index in sorted(locked)
        if index not in retained_locks
        or old_meanings[before["full_stitches"]["cells"][index]]
        != new_meanings[after["full_stitches"]["cells"][index]]
    ]
    if conflicts:
        raise LockConflictError(conflicts)


def apply_cell_updates(document: dict[str, Any], updates: list[tuple[int, int]]) -> CommandResult:
    cells = document["full_stitches"]["cells"]
    allowed_recipes = {0, *(recipe["index"] for recipe in document["recipes"])}
    normalized: dict[int, int] = {}
    for index, recipe_index in updates:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(cells):
            raise CommandValidationError(f"针格索引越界: {index!r}")
        if (
            not isinstance(recipe_index, int)
            or isinstance(recipe_index, bool)
            or recipe_index not in allowed_recipes
        ):
            raise CommandValidationError(f"配方索引不存在: {recipe_index!r}")
        previous = normalized.get(index)
        if previous is not None and previous != recipe_index:
            raise CommandValidationError(f"同一针格出现冲突更新: {index}")
        normalized[index] = recipe_index

    patch = tuple(
        CellPatch(index=index, before=cells[index], after=recipe_index)
        for index, recipe_index in sorted(normalized.items())
        if cells[index] != recipe_index
    )
    locked = _locked_indices(document)
    locked_conflicts = sorted(item.index for item in patch if item.index in locked)
    if locked_conflicts:
        raise LockConflictError(locked_conflicts)

    updated = deepcopy(document)
    updated_cells = updated["full_stitches"]["cells"]
    for item in patch:
        updated_cells[item.index] = item.after
    validate_document(updated).raise_for_errors()
    inverse = tuple(
        CellPatch(index=item.index, before=item.after, after=item.before)
        for item in reversed(patch)
    )
    return CommandResult(document=updated, patch=patch, inverse_patch=inverse)


def replay_patch(document: dict[str, Any], patch: list[CellPatch]) -> CommandResult:
    cells = document["full_stitches"]["cells"]
    mismatches = [item.index for item in patch if cells[item.index] != item.before]
    if mismatches:
        raise CommandValidationError(f"补丁前置值不匹配: {mismatches[:10]}")
    return apply_cell_updates(document, [(item.index, item.after) for item in patch])
