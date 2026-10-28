"""Archive only immutable objects referenced by a consistent database snapshot."""

import hashlib
import sqlite3
import sys
import tarfile
from contextlib import closing
from pathlib import Path


def main():
    backup = Path(sys.argv[1])
    data = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/opt/stitchforge/shared/data")
    with closing(sqlite3.connect(backup / "metadata.sqlite3")) as db:
        hashes = db.execute(
            "SELECT artifact_hash FROM asset_access UNION SELECT artifact_hash FROM artifacts"
        ).fetchall()
    with tarfile.open(backup / "artifacts.tar.gz", "w:gz") as archive:
        directory = tarfile.TarInfo("artifacts")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o750
        archive.addfile(directory)
        for (digest,) in hashes:
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid artifact digest in database snapshot")
            relative = Path("artifacts/sha256") / digest[:2] / digest[2:]
            source = data / relative
            with source.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("Artifact integrity check failed")
            archive.add(source, arcname=str(relative), recursive=False)
    print(f"Verified and archived {len(hashes)} referenced artifacts")


if __name__ == "__main__":
    main()
