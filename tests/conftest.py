"""Shared fixtures."""

from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from app.clock import BLACK, WHITE
from app.config import Settings, _resolve_stockfish
from app.game import LiveGame, Player, TimeControl
from app.main import create_app

ALICE = Player(1, "alice")
BOB = Player(2, "bob")


@pytest.fixture
def game() -> LiveGame:
    """A started 5+3 game: alice is White, bob is Black, clock started at t=0."""
    live = LiveGame("test01", ALICE, WHITE, TimeControl(300, 3))
    live.start(BOB, now=0.0, rng=random.Random(0))
    return live


def play(live: LiveGame, sans: str, start: float = 1.0, step: float = 1.0) -> float:
    """Play space-separated SAN moves, one every ``step`` seconds. Returns the last time."""
    now = start
    for san in sans.split():
        color = live.turn
        live.move(color, live.board.parse_san(san).uci(), now)
        now += step
    return now - step


@pytest.fixture
def client(tmp_path):
    """HTTP/WebSocket client against a fresh app with its own temporary database."""
    settings = Settings(
        data_dir=tmp_path,
        secret_key="test-secret",
        admin_username="admin",
        admin_password="admin-password",
        stockfish_path=_resolve_stockfish(""),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


__all__ = ["ALICE", "BLACK", "BOB", "WHITE", "play"]
