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

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from engine.db import Person, init_db
from engine.auth import get_password_hash

DATABASE_URL = "sqlite:///data/school_ai.db"


def create_or_update_admin(full_name: str, password: str, role: str = "admin"):
    engine = init_db(DATABASE_URL)
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
            print(f"Updated user '{full_name}' with new password (role: {role}).")
        else:
            user = Person(full_name=full_name, role=role, active=True, password_hash=pw_hash)
            db.add(user)
            db.commit()
            print(f"Created user '{full_name}' (role: {role}, ID: {user.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create/update admin user in School AI DB")
    parser.add_argument("--name",     required=True, help="Full name of the user")
    parser.add_argument("--password", required=True, help="Password for the user")
    parser.add_argument("--role",     default="admin", choices=["admin", "teacher"])
    args = parser.parse_args()
    create_or_update_admin(args.name, args.password, args.role)
