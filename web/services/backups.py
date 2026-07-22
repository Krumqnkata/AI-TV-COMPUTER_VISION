"""Safe SQLite backup creation, validation and controlled downloads."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url
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


def _sqlite_source() -> Path:
    url = make_url(Config.DATABASE_URL)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        raise RuntimeError("Вграденият backup в момента поддържа файлова SQLite база")
    source = Path(url.database).resolve()
    if not source.is_file():
        raise RuntimeError("Файлът на базата данни не е намерен")
    return source


def _safe_backup_path(file_name: str) -> Path:
    root = Config.BACKUP_DIR.resolve()
    candidate = (root / Path(file_name).name).resolve()
    if candidate.parent != root:
        raise ValueError("Невалиден backup път")
    return candidate


def create_sqlite_backup(
    db: Session,
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> BackupRecord:
    source = _sqlite_source()
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"school-ai-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
    destination = _safe_backup_path(file_name)
    with closing(sqlite3.connect(str(source))) as source_connection:
        with closing(sqlite3.connect(str(destination))) as destination_connection:
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Проверката на резервното копие е неуспешна")
    record = BackupRecord(
        file_name=file_name,
        storage_path=str(destination),
        database_type="sqlite",
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
        f"Създадено резервно копие: {file_name}",
        actor=actor,
        entity_type="BackupRecord",
        changes={"file_name": file_name, "size_bytes": record.size_bytes, "sha256": record.sha256},
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(record)
    return record


def verify_backup(db: Session, record: BackupRecord) -> bool:
    path = _safe_backup_path(record.file_name)
    valid = path.is_file() and path.stat().st_size == record.size_bytes and _sha256(path) == record.sha256
    if valid and record.database_type == "sqlite":
        try:
            with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                valid = bool(result and result[0] == "ok")
        except sqlite3.Error:
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
