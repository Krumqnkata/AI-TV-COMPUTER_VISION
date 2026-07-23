"""Database-aware backup creation, validation and controlled downloads."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from engine.admin_models import BackupRecord, StaffAccount
from utils.config import Config
from web.services.admin_control import audit_event


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_backup_path(file_name: str) -> Path:
    root = Config.BACKUP_DIR.resolve()
    candidate = (root / Path(file_name).name).resolve()
    if candidate.parent != root:
        raise ValueError("Невалиден backup път")
    return candidate


def _sqlite_source(url: URL) -> Path:
    if not url.database or url.database == ":memory:":
        raise RuntimeError("SQLite backup изисква файлова база")
    source = Path(url.database).resolve()
    if not source.is_file():
        raise RuntimeError("Файлът на SQLite базата не е намерен")
    return source


def _postgres_tool(name: str) -> Path:
    executable = f"{name}.exe" if os.name == "nt" else name
    if Config.POSTGRES_BIN_DIR_CONFIGURED:
        configured = Config.POSTGRES_BIN_DIR / executable
        if configured.is_file():
            return configured
        raise RuntimeError(f"{name} не е намерен в POSTGRES_BIN_DIR")

    discovered = shutil.which(executable) or shutil.which(name)
    if discovered:
        return Path(discovered)

    if os.name == "nt":
        roots = {
            Path(value) / "PostgreSQL"
            for key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
            if (value := os.getenv(key))
        }
        versions: list[tuple[int, Path]] = []
        for root in roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                try:
                    version = int(child.name.split(".", 1)[0])
                except ValueError:
                    continue
                versions.append((version, child))
        for _version, installation in sorted(
            versions,
            key=lambda item: item[0],
            reverse=True,
        ):
            candidate = installation / "bin" / executable
            if candidate.is_file():
                return candidate

    raise RuntimeError(
        f"{name} не е намерен. Добавете PostgreSQL bin папката в PATH "
        "или задайте POSTGRES_BIN_DIR в .env.local."
    )


def _postgres_environment(url: URL) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PGPASSWORD", None)
    if url.password:
        environment["PGPASSWORD"] = url.password
    environment["PGCLIENTENCODING"] = "UTF8"
    sslmode = url.query.get("sslmode")
    if sslmode:
        environment["PGSSLMODE"] = str(sslmode)
    return environment


def _postgres_connection_arguments(url: URL) -> list[str]:
    if not url.database:
        raise RuntimeError("PostgreSQL DATABASE_URL трябва да съдържа име на база")
    arguments = [f"--dbname={url.database}"]
    if url.host:
        arguments.append(f"--host={url.host}")
    if url.port:
        arguments.append(f"--port={url.port}")
    if url.username:
        arguments.append(f"--username={url.username}")
    return arguments


def _run_postgres_tool(command: list[str], url: URL) -> subprocess.CompletedProcess[str]:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=Config.BACKUP_COMMAND_TIMEOUT_SECONDS,
            env=_postgres_environment(url),
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("PostgreSQL backup командата просрочи позволеното време") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "неизвестна грешка").strip()
        raise RuntimeError(f"PostgreSQL backup командата е неуспешна: {detail[:500]}") from exc


def _record_backup(
    db: Session,
    actor: StaffAccount,
    destination: Path,
    database_type: str,
    *,
    ip_address: str | None,
) -> BackupRecord:
    record = BackupRecord(
        file_name=destination.name,
        storage_path=str(destination),
        database_type=database_type,
        status="verified",
        size_bytes=destination.stat().st_size,
        sha256=_sha256(destination),
        created_by_staff_id=actor.id,
        verified_at=datetime.now(),
    )
    db.add(record)
    audit_event(
        db,
        "backup.created",
        f"Създадено резервно копие: {destination.name}",
        actor=actor,
        entity_type="BackupRecord",
        changes={
            "file_name": destination.name,
            "database_type": database_type,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
        },
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(record)
    return record


def _create_sqlite_backup(
    db: Session,
    actor: StaffAccount,
    url: URL,
    *,
    ip_address: str | None,
) -> BackupRecord:
    source = _sqlite_source(url)
    file_name = f"school-ai-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
    destination = _safe_backup_path(file_name)
    with closing(sqlite3.connect(str(source))) as source_connection:
        with closing(sqlite3.connect(str(destination))) as destination_connection:
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Проверката на SQLite копието е неуспешна")
    return _record_backup(db, actor, destination, "sqlite", ip_address=ip_address)


def _create_postgresql_backup(
    db: Session,
    actor: StaffAccount,
    url: URL,
    *,
    ip_address: str | None,
) -> BackupRecord:
    file_name = f"school-ai-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.dump"
    destination = _safe_backup_path(file_name)
    command = [
        str(_postgres_tool("pg_dump")),
        "--format=custom",
        "--no-owner",
        "--no-acl",
        f"--file={destination}",
        *_postgres_connection_arguments(url),
    ]
    try:
        _run_postgres_tool(command, url)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("pg_dump не създаде валиден backup файл")
        _verify_postgresql_archive(destination, url)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return _record_backup(db, actor, destination, "postgresql", ip_address=ip_address)


def create_database_backup(
    db: Session,
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> BackupRecord:
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    url = make_url(Config.DATABASE_URL)
    backend = url.get_backend_name()
    if backend == "sqlite":
        return _create_sqlite_backup(db, actor, url, ip_address=ip_address)
    if backend == "postgresql":
        return _create_postgresql_backup(db, actor, url, ip_address=ip_address)
    raise RuntimeError(f"Backup не се поддържа за database backend: {backend}")


def create_sqlite_backup(
    db: Session,
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> BackupRecord:
    """Compatibility alias for existing integrations and SQLite tests."""
    url = make_url(Config.DATABASE_URL)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError("Тази compatibility функция работи само със SQLite")
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return _create_sqlite_backup(db, actor, url, ip_address=ip_address)


def _verify_postgresql_archive(path: Path, url: URL | None = None) -> bool:
    restore_url = url or make_url(Config.DATABASE_URL)
    command = [str(_postgres_tool("pg_restore")), "--list", str(path)]
    _run_postgres_tool(command, restore_url)
    return True


def verify_backup(db: Session, record: BackupRecord) -> bool:
    path = _safe_backup_path(record.file_name)
    valid = (
        path.is_file()
        and path.stat().st_size == record.size_bytes
        and _sha256(path) == record.sha256
    )
    if valid and record.database_type == "sqlite":
        try:
            with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                valid = bool(result and result[0] == "ok")
        except sqlite3.Error:
            valid = False
    elif valid and record.database_type == "postgresql":
        try:
            valid = _verify_postgresql_archive(path)
        except RuntimeError:
            valid = False
    elif valid:
        valid = False
    record.status = "verified" if valid else "invalid"
    record.verified_at = datetime.now()
    db.commit()
    return valid


def downloadable_backup_path(record: BackupRecord) -> Path:
    path = _safe_backup_path(record.file_name)
    if str(path) != str(Path(record.storage_path).resolve()) or not path.is_file():
        raise FileNotFoundError("Резервното копие не е налично")
    return path


def backup_media_type(record: BackupRecord) -> str:
    if record.database_type == "sqlite":
        return "application/vnd.sqlite3"
    return "application/octet-stream"
