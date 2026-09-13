#!/usr/bin/env bash
#
# edutrack-restore — put a backup back.
#
#   sudo edutrack-restore FOLDER
#       Full restore on the server: the database and the uploaded files.
#       FOLDER is where `edutrack-restic restore` (or restic on your laptop)
#       downloaded a snapshot to.
#
#   sudo edutrack-restore FILE.dump
#       The database only, from a single dump: a nightly one from
#       /var/backups/edutrack/daily, or the one edutrack-deploy took before it
#       migrated. This is the undo for a bad update.
#
#   ops/backup/restore.sh --drill FOLDER      (on your laptop)
#       Practice. Restores into a throw-away database called
#       edutrack_restore_drill, compares every table with the backup's own
#       record, checks the uploaded files, then deletes the throw-away database.
#       Add --keep to leave it there so you can look around in it.
#
# Before a real restore overwrites anything, the current database and media
# folder are set aside in $BACKUP_ROOT/pre-restore/, so restoring the wrong
# backup can itself be undone.
#
# Guides: ops/guides/02-restore-drill.md, ops/guides/05-disaster-recovery.md

set -Eeuo pipefail
umask 077

CONFIG=${EDUTRACK_OPS_CONFIG:-/etc/edutrack/ops.env}
if [[ -r $CONFIG ]]; then           # the server has one; a laptop drill doesn't need it
    set -a
    # shellcheck source=SCRIPTDIR/../ops.env.example
    source "$CONFIG"
    set +a
fi

: "${REPO_DIR:=/var/www/edutrack}" "${APP_USER:=edutrack}" "${SERVICE:=edutrack}"
: "${DB_NAME:=edutrack_db}" "${DB_USER:=edutrack_user}" "${BACKUP_ROOT:=/var/backups/edutrack}"
: "${APP_URL:=http://127.0.0.1:8000/login/}"
: "${POSTGRES_RUN_AS=postgres}"     # empty = connect as yourself (PGHOST, PGUSER …)

DRILL_DB=edutrack_restore_drill

COUNT_SQL="SELECT table_name,
       (xpath('/row/n/text()',
              query_to_xml(format('SELECT count(*) AS n FROM public.%I', table_name),
                           false, true, '')))[1]::text
  FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
 ORDER BY table_name"

log() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
usage() { sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; }

as_postgres() {
    if [[ -z $POSTGRES_RUN_AS || $(id -un) == "$POSTGRES_RUN_AS" ]]; then "$@"
    elif [[ $EUID -eq 0 ]]; then runuser -u "$POSTGRES_RUN_AS" -- "$@"
    else sudo -u "$POSTGRES_RUN_AS" -- "$@"
    fi
}
as_app() { runuser -u "$APP_USER" -- "$@"; }

# Printed if the script stops part-way, so you know what state it left behind.
failure_hint=
on_exit() {
    local status=$?
    if (( status != 0 )) && [[ -n $failure_hint ]]; then
        echo >&2
        log "$failure_hint" >&2
    fi
}
trap on_exit EXIT

manifest_value() {
    [[ -n $root && -f $root/MANIFEST.txt ]] || return 0
    sed -n "s/^$1: *//p" "$root/MANIFEST.txt"
}

# Compare the rows in database $1 with table-counts.tsv saved in the backup.
# Returns 0 when everything matches, 1 when only counts differ, 2 when a table is missing.
compare_counts() {
    local db=$1 expected=$root/table-counts.tsv
    if [[ -z $root || ! -f $expected ]]; then
        log "    no table-counts.tsv came with this backup, so there is nothing to compare"
        return 0
    fi
    as_postgres psql -XAtq -F $'\t' -d "$db" -c "$COUNT_SQL" \
    | awk -F'\t' '
        NR == FNR { saved[$1] = $2; next }
                  { restored[$1] = $2 }
        END {
            for (t in saved) {
                tables++; rows += saved[t]
                if (!(t in restored)) {
                    printf "    MISSING    %s (%d rows in the backup)\n", t, saved[t]; missing++
                } else if (restored[t] != saved[t]) {
                    printf "    DIFFERENT  %s: %d saved, %d restored\n", t, saved[t], restored[t]; different++
                }
            }
            printf "    %d tables, %d rows in the backup record: %d missing, %d with a different count\n",
                   tables, rows, missing, different
            exit (missing > 0 ? 2 : (different > 0 ? 1 : 0))
        }' "$expected" -
}

