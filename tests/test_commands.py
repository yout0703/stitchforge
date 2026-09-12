from __future__ import annotations

from copy import deepcopy

import pytest
from stitchforge.commands import (
    CommandValidationError,
    LockConflictError,
    apply_cell_updates,
    replay_patch,
)


def test_cell_command_builds_stable_forward_and_inverse_patch(minimal_document: dict) -> None:
    result = apply_cell_updates(minimal_document, [(11, 2), (1, 1), (1, 1)])

    assert [item.as_dict() for item in result.patch] == [
        {"index": 1, "before": 0, "after": 1},
        {"index": 11, "before": 1, "after": 2},
    ]
    assert minimal_document["full_stitches"]["cells"][1] == 0
    restored = replay_patch(result.document, list(result.inverse_patch))
    assert restored.document == minimal_document


def test_cell_command_rejects_locked_cell(minimal_document: dict) -> None:
    with pytest.raises(LockConflictError) as captured:
        apply_cell_updates(minimal_document, [(0, 2), (1, 1)])
    assert captured.value.indices == [0]


@pytest.mark.parametrize("updates", [[(99, 1)], [(1, 99)], [(1, 1), (1, 2)]])
def test_cell_command_rejects_invalid_updates(minimal_document: dict, updates) -> None:
    with pytest.raises(CommandValidationError):
        apply_cell_updates(minimal_document, updates)


def test_replay_rejects_changed_precondition(minimal_document: dict) -> None:
    changed = deepcopy(minimal_document)
    changed["full_stitches"]["cells"][1] = 2
    patch = apply_cell_updates(minimal_document, [(1, 1)]).patch
    with pytest.raises(CommandValidationError, match="前置值"):
        replay_patch(changed, list(patch))
