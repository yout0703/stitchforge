from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from stitchforge_storage import (
    ArtifactIntegrityError,
    ContentAddressedArtifactStore,
)


def test_artifact_round_trip_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    payload = b"deterministic stage output"

    first = store.put_bytes(payload, media_type="application/octet-stream")
    second = store.put_bytes(payload, media_type="application/octet-stream")

    assert first == second
    assert first.artifact_hash == hashlib.sha256(payload).hexdigest()
    assert first.storage_key.startswith("sha256/")
    assert store.read_bytes(first.artifact_hash) == payload
    store.verify(first)


def test_publish_failure_leaves_no_partial_object_or_temporary_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")

    def fail_publish(temporary: Path, target: Path) -> None:
        raise OSError("injected publish failure")

    monkeypatch.setattr(store, "_publish", fail_publish)
    with pytest.raises(OSError, match="injected"):
        store.put_bytes(b"partial", media_type="application/octet-stream")

    assert list((tmp_path / "artifacts").rglob(".stitchforge-*")) == []
    assert list((tmp_path / "artifacts" / "sha256").rglob("?" * 62)) == []


def test_corrupt_existing_object_is_rejected(tmp_path: Path) -> None:
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    artifact = store.put_bytes(b"original", media_type="text/plain")
    target = store.root / artifact.storage_key
    target.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError, match="已损坏"):
        store.put_bytes(b"original", media_type="text/plain")
    with pytest.raises(ArtifactIntegrityError, match="已损坏"):
        store.read_bytes(artifact.artifact_hash)
