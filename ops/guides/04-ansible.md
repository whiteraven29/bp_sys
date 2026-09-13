# 04 — The server as code, with Ansible

**Time:** about an hour the first time. **Where:** your laptop, talking to the server over SSH.
**Before:** guide 01 finished, so there's a fresh backup in case anything goes wrong.

## What "Infrastructure as Code" means

Today the server exists because someone typed the commands in `DEPLOYMENT.md`,
plus fixes nobody wrote down. If it dies, rebuilding it means doing all of that
again from memory, under pressure.

With Infrastructure as Code (IaC), the server's setup is written as a file in git:
[`ops/ansible/site.yml`](../ansible/site.yml). Building a new server becomes one
command. The file is also an exact record of how the server is set up, and every
change to it is in git history.

**Ansible** is the IaC tool that fits one server. It needs nothing installed on the
server: it logs in over SSH like you do, and runs from your laptop.

### The words you'll meet

| Word | Meaning | In this project |
|---|---|---|
| **Inventory** | Which servers to manage, and their particular values | `inventory.ini`: the Contabo IP, your domain |
| **Playbook** | A file listing what the servers should look like | `site.yml` |
| **Task** | One item in the playbook: "nginx is installed" | `- name: Install the system packages` |
| **Module** | The tool a task uses: `apt`, `template`, `ufw`, `postgresql_db`… | `ansible.builtin.apt:` |
| **Template** | A config file with blanks that Ansible fills in | `templates/nginx-edutrack.conf.j2` |
| **Handler** | A task that runs only if something changed: "reload nginx if its config changed" | `handlers:` at the top of `site.yml` |
| **Idempotent** | Running it again changes nothing if nothing needs changing | Every task in `site.yml` |
| **Check mode** | A dry run: report what *would* change, change nothing | `--check --diff` |

The most important idea is **idempotent**. A task doesn't say "run `apt install
nginx`". It says "nginx must be installed". Ansible looks first, and acts only if
it isn't. That's why the playbook is safe to run against the live server and safe
to run a hundred times.

Open `site.yml` now and read it top to bottom. Each task's `name:` says in plain
words what it makes true. Compare it with `DEPLOYMENT.md` and you'll recognise each step.

---

## Step 1 — Install Ansible on the laptop

```
$ sudo apt install -y ansible
$ ansible --version | head -1
$ ansible-galaxy collection list 2>/dev/null | grep -E 'community\.(general|postgresql)'
```

The last command should show both collections: `community.general` provides the
firewall module and `community.postgresql` the database modules. If either is
missing:

```
$ ansible-galaxy collection install community.general community.postgresql
```

## Step 2 — Check your SSH key login

Ansible logs in exactly the way you do, with your SSH key. Your server already
accepts only keys, so there's nothing to set up. Just confirm it works without
any password prompt from the server:

```
$ ssh YOU@YOUR-SERVER-IP 'echo logged in with my key'
```

If your key has a passphrase (guide 01 step 12), load it into the SSH agent
once, so Ansible doesn't need it typed for each of its many logins:

```
$ ssh-add ~/.ssh/id_ed25519
```

> If you log in as a normal user with `sudo` instead of `root`, use that user in
> step 3 and add `-K` to every `ansible-playbook` command below. Ansible then asks
> for your **sudo** password. That's the account's password on the server, used
> by `sudo`, not an SSH login password.
>
> If SSH on the server listens on a port other than 22, add `ansible_port=THAT-PORT`
> after `ansible_user=…` in step 3.

## Step 3 — Fill in the inventory

```
$ cd ~/Documents/BPHACOH/bp_sys/ops/ansible
$ cp inventory.ini.example inventory.ini
$ nano inventory.ini
```

Set `ansible_host` to the server's IP, `domains` to the same list as
`ALLOWED_HOSTS` in the server's `.env`, and `letsencrypt_email`. Leave
`enable_https=true` for the live server. `inventory.ini` is ignored by git, so your
server details stay off GitHub.

## Step 4 — Check Ansible can reach the server

```
$ ansible edutrack -m ping
```

```
contabo | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

## Step 5 — Prepare your undo

This is the first time a tool changes the live server for you. You don't use
snapshots, so prepare these three instead:

1. **A fresh backup**, so the newest one is minutes old. **server:** `sudo edutrack-backup`
2. **Ansible's own copies.** Every config file the playbook replaces is kept next
   to it with a date in its name, for example
   `/etc/nginx/sites-available/edutrack.48213.2026-10-02@14:12:07~`. Step 7 shows
   how to put one back.
3. **An open SSH session.** In a second terminal, log in to the server now and
   leave that window open until step 8 is done. The playbook touches the SSH
   settings and the firewall. If a new login ever failed, this open session would
   still work, and you'd use it to undo the change.

## Step 6 — Dry run: see what would change

```
$ ansible-playbook site.yml --check --diff
```

For each task, Ansible prints one of:

- **`ok`**: already as the playbook describes, nothing to do.
- **`changed`**: different on the server. With `--diff`, the lines starting with
  `-` are what the server has now, and the lines starting with `+` are what the
  playbook would write.
- **`skipping`**: the task doesn't apply (for example, "copy a .env" when one already exists).

At the end, `PLAY RECAP` counts them.

**What to expect on your existing server**, the first time:

