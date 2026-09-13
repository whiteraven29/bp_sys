#!/usr/bin/env bash
#
# edutrack-restic — run any restic command against the EduTrack backups in
# Google Drive, without typing the repository, password file or rclone config.
#
#     sudo edutrack-restic snapshots                 list the backups
#     sudo edutrack-restic ls latest                 what is inside the newest one
#     sudo edutrack-restic restore latest --target /root/recovery
#     sudo edutrack-restic stats                     how much space they use
#
# Guide: ops/guides/01-backups.md

set -Eeuo pipefail

CONFIG=${EDUTRACK_OPS_CONFIG:-/etc/edutrack/ops.env}
if [[ ! -r $CONFIG ]]; then
    echo "Cannot read $CONFIG. Create it first: ops/guides/01-backups.md, step 7." >&2
    exit 1
fi
set -a
# shellcheck source=SCRIPTDIR/../ops.env.example
source "$CONFIG"
set +a

exec restic "$@"
