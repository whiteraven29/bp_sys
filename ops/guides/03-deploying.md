# 03 — Putting new code live

**Before:** guide 01 step 8 installed the `edutrack-deploy` command.

From now on, this replaces "Updating the App After Code Changes" in `DEPLOYMENT.md`.

## Deploying

**laptop:** run the tests, commit and push as usual:

```
$ git push origin master
```

**server:**

```
$ sudo edutrack-deploy
```

That's all. A normal run looks like this:

```
  Fetching master from GitHub
  Going from 70f87e0 to 9390ec0:
    9390ec0 feat: hostel request form
  1/6 Saving the database before anything changes
      /var/backups/edutrack/pre-deploy/edutrack_db-2026-10-02_1412-code-70f87e0.dump
  2/6 Getting the new code
  3/6 Installing Python packages
  4/6 Checking the project and updating the database structure
  Operations to perform: ...
  5/6 Collecting static files
  6/6 Restarting edutrack
      the site answers (HTTP 200)
  Deployed 9390ec0. The database copy from before is kept at /var/backups/edutrack/pre-deploy/…
```

## What each step protects you from

| Step | Without it |
|---|---|
| Refuses when files were edited by hand on the server | `git pull` fails halfway, or a hand fix silently disappears |
| **1. Database copy before anything changes** | A migration that goes wrong has no quick undo. The nightly backup might be 20 hours old, which means a day of attendance lost |
| 2. `git merge --ff-only` | If the server's history somehow differs from GitHub, it stops instead of mixing the two |
| 3. `pip install` | New code needs a package the server doesn't have, and every page errors |
| 4. `manage.py check`, then `migrate` | A broken setting is caught before the database is touched |
| 5. `collectstatic` | New CSS and JavaScript don't show up |
| 6. Restart and check the site answers | You think it worked, and students find out it didn't |

The last 10 pre-deploy copies are kept (`PRE_DEPLOY_KEEP` in `/etc/edutrack/ops.env`).

## Before a big or risky change

The deploy already copies the database, but only on the server. For migrations
that change or delete a lot of data, or any upgrade you're unsure about, also
send a fresh copy off the server first:

```
$ sudo edutrack-backup
$ sudo edutrack-deploy
```

Deploy outside school hours, and tell staff the site may be down for a few minutes.

## The server has hand edits

```
These files were changed by hand on the server, so they differ from GitHub:
 M backend/edutrack/settings.py
```

The deploy stopped before changing anything. See what was changed:

```
$ sudo -u edutrack git -C /var/www/edutrack diff
```

- **The change should stay:** make the same change on your laptop, commit and
  push it. Then throw the server's copy away and deploy:
  `sudo -u edutrack git -C /var/www/edutrack checkout -- backend/edutrack/settings.py`
  and `sudo edutrack-deploy`
- **The change was a mistake or an experiment:** run the same `checkout` line and
  deploy.

The rule that keeps disaster recovery possible: **the server runs exactly what is
in git.** Anything changed only on the server disappears when the server does.

## Going back

If a step fails, the deploy stops and prints the exact commands for your
situation. They look like this:

```
DEPLOY STOPPED. To go back to how things were before this deploy:

    sudo -u edutrack git -C /var/www/edutrack reset --hard 70f87e0
    sudo edutrack-restore /var/backups/edutrack/pre-deploy/edutrack_db-2026-10-02_1412-code-70f87e0.dump        # also restarts the app
```

1. The first line puts the code back to the version that was running.
2. The second puts the database back to the copy taken just before the deploy.
   It asks you to type `RESTORE`, sets the current (broken) database aside in
   `/var/backups/edutrack/pre-restore/` first, then restarts the site and checks it answers.

**Order matters: code first, then the database.** The restore runs `migrate` with
whatever code is present. With the old code in place, the old database stays as it is.

**Do you always need the second line?** If the deploy stopped *before* step 4
(during `git merge` or `pip install`), the database wasn't touched, so the first
line and `sudo systemctl restart edutrack` are enough. If it stopped at step 4 or
later, run both.

**Anything entered between the deploy and going back is lost** from the database.
That's why you deploy outside busy hours and go back quickly, or fix forward
(push a fix and deploy again) when the problem is small.

After going back, fix the problem on your laptop, push, and deploy again.

## Changing the deploy script itself

`/usr/local/sbin/edutrack-deploy` is a copy (guide 01 step 8 explains why). After
changing `ops/deploy.sh` in git and deploying, install the new copy:

```
$ sudo install -o root -g root -m 700 /var/www/edutrack/ops/deploy.sh /usr/local/sbin/edutrack-deploy
```

(The Ansible playbook in guide 04 does this for you whenever you run it.)
