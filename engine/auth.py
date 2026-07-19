import os
import jwt
import datetime
from argon2 import PasswordHasher
from typing import Optional, Dict, Any

# Initialize Argon2 password hasher
ph = PasswordHasher()

def get_password_hash(password: str) -> str:
    """Hash a plain password using Argon2."""
    return ph.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against the stored Argon2 hash."""
    try:
        return ph.verify(hashed_password, plain_password)
    except Exception:
        return False

def create_access_token(data: Dict[str, Any], expires_delta: Optional[datetime.timedelta] = None) -> str:
    """Create a JWT token.
    data: payload dict (should include at least "sub" for user id)
    expires_delta: optional timedelta for expiration (default 30 minutes)
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
    to_encode.update({"exp": expire})
    secret_key = os.getenv("JWT_SECRET_KEY", "change_this_secret")
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    encoded_jwt = jwt.encode(to_encode, secret_key, algorithm=algorithm)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode a JWT token and return its payload. Returns None if invalid or expired."""
    secret_key = os.getenv("JWT_SECRET_KEY", "change_this_secret")
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
