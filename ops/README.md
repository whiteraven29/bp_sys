# EduTrack operations: backups, recovery and the server as code

This folder keeps EduTrack safe and rebuildable. The app's code is already safe
in GitHub. This folder protects what is **not** in GitHub:

| What | Where it lives | Why git can't hold it |
|---|---|---|
| The database: attendance, marks, invoices, payments, requests | PostgreSQL on the server | It changes every minute and holds personal data |
| Uploaded files: letters, announcement PDFs, the college logo | `backend/media/` on the server | Same |
| Secrets: `SECRET_KEY`, the database password | `backend/.env` on the server | Secrets must never be in a public repository |
| How the server is set up: packages, nginx, gunicorn, firewall, HTTPS | Typed by hand from `DEPLOYMENT.md` | Nothing wrote it down as code, until `ansible/` |

## The three copies

```
                        every night, 02:30
   ┌──────────────────┐   encrypted by restic   ┌─────────────────────────┐
   │  Contabo server  │ ──────────────────────▶ │  Google Drive (free)    │
   │                  │                         │  a Google account used  │
   │  live database   │                         │  only for backups       │
   │  last 7 dumps    │                         │  kept for a year        │
   └──────────────────┘                         └────────────┬────────────┘
                                                             │ once a month
                                                             ▼ you copy it down
                                                ┌─────────────────────────┐
                                                │  Your laptop            │
                                                │  encrypted copy, and    │
                                                │  the practice restore   │
                                                └─────────────────────────┘
```

Two of the three copies are outside Contabo. If the server dies or the Contabo
account is lost, the data survives. Everything that leaves the server is
encrypted, so Google only ever holds scrambled files.

Contabo snapshots are not used. Instead of a snapshot, each risky change has its
own undo: deploys copy the database first, Ansible keeps dated copies of the files
it replaces, and for anything else you run `sudo edutrack-backup` right before
(guide 01, step 12).

## Which guide, when

| Situation | Open |
|---|---|
| Setting up backups for the first time | [guides/01-backups.md](guides/01-backups.md) |
| Monthly: prove the backups really restore | [guides/02-restore-drill.md](guides/02-restore-drill.md) |
| Putting new code live | [guides/03-deploying.md](guides/03-deploying.md) |
| Writing the server down as code with Ansible | [guides/04-ansible.md](guides/04-ansible.md) |
| **Something went wrong: bad update, lost data, dead server, moving servers** | [guides/05-disaster-recovery.md](guides/05-disaster-recovery.md) |

Do them in order the first time: 01, then 02, 03 and 04. Guide 05 only makes
sense once 01 is running.

## The emergency kit: keep these OUTSIDE the server

If the server is gone, you need all of these, and none of them can be on the server:

- [ ] **The restic backup password.** Keep it in a password manager, plus a printed copy in a sealed envelope at the college. Without it, the backups cannot be opened by anyone, ever.
- [ ] **The login for the backup Google account.**
- [ ] **Your SSH key, and a spare one kept offline.** Password logins are off, so a key is the only way into a server (guide 01, step 12).
- [ ] **Your Contabo login**, to order a new server.
- [ ] **The login for wherever your domain's DNS is managed**, to point the domain at a new server.
- [ ] **Access to GitHub** (`github.com/whiteraven29/bp_sys`). This folder and its guides are there too.
- [ ] **A laptop with this repository, `restic`, `rclone` and `ansible` installed.**

## What is in this folder

```
ops/
├── README.md                  ← you are here
├── ops.env.example            settings for the scripts → /etc/edutrack/ops.env on the server
├── deploy.sh                  → /usr/local/sbin/edutrack-deploy
├── backup/
│   ├── backup.sh              → /usr/local/sbin/edutrack-backup
│   ├── restore.sh             → /usr/local/sbin/edutrack-restore  (also runs on the laptop for drills)
│   ├── restic.sh              → /usr/local/sbin/edutrack-restic
│   ├── edutrack-backup.service  what a backup runs     → /etc/systemd/system/
│   └── edutrack-backup.timer    when: nightly at 02:30 → /etc/systemd/system/
├── ansible/
│   ├── site.yml               the server, as code
│   ├── inventory.ini.example  which server, which domain → copy to inventory.ini
│   ├── group_vars/edutrack.yml  settings that are the same on any server
│   └── templates/             the gunicorn service and nginx site
└── guides/                    step-by-step instructions
```

Each script explains itself at the top. Read them: they are short, and knowing
what they do is part of being ready for an emergency.
