#!/usr/bin/env bash
#
# edutrack-deploy — put the newest code from GitHub live.
#
# Does what "Updating the App After Code Changes" in DEPLOYMENT.md does by
# hand, plus the steps that are easy to forget:
#   * it refuses to run over files someone edited by hand on the server,
#   * it saves a copy of the database before migrating,
#   * it checks the site answers afterwards, and tells you how to go back if not.
#
#     sudo edutrack-deploy
#
# Guide: ops/guides/03-deploying.md

set -Eeuo pipefail
umask 022    # static files must stay readable by nginx

CONFIG=${EDUTRACK_OPS_CONFIG:-/etc/edutrack/ops.env}
if [[ -r $CONFIG ]]; then
    set -a
    # shellcheck source=SCRIPTDIR/ops.env.example
    source "$CONFIG"
    set +a
fi

: "${REPO_DIR:=/var/www/edutrack}" "${APP_USER:=edutrack}" "${SERVICE:=edutrack}" "${BRANCH:=master}"
: "${DB_NAME:=edutrack_db}" "${BACKUP_ROOT:=/var/backups/edutrack}" "${PRE_DEPLOY_KEEP:=10}"
: "${APP_URL:=http://127.0.0.1:8000/login/}"
: "${POSTGRES_RUN_AS=postgres}"

BACKEND=$REPO_DIR/backend

log() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

as_app() { runuser -u "$APP_USER" -- "$@"; }
as_postgres() {
    if [[ -z $POSTGRES_RUN_AS || $(id -un) == "$POSTGRES_RUN_AS" ]]; then "$@"
    else runuser -u "$POSTGRES_RUN_AS" -- "$@"
    fi
}

check_site() {
    local host code
    host=$(sed -n 's/^ALLOWED_HOSTS=//p' "$BACKEND/.env" 2>/dev/null | cut -d, -f1 | tr -d " \"'")
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

rollback_help=
on_exit() {
    local status=$?
    if (( status != 0 )) && [[ -n $rollback_help ]]; then
        printf '\n%s\n' "$rollback_help" >&2
    fi
}
trap on_exit EXIT

[[ $EUID -eq 0 ]] || die "run it with sudo:  sudo edutrack-deploy"
[[ $(stat -c %U "$REPO_DIR") == "$APP_USER" ]] \
    || die "$REPO_DIR should belong to $APP_USER. Fix with:  sudo chown -R $APP_USER: $REPO_DIR"
cd "$REPO_DIR"

# ── Is the server's copy of the code exactly what is in git? ─────────────────
edited=$(as_app git status --porcelain --untracked-files=no)
if [[ -n $edited ]]; then
    cat >&2 <<EOF
These files were changed by hand on the server, so they differ from GitHub:

$edited

Deploying over them would either fail or throw the changes away. See what changed:

    sudo -u $APP_USER git -C $REPO_DIR diff

If the change belongs in the project, make it on your laptop and push it. Then
throw the server's copy away and deploy again:

    sudo -u $APP_USER git -C $REPO_DIR checkout -- <file>
    sudo edutrack-deploy

(ops/guides/03-deploying.md, "The server has hand edits")
EOF
    exit 1
fi

log "Fetching $BRANCH from GitHub"
as_app git fetch --quiet origin "$BRANCH"
old=$(as_app git rev-parse HEAD)
new=$(as_app git rev-parse "origin/$BRANCH")
if [[ $old == "$new" ]]; then
    log "Already running the newest code (${old:0:7}). Nothing to do."
    exit 0
fi
log "Going from ${old:0:7} to ${new:0:7}:"
as_app git --no-pager log --oneline "$old..$new" | sed 's/^/    /'

# ── 1. Safety copy of the database ───────────────────────────────────────────
log "1/6 Saving the database before anything changes"
mkdir -p "$BACKUP_ROOT/pre-deploy"
chmod 700 "$BACKUP_ROOT"
dump=$BACKUP_ROOT/pre-deploy/$DB_NAME-$(date +%Y-%m-%d_%H%M)-code-${old:0:7}.dump
( umask 077; as_postgres pg_dump --format=custom "$DB_NAME" > "$dump.partial" )
pg_restore --list "$dump.partial" > /dev/null || die "the safety dump is unreadable — not deploying"
mv "$dump.partial" "$dump"
log "    $dump"
find "$BACKUP_ROOT/pre-deploy" -maxdepth 1 -name '*.dump' -printf '%T@ %p\n' | sort -rn \
    | tail -n +"$((PRE_DEPLOY_KEEP + 1))" | cut -d' ' -f2- | xargs -r rm --

rollback_help="DEPLOY STOPPED. To go back to how things were before this deploy:

    sudo -u $APP_USER git -C $REPO_DIR reset --hard ${old:0:7}
    sudo edutrack-restore $dump        # also restarts the app

(ops/guides/03-deploying.md, \"Going back\")"

# ── 2–6. The update ──────────────────────────────────────────────────────────
log "2/6 Getting the new code"
as_app git merge --quiet --ff-only "origin/$BRANCH"

cd "$BACKEND"
log "3/6 Installing Python packages"
as_app venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

log "4/6 Checking the project and updating the database structure"
as_app venv/bin/python manage.py check
as_app venv/bin/python manage.py migrate --noinput

log "5/6 Collecting static files"
as_app venv/bin/python manage.py collectstatic --noinput --verbosity 0

log "6/6 Restarting $SERVICE"
systemctl restart "$SERVICE"
check_site || die "the site did not answer at $APP_URL — look at: sudo journalctl -u $SERVICE -n 50"

rollback_help=
log "Deployed ${new:0:7}. The database copy from before is kept at $dump"
