"""
Utility script to create or update an admin user in the School AI database.
Run this once to set up the admin credentials.

Usage:
    python tools/create_admin.py --name "Администратор" --username admin
"""
import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.admin_models import StaffAccount
from engine.auth import get_password_hash
from engine.db import Person
from web.database import SessionLocal, assert_schema_current
from web.services.admin_control import apply_role_codes, ensure_admin_foundation


def create_or_update_admin(full_name: str, password: str, role: str = "admin", username: str | None = None):
    if len(password) < 12:
        raise ValueError("Паролата трябва да бъде поне 12 символа.")
    assert_schema_current()
    db = SessionLocal()
    try:
        user = db.query(Person).filter(Person.full_name == full_name).first()
        pw_hash = get_password_hash(password)
        if user:
            user.password_hash = pw_hash
            user.role = role
            user.active = True
            db.commit()
        else:
            user = Person(full_name=full_name, role=role, active=True, password_hash=pw_hash)
            db.add(user)
            db.commit()
        ensure_admin_foundation(db)
        account = db.query(StaffAccount).filter(StaffAccount.linked_person_id == user.id).one()
        if username:
            collision = db.query(StaffAccount).filter(
                StaffAccount.username == username,
                StaffAccount.id != account.id,
            ).first()
            if collision:
                raise ValueError(f"Username '{username}' is already in use.")
            account.username = username
        account.display_name = full_name
        account.password_hash = pw_hash
        account.active = True
        account.force_password_change = False
        apply_role_codes(db, account, ["superadmin" if role == "admin" else "teacher_editor"])
        db.commit()
        print(f"Staff account '{account.username}' is ready (role: {role}, ID: {account.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create/update admin user in School AI DB")
    parser.add_argument("--name",     required=True, help="Full name of the user")
    parser.add_argument("--password", default=None, help="Password; omit to enter it without shell history")
    parser.add_argument("--role",     default="admin", choices=["admin", "teacher"])
    parser.add_argument("--username", default=None, help="Separate login name (defaults to the full name)")
    args = parser.parse_args()
    password = args.password or getpass.getpass("Нова парола (поне 12 символа): ")
    if args.password is None:
        confirmation = getpass.getpass("Повторете паролата: ")
        if password != confirmation:
            parser.error("Паролите не съвпадат.")
    try:
        create_or_update_admin(args.name, password, args.role, args.username)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