check_site() {
    local host code
    host=$(sed -n 's/^ALLOWED_HOSTS=//p' "$REPO_DIR/backend/.env" 2>/dev/null | cut -d, -f1 | tr -d " \"'")
    for _ in {1..15}; do
        code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${host:-localhost}" \
               -H 'X-Forwarded-Proto: https' "$APP_URL" || true)
        if [[ $code == [23]?? ]]; then
            log "    the site answers (HTTP $code)"
            return 0
        fi
        sleep 2
    done
    return 1
}

# ── What are we restoring from? ──────────────────────────────────────────────
mode=real keep=false assume_yes=false source_path=
while (( $# )); do
    case $1 in
        --drill) mode=drill ;;
        --keep)  keep=true ;;
        --yes)   assume_yes=true ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option $1 (try --help)" ;;
        *)  [[ -z $source_path ]] || die "give one folder or one dump file"
            source_path=$1 ;;
    esac
    shift
done
[[ -n $source_path ]] || { usage; exit 1; }

if [[ -d $source_path ]]; then
    dump=$(find "$source_path" -type f -name database.dump -print -quit)
    [[ -n $dump ]] || die "no database.dump inside $source_path — give the folder restic restored into"
    root=$(dirname "$dump")
elif [[ -f $source_path ]]; then
    dump=$source_path
    root=
else
    die "$source_path does not exist"
fi
pg_restore --list "$dump" > /dev/null 2>&1 || die "$dump is not a readable PostgreSQL dump"

if [[ -n $root && -f $root/MANIFEST.txt ]]; then
    sed 's/^/    /' "$root/MANIFEST.txt"
fi

# ── Practice run ─────────────────────────────────────────────────────────────
drill() {
    local result=0 saved found taken age

    [[ $DRILL_DB != "$DB_NAME" ]] || die "refusing to drill into the live database name"
    log "Practice restore into the throw-away database $DRILL_DB (nothing else is touched)"
    failure_hint="DRILL FAILED: this backup could not be restored. Keep the folder and find out why before relying on it."

    log "1/4 Restoring the database"
    as_postgres dropdb --if-exists "$DRILL_DB"
    as_postgres createdb "$DRILL_DB"
    as_postgres pg_restore --no-owner --no-privileges --exit-on-error --single-transaction \
        -d "$DRILL_DB" < "$dump"

    log "2/4 Comparing every table with the backup's record"
    compare_counts "$DRILL_DB" || result=$?

    log "3/4 Checking the uploaded files and settings"
    if [[ -n $root ]]; then
        saved=$(manifest_value 'media files')
        found=$(find "$root/media" -type f 2>/dev/null | wc -l)
        if [[ -n $saved && $saved != "$found" ]]; then
            log "    MISSING    uploaded files: the backup recorded $saved, the folder has $found"
            result=2
        else
            log "    $found uploaded files, as recorded"
        fi
        if [[ -f $root/env ]]; then
            log "    .env is in the backup"
        else
            log "    MISSING    .env is not in the backup"
            result=2
        fi
    else
        log "    a single dump file has no uploaded files or settings to check"
    fi

    log "4/4 How old is this backup?"
    taken=$(manifest_value taken)
    if [[ -n $taken ]]; then
        age=$(( ($(date +%s) - $(date -d "$taken" +%s)) / 86400 ))
        log "    taken $taken ($age days ago)"
        if (( age > 2 )); then
            log "    WARNING    nightly backups should never be more than a day old — are they running?"
            (( result > 0 )) || result=1
        fi
    fi

    failure_hint=
    if $keep; then
        log "The throw-away database is still there. Look inside with:  sudo -u postgres psql $DRILL_DB"
        log "Delete it afterwards with:  sudo -u postgres dropdb $DRILL_DB"
    else
        as_postgres dropdb "$DRILL_DB"
    fi

    case $result in
        0) log "DRILL PASSED — this backup can be restored." ;;
        1) log "DRILL PASSED WITH WARNINGS — read the lines above." ;;
        *) log "DRILL FAILED — read the lines above."; exit 1 ;;
    esac
}

