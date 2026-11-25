from pathlib import Path
import shutil
import sqlite3
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Verify or restore a database backup cleanly (SQLite integrity + table verification, or PostgreSQL pg_restore)."

    def add_arguments(self, parser):
        parser.add_argument("backup_file", help="Path to backup file (.sqlite3 or .dump).")
        parser.add_argument(
            "--target",
            default="",
            help="Optional target SQLite file path to restore into (defaults to configured DATABASES['default']).",
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Validate backup integrity and schema without overwriting any active database.",
        )

    def handle(self, *args, **options):
        backup_path = Path(options["backup_file"])
        if not backup_path.exists():
            raise CommandError(f"Backup file does not exist: {backup_path}")

        db_cfg = settings.DATABASES["default"]
        engine = db_cfg["ENGINE"]

        if "sqlite3" in engine or backup_path.suffix == ".sqlite3":
            with sqlite3.connect(backup_path) as check_conn:
                integrity = check_conn.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise CommandError(f"Backup failed PRAGMA integrity_check: {integrity}")
                tables = {
                    row[0]
                    for row in check_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "django_migrations" not in tables or "core_storesettings" not in tables:
                    raise CommandError("Backup is missing core ERP tables (django_migrations / core_storesettings).")

            if options["verify_only"]:
                self.stdout.write(self.style.SUCCESS(f"SQLite backup verified cleanly: {backup_path}"))
                return str(backup_path)

            target_path = Path(options["target"]) if options["target"] else Path(db_cfg["NAME"])
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not options["target"]:
                connections["default"].close()

            with sqlite3.connect(backup_path) as src_conn, sqlite3.connect(target_path) as dst_conn:
                src_conn.backup(dst_conn)
                post_check = dst_conn.execute("PRAGMA integrity_check").fetchone()
                if not post_check or post_check[0] != "ok":
                    raise CommandError(f"Restored target failed PRAGMA integrity_check: {post_check}")

            self.stdout.write(self.style.SUCCESS(f"SQLite database restored and verified: {target_path}"))
            return str(target_path)

        if "postgresql" in engine:
            pg_restore = shutil.which("pg_restore")
            if not pg_restore:
                raise CommandError("pg_restore binary not found on PATH.")
            if options["verify_only"]:
                proc = subprocess.run([pg_restore, "--list", str(backup_path)], capture_output=True, text=True)
                if proc.returncode != 0:
                    raise CommandError(f"pg_restore verification failed: {proc.stderr.strip()}")
                self.stdout.write(self.style.SUCCESS(f"PostgreSQL archive verified cleanly: {backup_path}"))
                return str(backup_path)

            cmd = [
                pg_restore,
                "--clean",
                "--if-exists",
                "--no-owner",
                "-d",
                db_cfg["NAME"],
                str(backup_path),
            ]
            env_vars = {}
            if db_cfg.get("HOST"):
                cmd.extend(["-h", str(db_cfg["HOST"])])
            if db_cfg.get("PORT"):
                cmd.extend(["-p", str(db_cfg["PORT"])])
            if db_cfg.get("USER"):
                cmd.extend(["-U", str(db_cfg["USER"])])
            if db_cfg.get("PASSWORD"):
                env_vars["PGPASSWORD"] = str(db_cfg["PASSWORD"])

            import os

            run_env = {**os.environ, **env_vars}
            proc = subprocess.run(cmd, env=run_env, capture_output=True, text=True)
            if proc.returncode != 0:
                raise CommandError(f"pg_restore failed: {proc.stderr.strip()}")
            self.stdout.write(self.style.SUCCESS(f"PostgreSQL database restored from: {backup_path}"))
            return str(backup_path)

        raise CommandError(f"Unsupported database engine for db_restore: {engine}")
