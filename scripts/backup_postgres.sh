#!/usr/bin/env bash
set -euo pipefail

# PostgreSQL backup script for MARINA SHOES ERP
# Usage: ./scripts/backup_postgres.sh /var/backups/marina_shoes
BACKUP_DIR="${1:-./backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$BACKUP_DIR"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL environment variable must be set."
  exit 1
fi

OUT_FILE="$BACKUP_DIR/marina_shoes_${TIMESTAMP}.dump"
pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" --file="$OUT_FILE"
echo "Backup saved to: $OUT_FILE"
