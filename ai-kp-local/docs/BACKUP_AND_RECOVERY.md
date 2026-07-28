# Backup and recovery

AI KP backups are self-contained ZIP archives stored under
`AI_KP_BACKUP_ROOT` (default `data/backups`). A package contains:

- an online SQLite snapshot created with `Connection.backup`;
- content-addressed map assets;
- immutable module source documents and content-addressed extracted images;
- local rulebook indexes;
- a manifest with format, application and schema versions;
- the uncompressed size and SHA-256 digest of every packaged file.

Only a local administrator may list, create, verify, or download packages:

```text
GET  /admin/backups
POST /admin/backups
POST /admin/backups/{filename}/verify
GET  /admin/backups/{filename}/content
```

Restore is deliberately not exposed through HTTP. Stop the API and run:

```bash
PYTHONPATH=src .venv/bin/python scripts/restore_backup.py \
  data/backups/ai-kp-backup-YYYYMMDDTHHMMSSZ.zip \
  --confirm RESTORE
```

The restore command validates member paths, duplicate entries, file count,
uncompressed size, every digest, supported schema version, and SQLite
`PRAGMA integrity_check` before replacing durable state. A malformed archive
must fail before the current database or file stores are changed.

Backup archives contain session credential hashes and configured model API
keys from SQLite. New packages are created with owner-only file permissions,
but they should still be handled as secrets and never committed to Git or
shared with players.

After recovery, start the API and run `/debug/database/check`, then inspect one
campaign, investigator, NPC, memory, module source/image, map image and token position. Keep at
least one copy outside the application data directory so disk loss does not
remove both live data and backups.
