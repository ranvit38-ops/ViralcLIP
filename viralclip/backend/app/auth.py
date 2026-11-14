"""Email/password auth with server-side sessions. Stdlib crypto only (scrypt + sha256)."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AuthSession, User, now

COOKIE = "vc_session"
router = APIRouter(prefix="/api/auth", tags=["auth"])


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(calc.hex(), h)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(vc_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    if vc_session:
        s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(vc_session)))
        if s and s.expires_at.replace(tzinfo=timezone.utc) > now():
            return db.get(User, s.user_id)
    raise HTTPException(401, "Not signed in")


class Credentials(BaseModel):
    email: str
    password: str


def _start_session(db: Session, user: User, resp: Response):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(token), expires_at=expires))
    db.commit()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=config.SECURE_COOKIES,
                    max_age=config.SESSION_DAYS * 86400, path="/")


@router.post("/register")
def register(c: Credentials, resp: Response, db: Session = Depends(get_db)):
    if not config.ALLOW_SIGNUP:
        raise HTTPException(403, "Sign-up is disabled on this server")
    email = c.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Enter a valid email address")
    if len(c.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, password_hash=hash_password(c.password))
    db.add(user)
    db.commit()
    _start_session(db, user, resp)
    return {"id": user.id, "email": user.email}


@router.post("/login")
def login(c: Credentials, resp: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == c.email.strip().lower()))
    if not user or not verify_password(c.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    _start_session(db, user, resp)
    return {"id": user.id, "email": user.email}


@router.post("/logout")
def logout(resp: Response, vc_session: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    if vc_session:
        s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(vc_session)))
        if s:
            db.delete(s)
            db.commit()
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email}
