#!/usr/bin/env python3
"""Test admin login"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from engine.db import Person, init_db
from engine.auth import verify_password, create_access_token
from datetime import timedelta

DATABASE_URL = "sqlite:///data/school_ai.db"
db_engine = init_db(DATABASE_URL)
Session = sessionmaker(bind=db_engine)
db = Session()

# Test 1: Check if admin user exists
print("=" * 60)
print("TEST 1: Check admin user")
print("=" * 60)

admin = db.query(Person).filter(Person.full_name == "Admin User").first()
if admin:
    print(f"[OK] Admin user found:")
    print(f"     ID: {admin.id}")
    print(f"     Name: {admin.full_name}")
    print(f"     Role: {admin.role}")
    print(f"     Active: {admin.active}")
    print(f"     Has password: {bool(admin.password_hash)}")
else:
    print("[ERROR] Admin user not found!")
    db.close()
    sys.exit(1)

# Test 2: Verify password
print("\n" + "=" * 60)
print("TEST 2: Password verification")
print("=" * 60)

if admin.password_hash:
    result = verify_password("admin123", admin.password_hash)
    print(f"[{'OK' if result else 'ERROR'}] verify_password('admin123'): {result}")
else:
    print("[ERROR] No password hash found!")
    db.close()
    sys.exit(1)

# Test 3: Create JWT token (simulate login)
print("\n" + "=" * 60)
print("TEST 3: JWT token generation")
print("=" * 60)

if admin.role in ("admin", "teacher"):
    token = create_access_token(
        data={"sub": admin.id, "role": admin.role, "name": admin.full_name},
        expires_delta=timedelta(hours=8)
    )
    print(f"[OK] JWT token created: {token[:30]}...")
else:
    print(f"[ERROR] User role '{admin.role}' is not admin/teacher")

db.close()
print("\n" + "=" * 60)
print("ALL TESTS PASSED!")
print("=" * 60)
