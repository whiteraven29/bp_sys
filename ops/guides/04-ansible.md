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

## How Ansible gets in, on a server like yours

Your server lets only a few named accounts log in over SSH, and only with a key.
The project belongs to `bphacoh`, which nobody logs in as directly. By hand, you
log in as yourself and then switch to `bphacoh`.

Ansible does the same, without adding any new way in:

```
laptop ──SSH, your key──▶ YOU ──sudo──▶ root      packages, nginx, firewall, SSH settings
                               └──sudo──▶ bphacoh   git, pip, migrate, collectstatic
                               └──sudo──▶ postgres  the database user and database
```

- It logs in as **you** (`ansible_user`), the account SSH already allows.
- `become` is Ansible's word for switching account. It uses `sudo`, so it asks
  for **your sudo password** once at the start of each run, shown as `BECOME password:`.
  That's the password `sudo` asks for on the server, not an SSH password.
- `bphacoh` needs no SSH key and no password. Nothing about who can log in changes.

That means **your own account needs `sudo`** on the server. Step 2 checks that.

## Step 2 — Check your login and sudo

**laptop:**

```
$ ssh -t YOU@YOUR-SERVER-IP 'sudo -v && sudo -u bphacoh whoami'
```

It should ask for your sudo password, then print `bphacoh`. That's exactly the
path Ansible takes.

If you see `YOU is not in the sudoers file`, your account can't use sudo, and
Ansible can't either. Give it sudo from the account that has it: log in as you
do now, switch to that account, and run `sudo usermod -aG sudo YOU`. Then log
out and back in, and repeat the check.

> **Worth doing while you're there:** keep `sudo` on *your* login account, and
> think about removing it from `bphacoh`. The website runs as `bphacoh`. If a bug
> in the site ever let an attacker run commands as that account, sudo rights there
> are one password away from the whole server.

If your SSH key has a passphrase (guide 01 step 12), load it into the SSH agent
once, so Ansible doesn't need it typed for each of its many logins:

```
$ ssh-add ~/.ssh/id_ed25519
```

## Step 3 — Fill in the inventory

On the **server**, look at who SSH lets in:

```
$ sudo sshd -T | grep -E '^(port|allowusers) '
```

Then on the **laptop**:

```
$ cd /home/whiteraven/Documents/BPHACOH/bp_sys/ops/ansible
$ cp -n inventory.ini.example inventory.ini
$ nano inventory.ini
```

`cp -n` doesn't overwrite an inventory you already filled in. Compare yours with
`inventory.ini.example` and add any lines it's missing (`ssh_allow_users`).

> **Run Ansible from the same laptop account you run `ssh` from.** Ansible uses
> that account's SSH key (`~/.ssh/`) and SSH settings (`~/.ssh/config`). If you
> SSH to the server as the laptop's **root** (from `sudo -i`, or `sudo ssh …`),
> then run every `ansible` and `ansible-playbook` command as root too. From a
> normal laptop account, the server would refuse the login with
> `Permission denied (publickey)`.
>
> As root, `~` means `/root`, not your home folder. That's why the commands in this
> guide use the full path `/home/whiteraven/Documents/BPHACOH/bp_sys`.
>
> `sudo` on the *server* is still separate: it's Ansible's `become`, and it's what
> `BECOME password` asks for.

- `ansible_host`: the server's IP.
- `ansible_user`: **your** login, the one you type in `ssh YOU@…`.
- `ansible_port=…`: add this on the same line only if `sshd -T` showed a port other than 22.
- `ssh_allow_users`: **exactly** the accounts after `allowusers` above, separated by
  spaces. The playbook writes this list into the server's SSH settings, so a
  rebuilt server gets the same restriction. It refuses to run if the list differs
  from what the server has now: that difference would either lock someone out or
  quietly let someone new in.
- `domains`: the same list as `ALLOWED_HOSTS` in the server's `.env`.
- `letsencrypt_email`, and leave `enable_https=true` for the live server.

`inventory.ini` is ignored by git, so your login name and IP stay off GitHub.

Now check the names in [`group_vars/edutrack.yml`](../ansible/group_vars/edutrack.yml)
against `/etc/edutrack/ops.env` on the server: `app_user: bphacoh`, `repo_dir`,
`service_name`, and `nginx_site` (the file name in `/etc/nginx/sites-enabled/`).
You don't have to get this perfect from memory. The first thing the playbook does
is compare them with the server, and it stops with a message naming the value to
fix before it changes anything.

