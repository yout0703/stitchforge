"""Cross-process computation admission for the local Unix deployment."""

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def computation_slot(work_dir: Path):
    work_dir.mkdir(parents=True, exist_ok=True)
    with (work_dir / "computation.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