# ── The real thing ───────────────────────────────────────────────────────────
real_restore() {
    local backend=$REPO_DIR/backend aside=$BACKUP_ROOT/pre-restore stamp answer
    local with_media=false saved_commit current_commit
    # To the second: a retry straight after a failed attempt must not overwrite
    # the copy of what was live before the first attempt.
    stamp=$(date +%Y-%m-%d_%H%M%S)
    [[ $EUID -eq 0 ]] || die "a real restore replaces live data; run it with sudo (or use --drill to practise)"
    if [[ -n $root && -d $root/media ]]; then with_media=true; fi

    echo
    echo "This REPLACES live data on $(hostname):"
    echo "    database        $DB_NAME  ←  $dump"
    if $with_media; then
        echo "    uploaded files  $backend/media  ←  $root/media"
    fi
    echo "Anything entered after that backup was taken disappears from the live system."
    echo "What is live now is set aside in $aside first, so this can be undone."
    echo
    if ! $assume_yes; then
        read -r -p "Type RESTORE to continue: " answer
        [[ $answer == RESTORE ]] || die "cancelled — nothing was changed"
    fi

    mkdir -p "$aside"
    chmod 700 "$BACKUP_ROOT"

    log "1/6 Setting the current database aside"
    if [[ $(as_postgres psql -XAtq -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'") == 1 ]]; then
        as_postgres pg_dump --format=custom "$DB_NAME" > "$aside/$DB_NAME-$stamp.dump"
        log "    $aside/$DB_NAME-$stamp.dump"
    else
        log "    there is no $DB_NAME database yet, nothing to set aside"
    fi

    log "2/6 Stopping the app so nobody writes while the data is swapped"
    systemctl stop "$SERVICE" || true
    failure_hint="STOPPED PART-WAY. The app is not running. What was live before is in $aside — see ops/guides/05-disaster-recovery.md, 'Undo a restore'."

    log "3/6 Restoring the database"
    as_postgres dropdb --if-exists --force "$DB_NAME"
    as_postgres createdb --owner="$DB_USER" "$DB_NAME"
    # --role makes every table belong to the app's database user, not to postgres.
    as_postgres pg_restore --no-owner --no-privileges --role="$DB_USER" \
        --exit-on-error --single-transaction -d "$DB_NAME" < "$dump"
    compare_counts "$DB_NAME" || true

    if $with_media; then
        log "4/6 Restoring uploaded files"
        if [[ -d $backend/media ]]; then
            mv "$backend/media" "$aside/media-$stamp"
            log "    the old folder is now $aside/media-$stamp"
        fi
        rsync -a "$root/media/" "$backend/media/"
        chown -R "$APP_USER:" "$backend/media"
    else
        log "4/6 Uploaded files left as they are (this restore is the database only)"
    fi
    if [[ -n $root && -f $root/env && ! -f $backend/.env ]]; then
        install -m 600 "$root/env" "$backend/.env"
        chown "$APP_USER:" "$backend/.env"
        log "    there was no backend/.env, so the one from the backup was put in place"
    fi

    log "5/6 Bringing the database structure up to the code's version"
    (cd "$backend" && as_app venv/bin/python manage.py migrate --noinput)

    log "6/6 Starting the app"
    systemctl start "$SERVICE"
    check_site || die "the app did not answer at $APP_URL — look at: sudo journalctl -u $SERVICE -n 50"
    failure_hint=

    saved_commit=$(manifest_value 'code commit')
    current_commit=$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || true)
    if [[ -n $saved_commit && $saved_commit != unknown && -n $current_commit ]] \
       && ! git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" merge-base --is-ancestor "$saved_commit" "$current_commit" 2>/dev/null; then
        log "NOTE: the backup was taken while the server ran commit ${saved_commit:0:7}, which is"
        log "      newer than (or unknown to) the code here, ${current_commit:0:7}. If pages misbehave,"
        log "      update the code:  sudo edutrack-deploy"
    fi
    log "Restore finished."
}

if [[ $mode == drill ]]; then drill; else real_restore; fi
