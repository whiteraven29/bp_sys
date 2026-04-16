#!/usr/bin/env bash
# EduTrack — first-time setup
set -e

echo "==> Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Copying .env template..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Edit .env with your PostgreSQL credentials before continuing."
  echo "    Then re-run:  source venv/bin/activate && python manage.py migrate"
  exit 0
fi

echo "==> Running migrations..."
python manage.py migrate

echo "==> Creating superuser (optional — press Ctrl-C to skip)..."
python manage.py createsuperuser || true

echo "==> Done! Start the server with:"
echo "    source venv/bin/activate && python manage.py runserver"