| Task | Why it may say `changed` |
|---|---|
| SSH accepts keys only | The file `/etc/ssh/sshd_config.d/00-edutrack-keys-only.conf` is new. It writes down the rule you already set by hand (no password logins), so a rebuilt server gets it too. Before writing it, the playbook checks your login account has a key installed |
| Firewall tasks | If `ufw` was never switched on. It first allows the port(s) SSH really listens on (read from the server, not guessed), then HTTP and HTTPS, and blocks everything else. **If the server runs anything else that must be reachable from outside, stop here**: it would be blocked |
| The gunicorn service file | The worker count is now calculated from the CPU count, and a comment header is added |
| The nginx site | When certbot was first run, it edited this file itself. The template writes the same settings in a tidier form |
| The EduTrack database | The playbook makes `edutrack_user` the owner of the database |
| Operations settings / Install edutrack-… | Files from guide 01 being kept in step |

**Read every diff.** If something would change in a way you don't understand,
stop and find out why before step 7. That's the purpose of the dry run.

Check mode can't predict everything: `migrate`, `collectstatic` and `certbot`
are commands, and Ansible skips commands in check mode because it can't know
what they would do.

## Step 7 — Run it for real

```
$ ansible-playbook site.yml
```

Then check two things:

1. **SSH still lets you in.** In a *third* terminal: `ssh YOU@YOUR-SERVER-IP 'echo still fine'`.
   Keep the session from step 5 open until this works.
2. **The site works.** In a browser: log in, open a page, download an uploaded letter.

**If something broke**, use the session from step 5 on the server:

| What broke | Undo |
|---|---|
| The site (nginx) | `ls /etc/nginx/sites-available/` shows the dated copy. `sudo cp -p '/etc/nginx/sites-available/edutrack.<date>~' /etc/nginx/sites-available/edutrack && sudo nginx -t && sudo systemctl reload nginx` |
| The app (gunicorn) | `sudo cp -p '/etc/systemd/system/edutrack.service.<date>~' /etc/systemd/system/edutrack.service && sudo systemctl daemon-reload && sudo systemctl restart edutrack` |
| Something can't connect since the firewall went on | `sudo ufw disable`, then find out which port it needs |
| SSH logins | `sudo rm /etc/ssh/sshd_config.d/00-edutrack-keys-only.conf && sudo systemctl reload ssh` |

Then fix the template or setting in `ops/ansible` on the laptop, dry-run again,
and run again. Each copy's name has the date and time, so pick the one from just
before your run.

## Step 8 — Run it again, and see nothing change

```
$ ansible-playbook site.yml
```

The `PLAY RECAP` should now say `changed=0`. This is idempotency. It proves the
playbook describes the server exactly as it is, which means it can build an
identical one.

## Step 9 — Commit

```
$ cd ~/Documents/BPHACOH/bp_sys
$ git add ops/ansible
$ git commit -m "ops: server setup matches the live server"
$ git push
```

After a few days of everything working, you can delete the dated `…~` copies Ansible left.

---

## How you change the server from now on

**Never edit nginx, gunicorn or firewall settings on the server by hand again.** Instead:

1. Change the template or task in `ops/ansible/` on the laptop.
2. `ansible-playbook site.yml --check --diff` and read the diff.
3. `ansible-playbook site.yml`
4. Commit and push.

Example: uploads over 10 MB are refused, and you need 20 MB. In
`group_vars/edutrack.yml`, change `upload_limit: 10M` to `upload_limit: 20M`,
then dry-run, run and commit. The "Reload nginx" handler runs by itself, because
the nginx template changed.

If someone *did* edit the server by hand, the next dry run shows it as a diff,
and the next real run puts it back to what git says.

## What the playbook deliberately does not do

| Not done by Ansible | Done by | Why |
|---|---|---|
| Updating the code on the live server | `sudo edutrack-deploy` (guide 03) | Deploys need the database copy and site check every time |
| `seed_levels`, `create_admin`, fee structures | You, once, on a brand new install | They create data; running them on a live server could overwrite real data |
| Replacing an existing `backend/.env` | Nobody | Your secrets stay as they are |
| Connecting Google Drive | You (guide 01 steps 5–6) | It needs you to sign in to Google |

## When something goes wrong

**`UNREACHABLE! … Permission denied (publickey)`**: Ansible isn't using the key
you log in with. Check `ansible_user` in `inventory.ini` is the account you use,
and run `ssh-add` (step 2). If your key isn't `~/.ssh/id_ed25519`, add
`ansible_ssh_private_key_file=~/.ssh/YOUR-KEY` after `ansible_user=…`.

**`Missing sudo password`**: you log in as a normal user; add `-K`.

**`authorized_keys is missing or empty`**: the playbook stopped *before* touching
SSH, because the account in `ansible_user` has no key installed where it looked.
Check `ansible_user` is the account you really log in as.

**`couldn't resolve module/action 'community.postgresql.postgresql_user'`**: run the `ansible-galaxy collection install` line from step 1.

**New SSH logins fail after a run**: use the session you kept open in step 5 and
the undo table in step 7. If no session is open, use the Contabo control panel:
start the server in its *Rescue System*, then remove
`/etc/ssh/sshd_config.d/00-edutrack-keys-only.conf` or run `ufw disable` from there.

**`certbot … Challenge failed`**: the domain doesn't point at this server yet. Set
`enable_https=false`, run the playbook, fix DNS, wait for it to update, then set it
back to `true` and run again.