## Step 4 — Check Ansible can reach the server, and become bphacoh

```
$ ansible edutrack -m ping
$ ansible edutrack -m command -a whoami --become --become-user bphacoh
```

Both ask for your sudo password. The first prints `"ping": "pong"`. The second
prints `bphacoh`: Ansible logged in as you, and switched to the project account.

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

Before the list of changes, the first tasks (section 0 in `site.yml`) compare the
playbook with your server. If one of them stops with `Nothing was changed…`, it
says which value to fix. Fix it and dry-run again. Section 0 checks:

- **Project names:** folder, account, service and nginx site match `/etc/edutrack/ops.env`.
- **Domains:** every entry in `domains` is in EduTrack's `ALLOWED_HOSTS`, no *other*
  site on the server already answers to it, and EduTrack's nginx site answers to
  exactly that list with the same certificate.
- **SSH logins:** `ssh_allow_users` equals what the server allows today.
- **Firewall:** switching the firewall on wouldn't block a port something already listens on.

> **This server hosts other projects** (blog, portfolio, bots…). The playbook only
> manages EduTrack's own files. The two server-wide pieces, the SSH settings and
> the firewall, only ever *add* what's missing, never remove other projects'
> settings, and section 0 stops first if either would lock something out. Backups
> and these guides cover **EduTrack only**: the other sites need their own.

| Task | Why it may say `changed` |
|---|---|
| SSH accepts keys only, and only from the accounts in ssh_allow_users | The file `/etc/ssh/sshd_config.d/00-edutrack-keys-only.conf` is new. It writes down the rules you already set by hand (no password logins, `AllowUsers`), so a rebuilt server gets them too. It changes nothing about who can log in today: section 0 already made sure the list is identical |
| Firewall tasks | If `ufw` was never switched on. It allows the port(s) SSH really listens on (read from the server, not guessed), HTTP, HTTPS and `firewall_extra_ports`, then refuses everything else. Section 0 already stopped if another project's port would have been blocked. With `manage_firewall=false` these tasks are skipped |
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
you log in with. Check three things:
1. You run Ansible as the **same laptop account** you run `ssh` from (root, in your case).
2. `ansible_user` in `inventory.ini` is the server account written before the `@` in your `ssh` command.
3. The key: run `ssh-add` (step 2), or, if your key isn't `~/.ssh/id_ed25519`, add
   `ansible_ssh_private_key_file=/root/.ssh/YOUR-KEY` after `ansible_user=…`.

**`Missing sudo password`** or **`Incorrect sudo password`**: at `BECOME password:`,
type your account's sudo password on the server.

**`YOU is not in the sudoers file`**: your login account has no sudo. See step 2.

**`Nothing was changed. This server's /etc/edutrack/ops.env says …`**: a name in
`group_vars/edutrack.yml` differs from the server. Set it to the server's value.

**`Nothing was changed. The server lets these accounts log in over SSH …`**:
`ssh_allow_users` in `inventory.ini` must list exactly the accounts from
`sudo sshd -T | grep allowusers`, including your own.

**`authorized_keys is missing or empty`**: the playbook stopped *before* touching
SSH, because the account in `ansible_user` has no key installed where it looked.
Check `ansible_user` is the account you really log in as.

**`chmod: … Operation not permitted` / `Failed to set permissions on the temporary files`**
when a task runs as `bphacoh`: the `acl` package is missing on the server. The
playbook installs it in its first step, so run it again; or install it by hand
with `sudo apt install acl`.

**`couldn't resolve module/action 'community.postgresql.postgresql_user'`**: run the `ansible-galaxy collection install` line from step 1.

**New SSH logins fail after a run**: use the session you kept open in step 5 and
the undo table in step 7. If no session is open, use the Contabo control panel:
start the server in its *Rescue System*, then remove
`/etc/ssh/sshd_config.d/00-edutrack-keys-only.conf` or run `ufw disable` from there.

**`certbot … Challenge failed`**: the domain doesn't point at this server yet. Set
`enable_https=false`, run the playbook, fix DNS, wait for it to update, then set it
back to `true` and run again.
