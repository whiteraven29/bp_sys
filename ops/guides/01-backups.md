# 01 — Setting up backups

**Time:** about an hour, once. **Cost:** nothing.
**You need:** SSH access to the server with `sudo`, your laptop, and a password manager (or paper).

By the end, every night at 02:30 the server will:

1. dump the database into `/var/backups/edutrack/daily/` (the last 7 are kept), and
2. encrypt the database, the uploaded files and `.env`, and send them to Google Drive,
   keeping 7 daily, 4 weekly and 12 monthly copies.

In this guide, `$` at the start of a line means "type this". Commands marked
**laptop** run on your laptop; everything else runs **on the server**.

---

## The tools, in one paragraph each

**`pg_dump`** comes with PostgreSQL. It writes the whole database to one file
while the site keeps running, and every table in that file is from the same
moment. That is why you dump rather than copying PostgreSQL's data folder:
copying the folder while the database runs gives you a broken copy.

**`restic`** is the backup program. It encrypts everything with your password
before anything leaves the server, so Google only holds scrambled files. It stores
each piece of data only once, so the 300th nightly backup uploads only what
changed. It also deletes old backups by a rule you give it ("keep 7 daily, 4
weekly, 12 monthly"). Its storage place is called a **repository**, and one
backup in it is called a **snapshot**.

**`rclone`** connects programs to cloud storage. restic uses it to reach Google Drive.

**`systemd` timers** are the modern Linux way to run something on a schedule, like
cron. Two files work together: a **`.service`** says *what* to run, and a
**`.timer`** says *when*.

---

## Step 1 — Look at the server first

Log in with your SSH key, as you always do. Wherever this guide says
`YOU@YOUR-SERVER-IP`, use your usual login (for example `root@…`):

```
$ ssh YOU@YOUR-SERVER-IP
$ lsb_release -ds
$ df -h /
$ sudo -u postgres psql -c 'SHOW server_version'
$ ls -l /var/www/edutrack/backend/.env
$ stat -c '%U' /var/www/edutrack
```

What you should see, and why each one matters:

- **`lsb_release`**: your Contabo kernel (`6.8.0-…-generic #…-Ubuntu`) belongs to
  **Ubuntu 24.04**, although `DEPLOYMENT.md` says 22.04. Write down what it really
  says. A backup can only be restored into the same or a *newer* PostgreSQL, so a
  replacement server must be the same Ubuntu version or newer.
- **`df -h /`**: free disk space. You need room for 7 dumps plus a copy of
  `media/`. A few GB free is plenty for now.
- **`SHOW server_version`**: the PostgreSQL version (16 on Ubuntu 24.04).
- **`.env`** must exist. It holds the secrets that get backed up.
- **`stat`** must print `edutrack`, the account that owns the code. If it prints
  `root`, fix it now, because the deploy script refuses to run otherwise:
  `sudo chown -R edutrack: /var/www/edutrack`

## Step 2 — Get this `ops/` folder onto the server

The scripts live in git, so the server gets them with `git pull`.

**laptop:** commit and push the `ops/` folder first:

```
$ cd ~/Documents/BPHACOH/bp_sys
$ git add ops .gitignore backend/requirements.txt
$ git commit -m "ops: backups, restore drill, deploy script, ansible playbook"
$ git push origin master
```

**server:** check whether anyone edited files by hand:

```
$ cd /var/www/edutrack
$ sudo -u edutrack git status --short
```

- **Nothing printed**: good, go on to the pull below.
- **` M backend/edutrack/settings.py`**: this is Step 8 of `DEPLOYMENT.md`, which
  added WhiteNoise by hand. Look at the change:

  ```
  $ sudo -u edutrack git diff backend/edutrack/settings.py
  ```

  If the only changes are the `whitenoise` middleware line and
  `STATICFILES_STORAGE`, throw them away. They aren't needed: nginx already
  serves `/static/` itself (the `location /static/` block), so WhiteNoise never
  handles those requests.

  ```
  $ sudo -u edutrack git checkout -- backend/edutrack/settings.py
  ```

  If the diff shows anything else, stop and copy that change into the project on
  your laptop first, so it isn't lost.

Now pull and restart:

```
$ sudo -u edutrack git pull origin master
$ sudo systemctl restart edutrack
$ curl -sI https://YOURDOMAIN/static/admin/css/base.css | head -1
```

The last command should print `HTTP/1.1 200 OK` (or `HTTP/2 200`). That proves
static files still load without WhiteNoise.

## Step 3 — Install restic and rclone

```
$ sudo apt update
$ sudo apt install -y restic rclone
$ restic version
$ rclone version | head -1
```

Any restic from 0.14 on and any rclone from 1.58 on is fine. Ubuntu 24.04 ships newer versions than that.

## Step 4 — A Google account only for backups

If you already created one, just check the points below. Otherwise, create a new
free Gmail account in a browser, for example `bphacoh.backups@gmail.com` (pick
any free name).

- **Why not a personal account?** The backups belong to the college, not to one person. If
  you leave, lose your phone, or your personal account is locked, the college
  must still reach its backups.
- Turn on **2-Step Verification** for it.
- Save the email and password in your password manager. This login is part of
  the emergency kit (`ops/README.md`).
- 15 GB is free. EduTrack's backups will use a tiny part of that for years.

## Step 5 — Connect the server to Google Drive

Signing in to Google needs a web browser, and the server has none. So you start
on the server, sign in on the laptop, and paste the result back into the server.

**server:** make a private folder for the settings, then start rclone's setup:

```
$ sudo mkdir -p /etc/edutrack
$ sudo chmod 700 /etc/edutrack
$ sudo rclone config --config /etc/edutrack/rclone.conf
```

Answer the questions like this (the exact wording differs a little between versions):

| rclone asks | You type | Why |
|---|---|---|
| `n) New remote` … `n/s/q>` | `n` | |
| `name>` | `gdrive` | The scripts expect this name |
| `Storage>` | `drive` | Type the word; the numbers change between versions |
| `client_id>` | *(press Enter)* | Use rclone's own |
| `client_secret>` | *(press Enter)* | |
| `scope>` | `1` (Full access all files) | This account holds only backups, so full access loses nothing |
| `service_account_file>` | *(press Enter)* | |
| `Edit advanced config?` | `n` | |
| `Use web browser to automatically authenticate?` / `Use auto config?` | **`n`** | The server has no browser |

rclone now prints a command that looks like this:

```
rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlIn0"
```

**laptop:** copy that exact command and run it in a laptop terminal (rclone is
already installed there):

```
$ rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlIn0"
```

A browser opens. **Sign in with the backup account from step 4, not your own**,
then click *Allow*. The laptop terminal then prints a block of text between
`Paste the following into your remote machine --->` and `<---End paste`. Copy
everything between those two markers (it starts with `{` and ends with `}`).

**server:** paste it at the `config_token>` prompt, then:

| rclone asks | You type |
|---|---|
| `Configure this as a Shared Drive (Team Drive)?` | `n` |
| `Keep this "gdrive" remote?` | `y` |
| the menu | `q` |

Test it and lock the file down. It contains a key to the Google account:

```
$ sudo chmod 600 /etc/edutrack/rclone.conf
$ sudo rclone --config /etc/edutrack/rclone.conf mkdir gdrive:edutrack-backups
$ sudo rclone --config /etc/edutrack/rclone.conf lsd gdrive:
```

The last command should list `edutrack-backups`. You will also see that folder
at drive.google.com when signed in as the backup account.

> **If `rclone authorize` fails** because the laptop's rclone is much newer than
> the server's, run `rclone authorize "drive"` on the laptop without the long
> code. It produces the same kind of token.

## Step 6 — Create the encryption password

restic encrypts with this password. **If it is lost, the backups can never be
opened, by you, by Google, or by anyone.** There is no reset.

```
$ sudo install -m 600 /dev/null /etc/edutrack/restic-password
$ openssl rand -base64 33 | sudo tee /etc/edutrack/restic-password > /dev/null
$ sudo cat /etc/edutrack/restic-password
```

The first line creates an empty file only root can read. The second fills it
with 44 random characters, and `tee` keeps the file's private permissions. The
third shows the password to you.

Now, before going further:

1. Save it in your password manager as **"EduTrack restic backup password"**.
2. Print it or write it down, put it in a sealed envelope, and give it to the
   principal or put it in the college safe.

Why both? The password manager is what you'll use day to day. The envelope is
for the day your laptop is also gone.

## Step 7 — Create the settings file

```
$ sudo install -m 600 /var/www/edutrack/ops/ops.env.example /etc/edutrack/ops.env
$ sudo grep -E '^DB_(NAME|USER)=' /var/www/edutrack/backend/.env
$ sudo nano /etc/edutrack/ops.env
```

The `grep` shows the real database name and user without showing the password.
In `nano`, check that `DB_NAME` and `DB_USER` match them, and that `REPO_DIR`,
`APP_USER` and `SERVICE` match your server (the defaults match `DEPLOYMENT.md`).
Save with `Ctrl+O`, then `Enter`, and quit with `Ctrl+X`.

## Step 8 — Create the backup repository in Google Drive

First install the four commands, so you can use `edutrack-restic` here:

```
$ cd /var/www/edutrack/ops
$ sudo install -o root -g root -m 700 backup/backup.sh  /usr/local/sbin/edutrack-backup
$ sudo install -o root -g root -m 700 backup/restore.sh /usr/local/sbin/edutrack-restore
$ sudo install -o root -g root -m 700 backup/restic.sh  /usr/local/sbin/edutrack-restic
$ sudo install -o root -g root -m 700 deploy.sh         /usr/local/sbin/edutrack-deploy
```

> **Why copy them to `/usr/local/sbin` instead of running them from the repository?**
> These commands run as root. `/var/www/edutrack` belongs to the `edutrack`
> account, the same account the website runs as. If a bug in the website ever
> let an attacker change files there, a root script inside it would hand them
> the whole server. Copies owned by root, which only root can change, close that
> door. The cost: when a script changes in git, run these `install` lines again.

Now create the repository:

```
$ sudo edutrack-restic init
```

You should see `created restic repository … at rclone:gdrive:edutrack-backups`,
followed by a warning that losing the password means losing the data. That is
step 6.

**Only ever run `init` once.** On a rebuilt server, you connect to the repository
that already exists (guide 05).

## Step 9 — Run the first backup by hand

```
$ sudo edutrack-backup
```

It prints five numbered steps. The dump, the file list (`MANIFEST.txt`), then
restic saying something like `snapshot 1a2b3c4d saved`, and finally `Backup finished.`

Look at what it made:

```
$ sudo ls -lh /var/backups/edutrack/daily/
$ sudo cat /var/backups/edutrack/staging/MANIFEST.txt
$ sudo edutrack-restic snapshots
```

Then open drive.google.com as the backup account. In `edutrack-backups` you'll
find folders called `data`, `index`, `keys` and `snapshots`, full of files with
random names. Try opening one: it's unreadable. **That is the encryption working.**

## Step 10 — Run it every night

```
$ sudo cp /var/www/edutrack/ops/backup/edutrack-backup.service /etc/systemd/system/
$ sudo cp /var/www/edutrack/ops/backup/edutrack-backup.timer   /etc/systemd/system/
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now edutrack-backup.timer
$ systemctl list-timers edutrack-backup.timer
```

- `daemon-reload` makes systemd read the new files.
- `enable --now` starts the timer now *and* after every reboot.
- `list-timers` shows when the next run is (02:30 Tanzania time, give or take 10 minutes).

Test the scheduled version once, the way the timer will run it:

```
$ sudo systemctl start edutrack-backup.service
$ journalctl -u edutrack-backup -n 40 --no-pager
```

`journalctl` is where the nightly output goes. The last line should be `Backup finished.`

## Step 11 — Get an email when a backup does NOT happen (optional, free)

A backup that silently stopped three months ago is the most common way backups
fail. healthchecks.io watches for this for free: the backup pings it every
night, and if the ping doesn't arrive, it emails you.

1. Sign up at https://healthchecks.io (the free plan is enough).
2. **Add Check**: set *Period* to `1 day` and *Grace* to `2 hours`.
3. Copy its ping URL (`https://hc-ping.com/…`).
4. Paste it into `/etc/edutrack/ops.env` as `HEALTHCHECK_URL=https://hc-ping.com/…`
5. `sudo edutrack-backup`. The check turns green on the website.

From now on, a failed or missing backup emails you, and the page shows each run.

## Step 12 — Protect your way in: the SSH key

Password logins are switched off on the server, which is good security. It also
means **your SSH private key is the only way in.** If the laptop is stolen or its
disk dies, nobody can log in to the live server.

The backups don't depend on the key: guide 05 rebuilds everything on a new server
with a new key. But you'd be rebuilding a server that still works, just because
you can't get in. Two cheap habits prevent that:

**1. Give the key a passphrase**, so a stolen laptop doesn't open the server:

**laptop:**

```
$ ssh-keygen -p -f ~/.ssh/id_ed25519
```

(If your key file has another name, use that. `ls ~/.ssh` shows it. If it asks
for an old passphrase and there isn't one, press Enter.)

**2. Make a spare key, kept offline**, for example on a USB stick in the sealed
envelope with the restic password (step 6):

**laptop:**

```
$ ssh-keygen -t ed25519 -f ~/edutrack-spare-key -C "edutrack spare key"
$ ssh-copy-id -f -i ~/edutrack-spare-key.pub YOU@YOUR-SERVER-IP
$ ssh -i ~/edutrack-spare-key YOU@YOUR-SERVER-IP 'echo the spare key works'
```

Give it a strong passphrase when `ssh-keygen` asks. `ssh-copy-id` logs in with
your *normal* key to install the spare one, so it works even though password
logins are off. Then move both files, `~/edutrack-spare-key` and
`~/edutrack-spare-key.pub`, to the USB stick, and delete them from the laptop.

**If every key is lost:** the Contabo control panel can start the server in its
*Rescue System*, or reinstall it with a new key. Reinstalling erases the disk, and
guide 05 section D rebuilds EduTrack on it from the backups.

### Before a risky change, without snapshots

You don't use Contabo snapshots, so each risky change gets its own undo:

| Before | Undo you already have |
|---|---|
| Deploying new code | `edutrack-deploy` copies the database first (guide 03) |
| Running the Ansible playbook | It keeps a dated copy of each config file it replaces, next to the file (guide 04) |
| Anything else: an Ubuntu upgrade, editing data by hand, a big import | Run `sudo edutrack-backup` right before, so the newest backup is minutes old, not hours |

## Step 13 — Your routine

| When | What | How long |
|---|---|---|
| Every day | Nothing. If you did step 11, an email arrives only when something is wrong. | 0 |
| Every week | `journalctl -u edutrack-backup --since "7 days ago" \| grep -E "Backup finished\|FAILED"` shows 7 × `Backup finished` | 1 minute |
| After a big marks-entry day | `sudo edutrack-backup`, so that day isn't only in tonight's backup | 2 minutes |
| Every month | The restore drill: [02-restore-drill.md](02-restore-drill.md) | 15 minutes |
| Every term | `sudo edutrack-restic stats` and the storage page of the backup Google account | 2 minutes |
| When a script in `ops/` changes | Repeat the four `install` lines from step 8 | 1 minute |

---

## When something goes wrong

**`Fatal: unable to open config file` / `wrong password or no key found`**:
the password in `/etc/edutrack/restic-password` isn't the one the repository
was created with. Put back the one from your password manager.

**`couldn't fetch token` / `invalid_grant` / `401` from Google Drive**: the Google
sign-in expired or was removed (for example, the backup account's access list was
cleared). Sign in again: `sudo rclone config reconnect gdrive: --config /etc/edutrack/rclone.conf`,
then do the laptop part of step 5 again.

**`repository is already locked`**: an earlier run was killed halfway. The next
backup clears the lock itself. To clear it now: `sudo edutrack-restic unlock`.

**`pg_dump: error: connection … failed`**: PostgreSQL is not running. `sudo systemctl status postgresql`.

**The disk filled up**: `sudo du -sh /var/backups/edutrack/*`. Lower `LOCAL_KEEP` in `/etc/edutrack/ops.env`.
