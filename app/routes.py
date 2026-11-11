"""REST endpoints: configuration, games, exports, profiles and analysis helpers."""

from __future__ import annotations

from typing import Any

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import __version__
from .auth import require_admin, require_user
from .config import WEB_DIR
from .engine import LEVELS
from .game import PRESETS, GameError, TimeControl
from .pgn import describe_outcome, game_to_json, game_to_pgn, games_to_pgn, positions
from .schemas import CreateGameBody, PositionBody

router = APIRouter(prefix="/api")


def _piece_sets() -> list[str]:
    """Piece sets are simply the sub-folders of web/pieces."""
    folder = WEB_DIR / "pieces"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if (p / "wK.svg").is_file())


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    """Compact description of a saved game for lists."""
    tc = TimeControl(row["base_seconds"], row["increment_seconds"])
    return {
        "id": row["id"],
        "white": {"id": row["white_id"], "name": row["white_name"]},
        "black": {"id": row["black_id"], "name": row["black_name"]},
        "vs_computer": row["vs_computer"],
        "time_control": tc.to_dict(),
        "result": row["result"],
        "termination": row["termination"],
        "outcome": describe_outcome(row),
        "ply_count": row["ply_count"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


def _saved_game(request: Request, game_id: str) -> dict[str, Any]:
    record = request.app.state.db.game(game_id)
    if record is None:
        raise HTTPException(404, "Game not found")
    return record


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


# -- meta ----------------------------------------------------------------------


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe used by the Docker health check."""
    return {"status": "ok", "version": __version__}


@router.get("/config", tags=["meta"])
async def config(request: Request) -> dict[str, Any]:
    """Static information the frontend needs to render its forms."""
    state = request.app.state
    return {
        "site_name": state.settings.site_name,
        "version": __version__,
        "allow_registration": state.settings.allow_registration,
        "engine_available": state.engine.available,
        "engine_levels": [{"level": k, "name": v["name"]} for k, v in LEVELS.items()],
        "presets": PRESETS,
        "piece_sets": _piece_sets(),
    }


# -- live games ----------------------------------------------------------------


@router.post("/games", status_code=201, tags=["games"])
async def create_game(
    body: CreateGameBody, request: Request, user: dict[str, Any] = Depends(require_user)
) -> dict[str, str]:
    try:
        game = await request.app.state.manager.create_game(
            user,
            body.color,
            TimeControl(body.base_seconds, body.increment_seconds),
            vs_computer=body.vs_computer,
            engine_level=body.engine_level,
        )
    except GameError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"id": game.id}


@router.post("/games/{game_id}/join", tags=["games"])
async def join_game(
    game_id: str, request: Request, user: dict[str, Any] = Depends(require_user)
) -> dict[str, str]:
    try:
        game = await request.app.state.manager.join_game(game_id, user)
    except GameError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"id": game.id}


# -- saved games ---------------------------------------------------------------


@router.get("/games", tags=["games"])
async def list_games(
    request: Request,
    player: str | None = Query(default=None, max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """All finished games (public), newest first; ``player`` filters by username."""
    db = request.app.state.db
    user_id = None
    if player:
        user = db.user_by_name(player)
        if user is None:
            return {"games": [], "total": 0, "page": page, "page_size": page_size}
        user_id = user["id"]
    rows, total = db.list_games(user_id, page_size, (page - 1) * page_size)
    return {
        "games": [_summary(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/games/{game_id}", tags=["games"])
async def get_game(game_id: str, request: Request) -> dict[str, Any]:
    """A saved game with every position, or a pointer to the live game."""
    live = request.app.state.manager.games.get(game_id)
    if live is not None and not live.saved:
        return {"live": True, "id": game_id, "status": live.status}
    return {"live": False, **game_to_json(_saved_game(request, game_id))}


@router.get("/games/{game_id}/pgn", tags=["export"])
async def export_pgn(game_id: str, request: Request) -> PlainTextResponse:
    pgn = game_to_pgn(_saved_game(request, game_id), request.app.state.settings.site_name)
    return PlainTextResponse(
        pgn, media_type="application/x-chess-pgn", headers=_attachment(f"game-{game_id}.pgn")
    )


@router.get("/games/{game_id}/json", tags=["export"])
async def export_json(game_id: str, request: Request) -> JSONResponse:
    return JSONResponse(
        game_to_json(_saved_game(request, game_id)), headers=_attachment(f"game-{game_id}.json")
    )


@router.get("/games/{game_id}/fen", tags=["export"])
async def export_fen(game_id: str, request: Request, ply: int = Query(default=-1)) -> dict:
    """FEN after ``ply`` half-moves (0 = start position, -1 = final position)."""
    fens = positions(_saved_game(request, game_id))
    if ply < 0:
        ply = len(fens) - 1
    if ply >= len(fens):
        raise HTTPException(400, f"This game has only {len(fens) - 1} plies")
    return {"ply": ply, "fen": fens[ply]}


@router.delete("/games/{game_id}", status_code=204, tags=["admin"])
async def delete_game(
    game_id: str, request: Request, _: dict[str, Any] = Depends(require_admin)
) -> None:
    """Admin: abort an unfinished game or delete a saved one."""
    aborted = await request.app.state.manager.admin_abort(game_id)
    deleted = request.app.state.db.delete_game(game_id)
    if not (aborted or deleted):
        raise HTTPException(404, "Game not found")


# -- profiles ------------------------------------------------------------------


def _profile_user(request: Request, username: str) -> dict[str, Any]:
    user = request.app.state.db.user_by_name(username)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


@router.get("/users/{username}", tags=["users"])
async def profile(username: str, request: Request) -> dict[str, Any]:
    user = _profile_user(request, username)
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user["created_at"],
        "stats": request.app.state.db.user_stats(user["id"]),
    }


@router.get("/users/{username}/games.pgn", tags=["export"])
async def export_user_pgn(username: str, request: Request) -> PlainTextResponse:
    """Every game of a user in a single PGN file."""
    user = _profile_user(request, username)
    state = request.app.state
    pgn = games_to_pgn(state.db.all_games_of(user["id"]), state.settings.site_name)
    return PlainTextResponse(
        pgn,
        media_type="application/x-chess-pgn",
        headers=_attachment(f"{user['username']}-games.pgn"),
    )


# -- analysis helper -----------------------------------------------------------


@router.post("/position", tags=["analysis"])
async def position(body: PositionBody) -> dict[str, Any]:
    """Describe a position, optionally after playing ``uci`` on it.

    The analysis board uses this so that even free exploration is validated by
    python-chess on the server; the browser never implements chess rules.
    """
    try:
        board = chess.Board(body.fen)
    except ValueError:
        raise HTTPException(400, "Invalid FEN") from None
    if not board.is_valid():
        raise HTTPException(400, "Illegal position")

    san = None
    if body.uci:
        try:
            move = chess.Move.from_uci(body.uci)
        except ValueError:
            raise HTTPException(400, "Malformed move") from None
        if move not in board.legal_moves:
            raise HTTPException(400, "Illegal move")
        san = board.san(move)
        board.push(move)

    dests: dict[str, list[str]] = {}
    for move in board.legal_moves:
        targets = dests.setdefault(chess.square_name(move.from_square), [])
        target = chess.square_name(move.to_square)
        if target not in targets:
            targets.append(target)
    outcome = board.outcome()
    return {
        "fen": board.fen(),
        "san": san,
        "uci": body.uci,
        "turn": "white" if board.turn == chess.WHITE else "black",
        "dests": dests,
        "check": chess.square_name(board.king(board.turn)) if board.is_check() else None,
        "game_over": outcome is not None,
        "result": outcome.result() if outcome else None,
    }
