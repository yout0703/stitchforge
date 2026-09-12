"""SQLite persistence adapter for the local StitchForge workbench."""

from .artifacts import ArtifactIntegrityError, ContentAddressedArtifactStore, StoredArtifact
from .sqlite import (
    ArtifactRecord,
    CommandRecord,
    JobLeaseLostError,
    JobRecord,
    JobStageRecord,
    ProjectRecord,
    RevisionRecord,
    SQLiteStore,
    StorageConflictError,
    StorageNotFoundError,
)

__all__ = [
    "ArtifactIntegrityError",
    "ArtifactRecord",
    "CommandRecord",
    "ContentAddressedArtifactStore",
    "JobLeaseLostError",
    "JobRecord",
    "JobStageRecord",
    "ProjectRecord",
    "RevisionRecord",
    "SQLiteStore",
    "StorageConflictError",
    "StorageNotFoundError",
    "StoredArtifact",
]
