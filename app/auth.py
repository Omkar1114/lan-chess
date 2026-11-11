"""Account endpoints and the helpers that identify the caller."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket

from .schemas import Credentials, LoginBody, Prefs
from .security import hash_password, new_session_token, token_digest, verify_password

COOKIE_NAME = "lanchess_session"
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60

router = APIRouter(prefix="/api", tags=["accounts"])

# username (lower-case) -> (failed attempts, locked until [monotonic seconds])
_failed_logins: dict[str, tuple[int, float]] = {}


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """The part of a user row that is safe to send to its owner."""
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "prefs": Prefs(**_load_prefs(user)).model_dump(),
    }


def _load_prefs(user: dict[str, Any]) -> dict[str, Any]:
    try:
        stored = json.loads(user.get("prefs") or "{}")
        Prefs(**stored)  # stored data from an older version might not validate
        return stored
    except (ValueError, TypeError):
        return {}


def _user_from_token(app_state: Any, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    return app_state.db.session_user(token_digest(token, app_state.settings.secret_key))


def optional_user(request: Request) -> dict[str, Any] | None:
    """Dependency: the logged-in user, or None for guests."""
    return _user_from_token(request.app.state, request.cookies.get(COOKIE_NAME))


def require_user(user: dict[str, Any] | None = Depends(optional_user)) -> dict[str, Any]:
    """Dependency: the logged-in user, or HTTP 401."""
    if user is None:
        raise HTTPException(401, "Please log in first")
    return user


def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if not user["is_admin"]:
        raise HTTPException(403, "Administrator rights required")
    return user


def websocket_user(ws: WebSocket) -> dict[str, Any] | None:
    """Identify the user behind a WebSocket through the same session cookie."""
    return _user_from_token(ws.app.state, ws.cookies.get(COOKIE_NAME))


def _start_session(request: Request, response: Response, user_id: int) -> None:
    state = request.app.state
    token = new_session_token()
    state.db.create_session(
        token_digest(token, state.settings.secret_key), user_id, state.settings.session_days
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=state.settings.session_days * 86400,
        httponly=True,  # not readable from JavaScript
        samesite="lax",  # not sent on cross-site POSTs
        secure=request.url.scheme == "https",
        path="/",
    )


# Endpoints below are plain ``def`` on purpose: password hashing is CPU-bound, so
# FastAPI runs them in its thread pool instead of blocking the event loop.


@router.post("/auth/register", status_code=201)
def register(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    state = request.app.state
    if not state.settings.allow_registration:
        raise HTTPException(403, "Registration is disabled on this server")
    if body.username.lower().startswith("stockfish"):
        raise HTTPException(400, "This username is reserved")
    if state.db.user_by_name(body.username) is not None:
        raise HTTPException(409, "This username is already taken")
    user_id = state.db.create_user(body.username, hash_password(body.password))
    _start_session(request, response, user_id)
    return public_user(state.db.user_by_id(user_id))


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    state = request.app.state
    key = body.username.lower()
    fails, locked_until = _failed_logins.get(key, (0, 0.0))
    if time.monotonic() < locked_until:
        raise HTTPException(429, "Too many failed attempts - try again in a minute")

    user = state.db.user_by_name(body.username)
    if user is None or not verify_password(body.password, user["password_hash"]):
        fails += 1
        lock = time.monotonic() + LOCKOUT_SECONDS if fails >= MAX_FAILED_LOGINS else 0.0
        _failed_logins[key] = (0 if lock else fails, lock)
        raise HTTPException(401, "Wrong username or password")

    _failed_logins.pop(key, None)
    _start_session(request, response, user["id"])
    return public_user(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        state = request.app.state
        state.db.delete_session(token_digest(token, state.settings.secret_key))
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me")
def me(user: dict[str, Any] | None = Depends(optional_user)) -> dict[str, Any]:
    return {"user": public_user(user) if user else None}


@router.put("/me/prefs")
def save_prefs(
    prefs: Prefs, request: Request, user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    request.app.state.db.set_prefs(user["id"], prefs.model_dump())
    return prefs.model_dump()
