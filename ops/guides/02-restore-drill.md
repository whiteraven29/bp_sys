# 02 — The monthly restore drill

**Time:** 15 minutes a month (plus 15 minutes the first time). **Where:** your laptop.

A backup you've never restored is a hope, not a backup. Backups fail quietly:
a password saved wrong, a folder left out, a job that stopped months ago. You only
find out when you try to restore. The drill makes sure you find out on a quiet
afternoon, not on the day the server dies.

The drill also leaves you with the third copy: an encrypted copy of all backups
on your laptop, which doesn't depend on Google or Contabo.

What the drill proves, each month:

- the password in your password manager opens the backups,
- the newest backup is from last night (so the nightly job is running),
- the database in it restores without a single error,
- every table has as many rows as the server recorded when it made the backup,
- the uploaded files and `.env` are in it.

It never touches your own databases. It restores into one called
`edutrack_restore_drill` and deletes that at the end.

---

## First time only: set up the laptop

**1. Install restic** (you already have rclone and PostgreSQL):

```
$ sudo apt install -y restic
```

**2. Connect the laptop's rclone to the backup Google account.** This is like
guide 01 step 5, but simpler, because the laptop has a browser:

```
$ rclone config
```

| rclone asks | You type |
|---|---|
| `n/s/q>` (or `e/n/d/r/c/s/q>` if you have other remotes) | `n` |
| `name>` | `gdrive` (if you already have a remote with that name, use `edutrack-gdrive` and use that name below) |
| `Storage>` | `drive` |
| `client_id>`, `client_secret>` | *(Enter)*, *(Enter)* |
| `scope>` | `1` |
| `service_account_file>` | *(Enter)* |
| `Edit advanced config?` | `n` |
| `Use web browser to automatically authenticate?` | **`y`**, then sign in with the **backup** account |
| `Shared Drive?` | `n` |
| `Keep this remote?` | `y`, then `q` |

```
$ rclone lsd gdrive:
```

It should list `edutrack-backups`.

**3. Make the laptop's own encrypted copy of the repository:**

```
$ restic -r ~/edutrack-backups init --from-repo rclone:gdrive:edutrack-backups --copy-chunker-params
```

It asks for the **source** repository's password (paste the one from your
password manager). Then it asks twice for a password for the **new** one: use the
same password, so there is only one to remember. `--copy-chunker-params` makes
the laptop copy split data the same way as the Google Drive copy, so monthly
copies stay small.

---

## Every month

**1. Copy the new backups down to the laptop:**

```
$ restic -r ~/edutrack-backups copy --from-repo rclone:gdrive:edutrack-backups
$ restic -r ~/edutrack-backups snapshots
```

`copy` downloads only snapshots the laptop doesn't have yet. `snapshots` lists
them. The newest should be from last night. Each has an ID like `1a2b3c4d`, and
`latest` always means the newest.

**2. Unpack the newest one into a temporary folder:**

```
$ restic -r ~/edutrack-backups restore latest --target ~/edutrack-drill
```

restic recreates the full server path inside that folder, so the files end up in
`~/edutrack-drill/var/backups/edutrack/staging/`. The drill script finds them itself.

**3. Run the drill:**

```
$ cd ~/Documents/BPHACOH/bp_sys
$ ops/backup/restore.sh --drill ~/edutrack-drill
```

It asks for your laptop `sudo` password, because it works with PostgreSQL as the
`postgres` account. A healthy result looks like this (the date and time at the
start of each line are left out here):

```
    EduTrack backup
    taken:            2026-10-13T02:31:07+03:00
    ...
  Practice restore into the throw-away database edutrack_restore_drill (nothing else is touched)
  1/4 Restoring the database
NOTICE:  database "edutrack_restore_drill" does not exist, skipping
  2/4 Comparing every table with the backup's record
    63 tables, 18412 rows in the backup record: 0 missing, 0 with a different count
  3/4 Checking the uploaded files and settings
    41 uploaded files, as recorded
    .env is in the backup
  4/4 How old is this backup?
    taken 2026-10-13T02:31:07+03:00 (0 days ago)
  DRILL PASSED — this backup can be restored.
```

The `NOTICE` line is normal: the script is only making sure no old drill database is left over.

**4. Delete the unpacked folder.** It is *not* encrypted: it holds students'
records and the server's secrets in readable form.

```
$ rm -rf ~/edutrack-drill
```

**5. Write it down.** Keep a line somewhere, for example
`2026-10-13  drill passed, 63 tables, 18412 rows, 41 files`. Watching those numbers
grow month by month is itself a check.

---

## Reading the result

| You see | It means | Do |
|---|---|---|
| `DRILL PASSED` | This backup restores | Nothing |
| `DIFFERENT django_session: 120 saved, 118 restored` | The server counted rows a moment after the dump, and someone logged in or out in between | Nothing, if only busy tables (sessions, notifications) differ by a few rows |
| `DIFFERENT` on attendance, results, payments… by a lot | The dump and the record don't agree | Take a fresh backup (`sudo edutrack-backup`) and drill again; if it repeats, investigate |
| `MISSING <table>` | A table is in the record but not in the dump | **Serious.** Investigate before trusting any backup |
| `MISSING uploaded files` | Files weren't copied into the backup | **Serious.** Check `backend/media` permissions on the server |
| `WARNING nightly backups should never be more than a day old` | The nightly job hasn't run | On the server: `journalctl -u edutrack-backup -n 80` and `systemctl list-timers` |
| `DRILL FAILED: this backup could not be restored` | pg_restore hit an error | Read the error above it. Try the previous snapshot: `restore <ID>` instead of `restore latest` |
| `wrong password or no key found` | The saved password is wrong | Find the right one **now**, while the server still exists. See guide 01 step 6 |

## Looking inside a backup (optional)

To see real data in the restored database, keep it instead of deleting it:

```
$ ops/backup/restore.sh --drill --keep ~/edutrack-drill
$ sudo -u postgres psql edutrack_restore_drill
edutrack_restore_drill=# SELECT count(*) FROM attendance_studentprofile;
edutrack_restore_drill=# \q
$ sudo -u postgres dropdb edutrack_restore_drill
```

## Keeping the laptop copy tidy (once a term)

The laptop copy keeps every snapshot it ever copied. Thin it out, keeping more
history than the server does:

```
$ restic -r ~/edutrack-backups forget --keep-monthly 24 --keep-weekly 8 --prune
```
