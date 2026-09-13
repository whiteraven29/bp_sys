#!/usr/bin/env bash
#
# edutrack-backup — back up everything EduTrack cannot rebuild from git.
#
# What it saves
#   * the PostgreSQL database          attendance, marks, invoices, payments, requests …
#   * backend/media/                   uploaded letters, announcement PDFs, the college logo
#   * backend/.env                     SECRET_KEY and the database password
#   * the nginx site and gunicorn service files, to compare against after a rebuild
#
# Where it puts them
#   1. $BACKUP_ROOT/daily/ on this server — the last $LOCAL_KEEP database dumps.
#      A quick undo for mistakes. It dies with the server, so it is not enough alone.
#   2. Google Drive, through restic — encrypted before it leaves the server and
#      kept for a year (7 daily, 4 weekly, 12 monthly). This is the real backup.
#
# Installed as /usr/local/sbin/edutrack-backup, run every night by
# edutrack-backup.timer, and safe to run by hand at any time:
#
#     sudo edutrack-backup
#
# Guide: ops/guides/01-backups.md

set -Eeuo pipefail
umask 077    # everything this script writes is readable by root only

CONFIG=${EDUTRACK_OPS_CONFIG:-/etc/edutrack/ops.env}
if [[ ! -r $CONFIG ]]; then
    echo "Cannot read $CONFIG. Create it first: ops/guides/01-backups.md, step 7." >&2
    exit 1
fi
set -a                 # export every setting, so restic and rclone see theirs
# shellcheck source=SCRIPTDIR/../ops.env.example
source "$CONFIG"
set +a

: "${REPO_DIR:=/var/www/edutrack}" "${SERVICE:=edutrack}" "${DB_NAME:=edutrack_db}"
: "${BACKUP_ROOT:=/var/backups/edutrack}" "${LOCAL_KEEP:=7}"
: "${KEEP_DAILY:=7}" "${KEEP_WEEKLY:=4}" "${KEEP_MONTHLY:=12}"
: "${NGINX_SITE:=/etc/nginx/sites-available/edutrack}"
: "${POSTGRES_RUN_AS=postgres}"     # empty = connect as yourself (PGHOST, PGUSER …)

BACKEND=$REPO_DIR/backend
DAILY=$BACKUP_ROOT/daily
STAGING=$BACKUP_ROOT/staging       # what gets sent to Google Drive, laid out flat

log() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# PostgreSQL's own admin account can read every table without a password.
as_postgres() {
    if [[ -z $POSTGRES_RUN_AS || $(id -un) == "$POSTGRES_RUN_AS" ]]; then "$@"
    elif [[ $EUID -eq 0 ]]; then runuser -u "$POSTGRES_RUN_AS" -- "$@"
    else sudo -u "$POSTGRES_RUN_AS" -- "$@"
    fi
}

# Tell healthchecks.io (if configured) how the run went. Never fails the backup.
ping_health() {
    [[ -n ${HEALTHCHECK_URL:-} ]] || return 0
    curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTHCHECK_URL$1" || true
}

on_exit() {
    local status=$?
    if (( status != 0 )); then
        log "Backup FAILED (exit $status). Nothing was deleted from Google Drive."
        ping_health /fail
    fi
}
trap on_exit EXIT

# Exact row count of every table, one "table<TAB>rows" line each. Stored with the
# backup so a restore can prove it brought back as many rows as were saved.
COUNT_SQL="SELECT table_name,
       (xpath('/row/n/text()',
              query_to_xml(format('SELECT count(*) AS n FROM public.%I', table_name),
                           false, true, '')))[1]::text
  FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
 ORDER BY table_name"

ping_health /start
mkdir -p "$DAILY" "$STAGING/config"
chmod 700 "$BACKUP_ROOT"
rm -f "$DAILY"/*.partial

# ── 1. Database ───────────────────────────────────────────────────────────────
stamp=$(date +%Y-%m-%d_%H%M)
dump=$DAILY/$DB_NAME-$stamp.dump
log "1/5 Dumping database $DB_NAME"
# -Z 0 leaves the dump uncompressed on purpose: restic then recognises the parts
# that did not change since last night and uploads only the rest, compressed.
as_postgres pg_dump --format=custom -Z 0 "$DB_NAME" > "$dump.partial"
# A dump pg_restore cannot read is not a backup: find out now, not during an emergency.
pg_restore --list "$dump.partial" > /dev/null || die "the new dump is unreadable"
mv "$dump.partial" "$dump"
log "    $(du -h "$dump" | cut -f1)  $dump"

# ── 2. Local copies: keep the newest $LOCAL_KEEP ─────────────────────────────
# Counted, not aged: if backups stop for a month the last good ones stay.
log "2/5 Keeping the newest $LOCAL_KEEP dumps in $DAILY"
find "$DAILY" -maxdepth 1 -name '*.dump' -printf '%T@ %p\n' | sort -rn \
    | tail -n +"$((LOCAL_KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -v --

# ── 3. Gather what goes off-site ─────────────────────────────────────────────
log "3/5 Gathering the database, uploaded files and settings"
cp "$dump" "$STAGING/database.dump"
mkdir -p "$BACKEND/media"
rsync -a --delete "$BACKEND/media/" "$STAGING/media/"
if [[ -f $BACKEND/.env ]]; then
    cp "$BACKEND/.env" "$STAGING/env"
else
    log "    WARNING: $BACKEND/.env not found — the backup will not contain it"
fi
for file in "$NGINX_SITE" "/etc/systemd/system/$SERVICE.service"; do
    if [[ -f $file ]]; then cp "$file" "$STAGING/config/"; fi
done

as_postgres psql -XAtq -F $'\t' -d "$DB_NAME" -c "$COUNT_SQL" > "$STAGING/table-counts.tsv"
migration=$(as_postgres psql -XAtq -d "$DB_NAME" \
    -c "SELECT app || '.' || name FROM django_migrations ORDER BY id DESC LIMIT 1" 2>/dev/null || true)
commit=$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
cat > "$STAGING/MANIFEST.txt" <<EOF
EduTrack backup
taken:            $(date -Is)
server:           $(hostname)
database:         $DB_NAME, PostgreSQL $(as_postgres psql -XAtq -d "$DB_NAME" -c 'SHOW server_version')
tables:           $(wc -l < "$STAGING/table-counts.tsv")
rows:             $(awk -F'\t' '{ n += $2 } END { print n + 0 }' "$STAGING/table-counts.tsv")
latest migration: ${migration:-unknown}
code commit:      $commit
media files:      $(find "$STAGING/media" -type f | wc -l)
EOF
sed 's/^/    /' "$STAGING/MANIFEST.txt"

# ── 4. Send it to Google Drive ───────────────────────────────────────────────
[[ -r ${RESTIC_PASSWORD_FILE:-} ]] || die "no restic password file — see ops/guides/01-backups.md, step 6"
log "4/5 Sending an encrypted snapshot to $RESTIC_REPOSITORY"
restic unlock                     # clears a lock left behind by a run that was killed
# --host is fixed so snapshots from a rebuilt server continue the same history.
restic backup --host edutrack --tag nightly "$STAGING"
restic forget --host edutrack --group-by host \
    --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY"

# ── 5. Weekly housekeeping ───────────────────────────────────────────────────
if [[ $(date +%u) == 7 ]]; then
    log "5/5 Sunday: freeing space from forgotten snapshots and test-reading 10% of the data"
    restic prune
    restic check --read-data-subset=10%
else
    log "5/5 Housekeeping runs on Sundays"
fi

log "Backup finished."
ping_health ""
