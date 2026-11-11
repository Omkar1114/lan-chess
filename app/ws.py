"""WebSocket endpoints: lobby feed, game rooms and engine analysis."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import chess
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth import websocket_user
from .manager import Connection

router = APIRouter()


async def _receive(ws: WebSocket) -> dict[str, Any] | None:
    """Next JSON object from the client; None for malformed frames."""
    text = await ws.receive_text()
    try:
        message = json.loads(text)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


@router.websocket("/ws/lobby")
async def lobby_socket(ws: WebSocket) -> None:
    """Pushes a full lobby snapshot whenever the list of games changes."""
    manager = ws.app.state.manager
    await ws.accept()
    await manager.lobby_connect(ws)
    try:
        while True:
            message = await _receive(ws)
            if message and message.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        manager.lobby_disconnect(ws)


@router.websocket("/ws/game/{game_id}")
async def game_socket(ws: WebSocket, game_id: str) -> None:
    """Room of one game. Players and spectators share the same channel."""
    manager = ws.app.state.manager
    await ws.accept()
    game = manager.games.get(game_id)
    if game is None:
        # Finished long ago (or never existed): the client falls back to the saved game.
        await ws.send_json({"type": "gone"})
        await ws.close()
        return

    user = websocket_user(ws)
    conn = Connection(ws, user["id"] if user else None, user["username"] if user else None)
    await manager.connect(game, conn)
    try:
        while True:
            message = await _receive(ws)
            if message is None:
                await ws.send_json({"type": "error", "message": "Malformed message"})
                continue
            await manager.handle(game, conn, message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await manager.disconnect(game, conn)


@router.websocket("/ws/analysis")
async def analysis_socket(ws: WebSocket) -> None:
    """Streams Stockfish evaluations for whatever position the client is looking at."""
    await ws.accept()
    session = await ws.app.state.engine.open_analysis()
    if session is None:
        await ws.send_json({"type": "unavailable"})
        await ws.close()
        return

    task: asyncio.Task | None = None

    async def stop_current() -> None:
        nonlocal task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            task = None

    async def run(fen: str, board: chess.Board) -> None:
        last_depth = 0
        async for info in session.analyse(board):
            if info["depth"] <= last_depth:
                continue
            last_depth = info["depth"]
            await ws.send_json({"type": "info", "fen": fen, **info})

    try:
        while True:
            message = await _receive(ws)
            if message is None:
                continue
            kind = message.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "stop":
                await stop_current()
            elif kind == "analyse":
                await stop_current()
                fen = str(message.get("fen", ""))
                try:
                    board = chess.Board(fen)
                except ValueError:
                    continue
                if not board.is_valid() or board.is_game_over():
                    continue  # nothing for the engine to search
                task = asyncio.create_task(run(fen, board))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await stop_current()
        await session.close()
