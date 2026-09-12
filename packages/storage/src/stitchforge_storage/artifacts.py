from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ArtifactIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    artifact_hash: str
    byte_length: int
    media_type: str
    storage_key: str


class ContentAddressedArtifactStore:
    """Immutable local artifacts published atomically under their SHA-256 digest."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._objects = self.root / "sha256"
        self._objects.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def storage_key(artifact_hash: str) -> str:
        if SHA256_PATTERN.fullmatch(artifact_hash) is None:
            raise ValueError("artifact_hash 必须是小写 SHA-256")
        return f"sha256/{artifact_hash[:2]}/{artifact_hash[2:]}"

    def _path_for_hash(self, artifact_hash: str) -> Path:
        return self.root / self.storage_key(artifact_hash)

    @staticmethod
    def _digest_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        length = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                length += len(chunk)
        return digest.hexdigest(), length

    def _verify_existing(self, target: Path, expected_hash: str, expected_length: int) -> None:
        actual_hash, actual_length = self._digest_file(target)
        if (actual_hash, actual_length) != (expected_hash, expected_length):
            raise ArtifactIntegrityError(f"内容寻址产物已损坏: {expected_hash}")

    @staticmethod
    def _publish(temporary: Path, target: Path) -> None:
        """Atomically publish without replacing an existing immutable object."""
        os.link(temporary, target)

    def put_bytes(self, payload: bytes, *, media_type: str) -> StoredArtifact:
        if not media_type:
            raise ValueError("media_type 不能为空")
        artifact_hash = hashlib.sha256(payload).hexdigest()
        target = self._path_for_hash(artifact_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_existing(target, artifact_hash, len(payload))
            return StoredArtifact(
                artifact_hash=artifact_hash,
                byte_length=len(payload),
                media_type=media_type,
                storage_key=self.storage_key(artifact_hash),
            )

        descriptor: int | None = None
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".stitchforge-", dir=target.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                self._publish(temporary, target)
            except FileExistsError:
                self._verify_existing(target, artifact_hash, len(payload))
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        self._verify_existing(target, artifact_hash, len(payload))
        return StoredArtifact(
            artifact_hash=artifact_hash,
            byte_length=len(payload),
            media_type=media_type,
            storage_key=self.storage_key(artifact_hash),
        )

    def read_bytes(self, artifact_hash: str) -> bytes:
        target = self._path_for_hash(artifact_hash)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as error:
            raise FileNotFoundError(f"产物不存在: {artifact_hash}") from error
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != artifact_hash:
            raise ArtifactIntegrityError(f"内容寻址产物已损坏: {artifact_hash}")
        return payload

    def verify(self, artifact: StoredArtifact) -> None:
        expected_key = self.storage_key(artifact.artifact_hash)
        if artifact.storage_key != expected_key:
            raise ArtifactIntegrityError("产物 storage_key 与内容哈希不一致")
        target = self.root / expected_key
        try:
            self._verify_existing(target, artifact.artifact_hash, artifact.byte_length)
        except FileNotFoundError as error:
            raise ArtifactIntegrityError(f"产物不存在: {artifact.artifact_hash}") from error
