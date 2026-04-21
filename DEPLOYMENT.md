# EduTrack — VPS Deployment Guide

## Prerequisites

- Ubuntu 22.04 VPS
- A domain name pointed to your VPS IP (set an **A record** in your domain DNS → your server IP)
- SSH access to the server

---

## Step 1 — Connect to Your Server

```bash
ssh root@your-server-ip
```

---

## Step 2 — Install System Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv nginx postgresql postgresql-contrib git
```

---

## Step 3 — Set Up PostgreSQL Database

```bash
sudo -u postgres psql
```

Inside the PostgreSQL shell, run:

```sql
CREATE DATABASE edutrack_db;
CREATE USER edutrack_user WITH PASSWORD 'choose-a-strong-password';
GRANT ALL PRIVILEGES ON DATABASE edutrack_db TO edutrack_user;
\q
```

---

## Step 4 — Create a Non-Root User (Recommended)

```bash
adduser edutrack
usermod -aG sudo edutrack
su - edutrack
```

---

## Step 5 — Clone the Project

```bash
cd /var/www
sudo git clone https://github.com/whiteraven29/YOUR-REPO-NAME.git edutrack
sudo chown -R edutrack:edutrack /var/www/edutrack
cd /var/www/edutrack/backend
```

---

## Step 6 — Set Up Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn whitenoise
```

---

## Step 7 — Create the Environment File

```bash
nano /var/www/edutrack/backend/.env
```

Paste the following and fill in your values:

```
SECRET_KEY=replace-this-with-a-long-random-string
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

DB_NAME=edutrack_db
DB_USER=edutrack_user
DB_PASSWORD=choose-a-strong-password
DB_HOST=localhost
DB_PORT=5432
```

> **Generate a secret key** by running:
> ```bash
> python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
> ```
> Copy the output and paste it as the `SECRET_KEY` value.

---

## Step 8 — Update settings.py for Production
f-v3%7xq15iq6a8cu5wm*jjk=b#s@4zqh@%xz*li0c9inhv=)4
Open `settings.py`:

```bash
nano /var/www/edutrack/backend/edutrack/settings.py
```

**Add WhiteNoise** to `MIDDLEWARE` — insert it directly after `SecurityMiddleware`:

```python
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # <-- add this line
    ...
]
```

**Add at the bottom** of `settings.py`:

```python
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

Save and close.

---

## Step 9 — Run Migrations and Collect Static Files

```bash
source /var/www/edutrack/backend/venv/bin/activate
cd /var/www/edutrack/backend

python manage.py migrate
python manage.py seed_levels
python manage.py collectstatic --no-input
python manage.py createsuperuser
```

---

## Step 10 — Create a Gunicorn Systemd Service

```bash
sudo nano /etc/systemd/system/edutrack.service
```

Paste the following:

```ini
[Unit]
Description=EduTrack Gunicorn Daemon
After=network.target

[Service]
User=edutrack
Group=www-data
WorkingDirectory=/var/www/edutrack/backend
ExecStart=/var/www/edutrack/backend/venv/bin/gunicorn \
    edutrack.wsgi \
    --bind 127.0.0.1:8000 \
    --workers 3 \
    --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable edutrack
sudo systemctl start edutrack
```

Verify it is running:

```bash
sudo systemctl status edutrack
```

You should see `Active: active (running)`.

---

## Step 11 — Configure Nginx

```bash
sudo nano /etc/nginx/sites-available/edutrack
```

Paste the following — **replace `yourdomain.com`** with your actual domain:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    location /static/ {
        alias /var/www/edutrack/backend/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120;
        client_max_body_size 10M;
    }
}
```

Enable the site and test:

```bash
sudo ln -s /etc/nginx/sites-available/edutrack /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

At this point your app should be reachable at `http://yourdomain.com`.

---

## Step 12 — Add HTTPS with Let's Encrypt (Free SSL)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Follow the prompts — Certbot will automatically update your Nginx config and redirect HTTP → HTTPS.

Verify auto-renewal works:

```bash
sudo certbot renew --dry-run
```

Your app is now live at `https://yourdomain.com`.

---

## Step 13 — Create an Admin Account

If you skipped `createsuperuser` earlier:

```bash
source /var/www/edutrack/backend/venv/bin/activate
cd /var/www/edutrack/backend
python manage.py createsuperuser
```

Log in at `https://yourdomain.com/admin` to manage users and data.

---

## Updating the App After Code Changes

Each time you push new code to GitHub, run this on the server:

```bash
cd /var/www/edutrack
git pull origin master
source backend/venv/bin/activate
cd backend
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --no-input
sudo systemctl restart edutrack
```

---

## Useful Commands

| Task | Command |
|------|---------|
| View app logs | `sudo journalctl -u edutrack -f` |
| Restart app | `sudo systemctl restart edutrack` |
| Restart Nginx | `sudo systemctl restart nginx` |
| Check Nginx errors | `sudo tail -f /var/log/nginx/error.log` |
| Open Django shell | `python manage.py shell` |
| Re-seed levels | `python manage.py seed_levels` |

---

## Troubleshooting

**502 Bad Gateway** — Gunicorn is not running. Check:
```bash
sudo systemctl status edutrack
sudo journalctl -u edutrack -n 50
```

**Static files not loading** — Re-run collectstatic:
```bash
python manage.py collectstatic --no-input
sudo systemctl restart nginx
```

**Database connection error** — Check your `.env` credentials match what you set in Step 3.

**ALLOWED_HOSTS error** — Make sure your domain is listed in `.env` under `ALLOWED_HOSTS`.
	
2.57.91.91