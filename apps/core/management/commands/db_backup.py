from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Create a verified database backup (SQLite hot backup with integrity check, or PostgreSQL pg_dump)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=str(settings.BASE_DIR / "backups"),
            help="Directory to write backup artifacts into (default: ./backups).",
        )
        parser.add_argument(
            "--output",
            default="",
            help="Exact output file path (overrides --output-dir filename generation).",
        )

    def handle(self, *args, **options):
        db_cfg = settings.DATABASES["default"]
        engine = db_cfg["ENGINE"]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if "sqlite3" in engine:
            src_path = Path(db_cfg["NAME"])
            if not src_path.exists():
                raise CommandError(f"SQLite database file not found: {src_path}")

            if options["output"]:
                dest_path = Path(options["output"])
                dest_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                out_dir = Path(options["output_dir"])
                out_dir.mkdir(parents=True, exist_ok=True)
                dest_path = out_dir / f"marina_shoes_{ts}.sqlite3"

            connections["default"].close()
            with sqlite3.connect(src_path) as src_conn, sqlite3.connect(dest_path) as dst_conn:
                src_conn.backup(dst_conn)
                check_row = dst_conn.execute("PRAGMA integrity_check").fetchone()
                if not check_row or check_row[0] != "ok":
                    raise CommandError(f"Backup integrity check failed: {check_row}")

            self.stdout.write(self.style.SUCCESS(f"SQLite backup verified (PRAGMA integrity_check=ok): {dest_path}"))
            return str(dest_path)

        if "postgresql" in engine:
            pg_dump = shutil.which("pg_dump")
            if not pg_dump:
                raise CommandError("pg_dump binary not found on PATH.")
            if options["output"]:
                dest_path = Path(options["output"])
                dest_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                out_dir = Path(options["output_dir"])
                out_dir.mkdir(parents=True, exist_ok=True)
                dest_path = out_dir / f"marina_shoes_{ts}.dump"

            cmd = [
                pg_dump,
                "--format=custom",
                "--no-owner",
                f"--file={dest_path}",
                db_cfg["NAME"],
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
                raise CommandError(f"pg_dump failed: {proc.stderr.strip()}")
            self.stdout.write(self.style.SUCCESS(f"PostgreSQL backup created: {dest_path}"))
            return str(dest_path)

        raise CommandError(f"Unsupported database engine for db_backup: {engine}")
