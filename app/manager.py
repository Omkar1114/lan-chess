"""Game registry and real-time hub.

The manager owns every in-memory ``LiveGame``, the WebSocket connections
attached to them, the lobby subscribers, the flag (timeout) ticker, engine
moves and persistence of finished games. Everything runs on the single asyncio
event loop, and each game mutation happens without awaiting in between, so no
locking is required.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from .clock import BLACK, WHITE, other
from .db import Database
from .engine import LEVELS, EngineService
from .game import ABORTED, FINISHED, LIVE, OPEN, GameError, LiveGame, Player, TimeControl

log = logging.getLogger("lanchess.manager")

TICK_SECONDS = 0.25
OPEN_GAME_GRACE_SECONDS = 60  # an open game is cancelled when its host is gone this long
FINISHED_GAME_TTL_SECONDS = 15 * 60  # finished games stay in memory for rematches


@dataclass(eq=False)
class Connection:
    """One WebSocket attached to a game (a player or a spectator)."""

    ws: WebSocket
    user_id: int | None
    username: str | None


def engine_name(level: int) -> str:
    return f"Stockfish (level {level})"


class GameManager:
    def __init__(
        self,
        db: Database,
        engine: EngineService,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db = db
        self.engine = engine
        self.now = now
        self.games: dict[str, LiveGame] = {}
        self._conns: dict[str, set[Connection]] = {}
        self._lobby: set[WebSocket] = set()
        self._engine_tasks: dict[str, asyncio.Task] = {}
        self._host_seen: dict[str, float] = {}
        self._finished_at: dict[str, float] = {}
        self._ticker: asyncio.Task | None = None

    # -- background ticker ---------------------------------------------------

    def start(self) -> None:
        self._ticker = asyncio.create_task(self._tick_forever(), name="game-ticker")

    async def stop(self) -> None:
        tasks = [t for t in (self._ticker, *self._engine_tasks.values()) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.tick()
            except Exception:  # the ticker must survive anything
                log.exception("Ticker iteration failed")

    async def tick(self) -> None:
        """Flag players whose time ran out and clean up stale games."""
        now = self.now()
        lobby_changed = False
        for game in list(self.games.values()):
            if game.status == LIVE:
                if game.check_flag(now):
                    await self._after_change(game)
            elif game.status == OPEN:
                if self._is_connected(game, game.host.user_id):
                    self._host_seen[game.id] = now
                elif now - self._host_seen.get(game.id, now) > OPEN_GAME_GRACE_SECONDS:
                    game.abort()
                    await self._broadcast_state(game)
                    self._drop(game)
                    lobby_changed = True
            elif game.status == FINISHED:
                age = now - self._finished_at.get(game.id, now)
                idle = not self._conns.get(game.id)
                if (idle and age > 60) or age > FINISHED_GAME_TTL_SECONDS:
                    self._drop(game)
        if lobby_changed:
            await self.broadcast_lobby()

    def _drop(self, game: LiveGame) -> None:
        self.games.pop(game.id, None)
        self._host_seen.pop(game.id, None)
        self._finished_at.pop(game.id, None)
        task = self._engine_tasks.pop(game.id, None)
        if task is not None:
            task.cancel()

    # -- creating and joining ------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        return secrets.token_urlsafe(6)

    async def create_game(
        self,
        user: dict[str, Any],
        color: str,
        time_control: TimeControl,
        vs_computer: bool = False,
        engine_level: int = 4,
    ) -> LiveGame:
        """Host a new game. Games against the engine start immediately."""
        host = Player(user["id"], user["username"])
        for existing in self.games.values():
            if existing.status == OPEN and existing.host.user_id == host.user_id:
                raise GameError("You already have an open game waiting for an opponent")
        if vs_computer:
            if not self.engine.available:
                raise GameError("The chess engine is not available on this server")
            if engine_level not in LEVELS:
                raise GameError("Unknown difficulty level")

        game = LiveGame(
            self._new_id(),
            host,
            color,
            time_control,
            vs_computer=vs_computer,
            engine_level=engine_level if vs_computer else None,
        )
        self.games[game.id] = game
        self._host_seen[game.id] = self.now()
        if vs_computer:
            game.start(Player(None, engine_name(engine_level), is_engine=True), self.now())
        await self._after_change(game, lobby=True)
        return game

    async def join_game(self, game_id: str, user: dict[str, Any]) -> LiveGame:
        """Take the free seat of an open game."""
        game = self.games.get(game_id)
        if game is None:
            raise GameError("This game no longer exists")
        game.start(Player(user["id"], user["username"]), self.now())
        await self._after_change(game, lobby=True)
        return game

    async def admin_abort(self, game_id: str) -> bool:
        """Remove an unfinished game without saving it (admin action)."""
        game = self.games.get(game_id)
        if game is None or game.status == FINISHED:
            return False
        game.status = ABORTED
        game.termination = "aborted"
        await self._broadcast_state(game)
        self._drop(game)
        await self.broadcast_lobby()
        return True

    # -- connections ---------------------------------------------------------

    async def connect(self, game: LiveGame, conn: Connection) -> None:
        self._conns.setdefault(game.id, set()).add(conn)
        if conn.user_id is not None and conn.user_id == game.host.user_id:
            self._host_seen[game.id] = self.now()
        # Everyone gets a fresh state so presence dots and spectator counts update.
        await self._broadcast_state(game)

    async def disconnect(self, game: LiveGame, conn: Connection) -> None:
        conns = self._conns.get(game.id)
        if conns is None:
            return
        conns.discard(conn)
        if not conns:
            del self._conns[game.id]
        if game.id in self.games:
            await self._broadcast_state(game)

    def _is_connected(self, game: LiveGame, user_id: int | None) -> bool:
        if user_id is None:
            return False
        return any(c.user_id == user_id for c in self._conns.get(game.id, ()))

    # -- incoming messages ---------------------------------------------------

    async def handle(self, game: LiveGame, conn: Connection, message: dict[str, Any]) -> None:
        """Apply one client message. Spectators can only ping."""
        kind = message.get("type")
        if kind == "ping":
            await self._send(conn.ws, {"type": "pong"})
            return

        now = self.now()
        color = game.color_of(conn.user_id)
        try:
            if game.status == OPEN and kind == "abort":
                if conn.user_id != game.host.user_id:
                    raise GameError("Only the host can cancel this game")
                game.abort()
                await self._broadcast_state(game)
                self._drop(game)
                await self.broadcast_lobby()
                return
            if color is None:
                raise GameError("Spectators cannot do that")
            self._apply(game, color, kind, message, now)
        except GameError as exc:
            await self._send(conn.ws, {"type": "error", "message": str(exc)})
        # Even a rejected action may have ended the game (e.g. a move after the flag fell).
        await self._after_change(game)

    def _apply(self, game: LiveGame, color: str, kind: str, message: dict, now: float) -> None:
        engine_color = game.engine_color()
        if kind == "move":
            game.move(color, str(message.get("uci", "")), now)
        elif kind == "resign":
            game.resign(color, now)
        elif kind == "draw_offer":
            game.offer_draw(color)
            if engine_color:  # the engine never agrees to a draw
                game.respond_draw(engine_color, False, now)
                raise GameError("The computer declines the draw offer")
        elif kind in ("draw_accept", "draw_decline"):
            game.respond_draw(color, kind == "draw_accept", now)
        elif kind == "takeback_request":
            game.request_takeback(color)
            if engine_color:  # the engine always grants takebacks
                game.respond_takeback(engine_color, True, now)
        elif kind in ("takeback_accept", "takeback_decline"):
            game.respond_takeback(color, kind == "takeback_accept", now)
        elif kind == "rematch_offer":
            if game.offer_rematch(color) or engine_color:
                self._start_rematch(game, now)
        elif kind == "rematch_decline":
            game.decline_rematch(color)
        else:
            raise GameError("Unknown message type")

    def _start_rematch(self, old: LiveGame, now: float) -> None:
        """Create the return game with colours swapped and link it from the old one."""
        new_white, new_black = old.players[BLACK], old.players[WHITE]
        human_is_white = not new_white.is_engine
        host, opponent = (new_white, new_black) if human_is_white else (new_black, new_white)
        game = LiveGame(
            self._new_id(),
            host,
            WHITE if human_is_white else BLACK,
            old.time_control,
            vs_computer=old.vs_computer,
            engine_level=old.engine_level,
        )
        game.start(opponent, now)
        self.games[game.id] = game
        self._host_seen[game.id] = now
        old.rematch_game_id = game.id
        old.rematch_offer = None
        self._maybe_engine_turn(game)
        asyncio.get_running_loop().create_task(self.broadcast_lobby())

    # -- after every change --------------------------------------------------

    async def _after_change(self, game: LiveGame, lobby: bool = False) -> None:
        """Persist if finished, wake the engine if needed, and notify everyone."""
        if game.status == FINISHED and not game.saved:
            game.saved = True
            self._finished_at[game.id] = self.now()
            try:
                self.db.save_game(game.to_record())
            except Exception:  # never lose the live state over a DB error
                log.exception("Could not save game %s", game.id)
            lobby = True
        self._maybe_engine_turn(game)
        await self._broadcast_state(game)
        if lobby:
            await self.broadcast_lobby()

    def _maybe_engine_turn(self, game: LiveGame) -> None:
        if game.status != LIVE or game.engine_color() != game.turn:
            return
        task = self._engine_tasks.get(game.id)
        if task is not None and not task.done():
            task.cancel()  # position changed (takeback) while the engine was thinking
        self._engine_tasks[game.id] = asyncio.create_task(self._engine_turn(game))

    async def _engine_turn(self, game: LiveGame) -> None:
        """Let Stockfish pick and play a move for its side."""
        ply, color = len(game.moves), game.turn
        budget = None
        if not game.clock.unlimited:
            # Never spend more than a small slice of the engine's own clock.
            budget = game.clock.remaining_ms(color, self.now()) / 1000 / 25
        started = self.now()
        move = await self.engine.best_move(game.board.copy(), game.engine_level or 4, budget)
        # A short minimum delay feels more natural than an instant reply.
        await asyncio.sleep(max(0.0, 0.35 - (self.now() - started)))
        if game.status != LIVE or len(game.moves) != ply or game.turn != color:
            return  # game ended or a takeback happened meanwhile
        try:
            game.move(color, move.uci(), self.now())
        except GameError as exc:
            log.warning("Engine move rejected in game %s: %s", game.id, exc)
        await self._after_change(game)

    # -- outgoing messages ---------------------------------------------------

    @staticmethod
    async def _send(ws: WebSocket, payload: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):  # the socket may be closing
            await ws.send_json(payload)

    def state_for(self, game: LiveGame, conn: Connection | None = None) -> dict[str, Any]:
        """Game state plus presence, personalised with the receiver's role."""
        state = game.to_state(self.now())
        conns = self._conns.get(game.id, ())
        for color in (WHITE, BLACK):
            if state[color] is not None:
                player = game.players[color]
                state[color]["connected"] = player.is_engine or self._is_connected(
                    game, player.user_id
                )
        state["spectators"] = sum(1 for c in conns if game.color_of(c.user_id) is None)
        state["type"] = "state"
        state["you"] = game.color_of(conn.user_id) if conn else None
        state["is_host"] = bool(conn and conn.user_id == game.host.user_id)
        return state

    async def _broadcast_state(self, game: LiveGame) -> None:
        conns = list(self._conns.get(game.id, ()))
        if not conns:
            return
        base = self.state_for(game)
        await asyncio.gather(
            *(
                self._send(
                    c.ws,
                    {
                        **base,
                        "you": game.color_of(c.user_id),
                        "is_host": c.user_id is not None and c.user_id == game.host.user_id,
                    },
                )
                for c in conns
            )
        )

    # -- lobby ---------------------------------------------------------------

    def lobby_snapshot(self) -> dict[str, Any]:
        open_games, live_games = [], []
        for game in self.games.values():
            if game.status == OPEN:
                open_games.append(
                    {
                        "id": game.id,
                        "host": game.host.name,
                        "host_id": game.host.user_id,
                        # Colour the joining player will get.
                        "your_color": "random"
                        if game.color_pref == "random"
                        else other(game.color_pref),
                        "time_control": game.time_control.to_dict(),
                        "created_at": game.created_at,
                    }
                )
            elif game.status == LIVE:
                live_games.append(
                    {
                        "id": game.id,
                        "white": game.players[WHITE].to_dict(),
                        "black": game.players[BLACK].to_dict(),
                        "vs_computer": game.vs_computer,
                        "time_control": game.time_control.to_dict(),
                        "started_at": game.started_at,
                    }
                )
        return {"type": "lobby", "open": open_games, "live": live_games}

    async def lobby_connect(self, ws: WebSocket) -> None:
        self._lobby.add(ws)
        await self._send(ws, self.lobby_snapshot())

    def lobby_disconnect(self, ws: WebSocket) -> None:
        self._lobby.discard(ws)

    async def broadcast_lobby(self) -> None:
        if not self._lobby:
            return
        snapshot = self.lobby_snapshot()
        await asyncio.gather(*(self._send(ws, snapshot) for ws in list(self._lobby)))
