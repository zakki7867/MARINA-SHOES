#!/usr/bin/env bash
set -euo pipefail

# PostgreSQL restore script for MARINA SHOES ERP
# Usage: ./scripts/restore_postgres.sh ./backups/marina_shoes_YYYYMMDD_HHMMSS.dump
DUMP_FILE="${1:?Usage: restore_postgres.sh <dump_file>}"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL environment variable must be set."
  exit 1
fi

pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$DUMP_FILE"
echo "Database restored from: $DUMP_FILE"
