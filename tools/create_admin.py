"""
Utility script to create or update an admin user in the School AI database.
Run this once to set up the admin credentials.

Usage:
    python tools/create_admin.py --name "Администратор" --password "YourSecurePassword"
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy.orm import sessionmaker
from engine import admin_models as _admin_models  # noqa: F401
from engine.admin_models import StaffAccount
from engine.db import Person, init_db
from engine.auth import get_password_hash
from web.services.admin_control import apply_role_codes, ensure_admin_foundation


def create_or_update_admin(full_name: str, password: str, role: str = "admin", username: str | None = None):
    engine = init_db()
    Session = sessionmaker(bind=engine)
    db = Session()
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
    parser.add_argument("--password", required=True, help="Password for the user")
    parser.add_argument("--role",     default="admin", choices=["admin", "teacher"])
    parser.add_argument("--username", default=None, help="Separate login name (defaults to the full name)")
    args = parser.parse_args()
    create_or_update_admin(args.name, args.password, args.role, args.username)
