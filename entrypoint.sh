#!/bin/sh
set -e

# Wait for PostgreSQL when it runs as a separate host (Docker Compose).
if [ -n "$POSTGRES_HOST" ] && [ "$POSTGRES_HOST" != "localhost" ] && [ "$POSTGRES_HOST" != "127.0.0.1" ]; then
  echo "Waiting for PostgreSQL at $POSTGRES_HOST:${POSTGRES_PORT:-5432}..."
  until pg_isready -h "$POSTGRES_HOST" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-umis}" >/dev/null 2>&1; do
    sleep 0.2
  done
  echo "PostgreSQL is ready."
fi

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

exec "$@"
