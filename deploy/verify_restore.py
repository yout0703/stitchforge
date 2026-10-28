"""Validate restored preflight data using the actual application storage layer."""

import sqlite3
from contextlib import closing
from pathlib import Path

from stitchforge.validation import validate_document
from stitchforge_storage import ContentAddressedArtifactStore, SQLiteStore


def main():
    data = Path("/data")
    with closing(sqlite3.connect(data / "metadata.sqlite3")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        rows = db.execute("SELECT project_id FROM projects").fetchall()
        hashes = db.execute(
            "SELECT artifact_hash FROM asset_access UNION SELECT artifact_hash FROM artifacts"
        ).fetchall()
    assert rows and hashes, "Restore drill must include real generated test content"
    store = SQLiteStore(data / "metadata.sqlite3")
    for (project_id,) in rows:
        project = store.get_project(project_id)
        revision = store.get_revision(project.head_revision_id)
        validate_document(revision.document).raise_for_errors()
    artifacts = ContentAddressedArtifactStore(data / "artifacts")
    for (digest,) in hashes:
        artifacts.read_bytes(digest)
    print(f"PASS: restored {len(rows)} valid projects and {len(hashes)} verified artifacts")


if __name__ == "__main__":
    main()
