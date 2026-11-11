"""Request bodies and the user-preferences model (validated by pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class Prefs(BaseModel):
    """Board and piece customisation, stored per account as JSON."""

    board_theme: str = Field(default="brown", pattern=r"^[a-z0-9_-]{1,30}$")
    custom_light: str = Field(default="#f0d9b5", pattern=HEX_COLOR)
    custom_dark: str = Field(default="#b58863", pattern=HEX_COLOR)
    piece_set: str = Field(default="cburnett", pattern=r"^[a-z0-9_-]{1,30}$")
    show_coordinates: bool = True
    highlight_last_move: bool = True
    show_legal_moves: bool = True
    sounds: bool = True
    auto_queen: bool = False


class CreateGameBody(BaseModel):
    color: Literal["white", "black", "random"] = "random"
    base_seconds: int = Field(default=300, ge=0)
    increment_seconds: int = Field(default=0, ge=0)
    vs_computer: bool = False
    engine_level: int = Field(default=4, ge=1, le=8)


class PositionBody(BaseModel):
    """A position, optionally with a move to play on it (analysis board)."""

    fen: str = Field(min_length=10, max_length=120)
    uci: str | None = Field(default=None, min_length=4, max_length=5)
