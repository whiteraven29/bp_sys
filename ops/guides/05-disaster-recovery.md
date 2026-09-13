# 05 — When something goes wrong

Read the table, go to your situation, and follow the steps in order. Don't skip
the checks: under pressure, most mistakes come from rushing.

| What happened | Go to | Data lost |
|---|---|---|
| A deploy failed or broke the site | [A. A bad update](#a-a-bad-update) | Nothing, if you go back straight away |
| Someone deleted or changed the wrong records | [B. Wrong data](#b-wrong-data) | Depends on how you fix it |
| The server is running but its data is damaged | [C. Restore on the same server](#c-restore-on-the-same-server) | Everything since last night's backup |
| The server is dead, or the Contabo account is gone | [D. Rebuild on a new server](#d-rebuild-on-a-new-server) | Everything since last night's backup |
| Planned move to a new server or provider | [E. Moving servers on purpose](#e-moving-servers-on-purpose) | Nothing |
| You restored the wrong backup | [Undo a restore](#undo-a-restore) | Nothing |

**Before anything else**, if the server still runs, save what it has now. Even
damaged data may be needed later:

```
$ sudo edutrack-backup
```

---

## A. A bad update

If `edutrack-deploy` stopped, it printed the commands to go back. Follow
[guide 03, "Going back"](03-deploying.md#going-back).

If the deploy *succeeded* but the site misbehaves afterwards, the database copy
from before is in `/var/backups/edutrack/pre-deploy/`. The file name ends in the
commit that was running before, for example `…-code-70f87e0.dump`:

```
$ sudo ls -lt /var/backups/edutrack/pre-deploy/ | head -3
$ sudo -u edutrack git -C /var/www/edutrack reset --hard 70f87e0
$ sudo edutrack-restore /var/backups/edutrack/pre-deploy/edutrack_db-…-code-70f87e0.dump
```

## B. Wrong data

Example: a tutor deleted a module's CAT marks this afternoon, and nobody noticed
until now.

**Restoring everything is usually the wrong answer here.** It would bring back the
marks, but it would also erase everything *everyone else* entered since the
backup: attendance, payments, requests.

Instead, bring the backup up *next to* the live database and copy back only what's missing:

```
$ sudo edutrack-restore --drill --keep /var/backups/edutrack/daily/edutrack_db-2026-10-13_0231.dump
$ sudo -u postgres psql edutrack_restore_drill
```

Pick the newest dump from *before* the mistake (`sudo ls -lt /var/backups/edutrack/daily/`).
`--keep` leaves the restored copy in a separate database called
`edutrack_restore_drill`. The live site isn't touched. Find the missing rows there
and copy them back. Get a developer's help for this part, because rows are
connected (a result belongs to an enrollment, which belongs to a student). When
finished:

```
$ sudo -u postgres dropdb edutrack_restore_drill
```

If the mistake is older than the 7 local dumps, get an older one from Google Drive
first. Section C, steps 1–2, shows how.

If only a little was entered since the backup and everyone agrees to re-enter it,
a full restore (section C) is simpler.

## C. Restore on the same server

The server works, but the data is damaged (for example, a disk error, or a
migration that damaged tables without anyone noticing for days).

**1. Choose a backup:**

```
$ sudo edutrack-restic snapshots
```

The newest is at the bottom. If the damage happened at a known time, pick the
last snapshot from before it and note its ID (for example `1a2b3c4d`).

**2. Download it:**

```
$ sudo edutrack-restic restore 1a2b3c4d --target /root/recovery
$ sudo find /root/recovery -name MANIFEST.txt -exec cat {} \;
```

Check the `taken:` date is the one you meant. Use `latest` instead of an ID for the newest.

**3. Restore it:**

```
$ sudo edutrack-restore /root/recovery
```

Read what it says it will replace, then type `RESTORE`. It:

1. saves the current database to `/var/backups/edutrack/pre-restore/`,
2. stops the site,
3. restores the database and checks every table's row count against the backup's record,
4. moves the current `media/` aside and puts the backup's files in its place,
5. runs `migrate`, starts the site, and checks it answers.

**4. Check in a browser:** log in as admin, open recent attendance, and open an
uploaded letter.

**5. Clean up.** The folder holds readable personal data:

```
$ sudo rm -rf /root/recovery
```

## D. Rebuild on a new server

The server is gone. You need the emergency kit from [ops/README.md](../README.md):
the restic password, the backup Google account, Contabo, DNS, GitHub and your laptop.

**Plan for 2–3 hours.** Tell the college the system is down and when you expect it back.

### 1. Order a new server

In Contabo, order a VPS with **Ubuntu 24.04** (the same version as before, or
newer; never older).

**Give it your SSH public key while ordering.** Contabo lets you add one for the
root account during ordering (or reinstalling). Paste the output of this, from the
laptop:

```
$ cat ~/.ssh/id_ed25519.pub
```

Note the new IP address, and check your key gets you in: `ssh root@NEW-IP`.

> **If you forgot the key**, a fresh Contabo server usually still accepts the root
> password Contabo emailed you. Use it just once to install your key:
> `ssh-copy-id root@NEW-IP`. Step 4 then switches password logins off, the same
> as on the old server.

### 2. Get the newest backup onto the laptop

**laptop:**

```
$ restic -r rclone:gdrive:edutrack-backups snapshots
$ restic -r rclone:gdrive:edutrack-backups restore latest --target ~/edutrack-recovery
$ find ~/edutrack-recovery -name MANIFEST.txt -exec cat {} \;
```

If Google Drive is unavailable, use the laptop's own copy from the monthly drill:
replace `rclone:gdrive:edutrack-backups` with `~/edutrack-backups`.

### 3. Take the secrets out of it

**laptop:**

```
$ mkdir -p ~/edutrack-secrets
$ cp "$(find ~/edutrack-recovery -name env -path '*staging*')" ~/edutrack-secrets/env
$ chmod 600 ~/edutrack-secrets/env
```

This is the old server's `.env`, with the same `SECRET_KEY` and database password.
Ansible copies it to the new server.

### 4. Build the server with Ansible

**laptop:** in `ops/ansible/inventory.ini`, set `ansible_host` to the **new** IP and
`ansible_user=root`. If your domain still points at the old (dead) server, also
set `enable_https=false` for now.

```
$ cd ~/Documents/BPHACOH/bp_sys/ops/ansible
$ ansible edutrack -m ping
$ ansible-playbook site.yml
```

It switches password logins off and the firewall on, installs everything, clones
the code, copies `.env`, creates the database user and an empty database, and
starts the site. At the end it tells you backups are not connected yet. That's
expected, and comes in step 9.

> SSH may warn `REMOTE HOST IDENTIFICATION HAS CHANGED` if the new server got an
> IP you used before. Remove the old entry: `ssh-keygen -R NEW-IP`.

### 5. Put the data back

**laptop:** send the backup to the new server:

```
$ rsync -a ~/edutrack-recovery/ root@NEW-IP:/root/recovery/
```

**server:**

```
$ sudo edutrack-restore /root/recovery
```

Type `RESTORE`. Replacing the empty database is exactly what you want here. The
row counts must show `0 missing`.

### 6. Point the domain at the new server

Wherever your domain's DNS is managed, change the **A record** for the domain (and
`www`) to the new IP. **laptop:** wait until this shows the new IP (minutes to a
few hours):

```
$ dig +short yourdomain.com
```

### 7. Switch HTTPS back on

**laptop:** set `enable_https=true` in `inventory.ini`, then:

```
$ ansible-playbook site.yml
```

Certbot fetches a new certificate. Do steps 6 and 7 back to back: browsers that
visited the site before insist on HTTPS, so the site is unreachable for them
until the certificate exists.

### 8. Check everything

- The site opens over `https://`, and you can log in as admin.
- Recent attendance and marks are there, up to the backup's `taken:` time.
- A student can download an uploaded letter (this proves `media/` came back).
- Tell staff: anything entered after the backup's `taken:` time must be entered again.

### 9. Switch backups back on

The new server has no connection to Google Drive yet. **server:**

1. Guide 01, **step 5**: connect rclone to the **same** backup Google account.
2. Guide 01, **step 6**, but with the **existing** password, not a new one:
   ```
   $ sudo install -m 600 /dev/null /etc/edutrack/restic-password
   $ sudo nano /etc/edutrack/restic-password
   ```
   Paste the password from your password manager, then save.
3. **Skip `restic init`.** The repository already exists, with all your history.
   Check the server can read it: `sudo edutrack-restic snapshots`
4. **laptop:** run `ansible-playbook site.yml` once more. It sees Drive is
   connected now and switches on the nightly timer.
5. `sudo edutrack-backup`. It should finish with `Backup finished.`

### 10. Clean up

```
$ sudo rm -rf /root/recovery                           # server
$ rm -rf ~/edutrack-recovery ~/edutrack-secrets        # laptop
```

Both hold readable personal data and secrets, and both are in the encrypted backups anyway.

Cancel the old server at Contabo only after a week of the new one working.

## E. Moving servers on purpose

The same as D, with two differences that mean nothing is lost:

**Before D step 2**, on the **old** server, outside school hours:

```
$ sudo systemctl stop edutrack        # nobody can enter anything from now on
$ sudo edutrack-backup                # so this is the final state
```

**During D step 4**, leave `enable_https=false` until DNS points at the new
server, as described there.

You can rehearse steps 1–5 of D at any time on a cheap temporary VPS, without
touching the domain or stopping the live site. Skip the `systemctl stop` above for
a rehearsal. You can't browse to the rehearsal server, because Django only answers
to your domain and insists on HTTPS. But `edutrack-restore` checks the row counts
and checks that the site answers, and that is what the rehearsal needs to prove.
Delete the temporary VPS afterwards. A rehearsal once a year is the best proof
that section D will work on a bad day.

---

## Undo a restore

Every real restore first saves what was live into `/var/backups/edutrack/pre-restore/`:

```
$ sudo ls -lt /var/backups/edutrack/pre-restore/
edutrack_db-2026-10-13_142205.dump      ← the database as it was
media-2026-10-13_142205                 ← the uploaded files as they were
```

To put them back:

```
$ sudo edutrack-restore /var/backups/edutrack/pre-restore/edutrack_db-2026-10-13_142205.dump
$ sudo mv /var/www/edutrack/backend/media /var/backups/edutrack/pre-restore/media-restored-by-mistake
$ sudo mv /var/backups/edutrack/pre-restore/media-2026-10-13_142205 /var/www/edutrack/backend/media
```

If a restore **stopped part-way**, it printed `STOPPED PART-WAY. The app is not running`.
Start the site with the steps above, then read the error it printed before
that message and fix that first.

Once everything is confirmed fine, free the space: `sudo rm -rf /var/backups/edutrack/pre-restore/*`
