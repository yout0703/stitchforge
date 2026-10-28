#!/bin/sh
# The SQLite snapshot precedes the immutable-artifact copy; orphan blobs are harmless.
set -eu
umask 077
cd /opt/stitchforge/current
backup_dir=$(mktemp -d /opt/stitchforge/backups/snapshot-XXXXXXXX)
docker compose --env-file deploy/release.env -p stitchforge -f deploy/compose.yaml exec -T app python -c '
import sqlite3
from contextlib import closing
with closing(sqlite3.connect("/data/metadata.sqlite3")) as source:
    with closing(sqlite3.connect("/data/metadata.backup.sqlite3")) as target:
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
'
install -m 600 /opt/stitchforge/shared/data/metadata.backup.sqlite3 "$backup_dir/metadata.sqlite3"
python3 /opt/stitchforge/current/deploy/backup_artifacts.py "$backup_dir"
printf 'Backup completed: %s\n' "$backup_dir"
