import os
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Initialize Argon2 password hasher
ph = PasswordHasher()

def get_password_hash(password: str) -> str:
    """Hash a plain password using Argon2."""
    return ph.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against the stored Argon2 hash."""
    try:
        return ph.verify(hashed_password, plain_password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False
