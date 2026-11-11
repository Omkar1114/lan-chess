"""Authoritative state of a single game.

``LiveGame`` contains every rule of a game session - move validation (delegated
to python-chess), clocks, draw offers, takebacks, resignation and end-of-game
detection - but performs no I/O. Networking, timers, the engine and persistence
live in ``manager.py``; this split is what makes the rules unit-testable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import chess

from .clock import BLACK, WHITE, ChessClock, other
from .db import iso, utc_now

OPEN = "open"  # waiting for an opponent
LIVE = "live"  # being played
FINISHED = "finished"
ABORTED = "aborted"  # cancelled before it started; never saved

# Named time-control presets offered in the UI: (label, base seconds, increment seconds).
PRESETS: list[dict[str, Any]] = [
    {"category": "bullet", "base": 60, "increment": 0},
    {"category": "bullet", "base": 120, "increment": 1},
    {"category": "blitz", "base": 180, "increment": 0},
    {"category": "blitz", "base": 180, "increment": 2},
    {"category": "blitz", "base": 300, "increment": 0},
    {"category": "blitz", "base": 300, "increment": 3},
    {"category": "rapid", "base": 600, "increment": 0},
    {"category": "rapid", "base": 600, "increment": 5},
    {"category": "rapid", "base": 900, "increment": 10},
    {"category": "classical", "base": 1800, "increment": 0},
    {"category": "classical", "base": 1800, "increment": 20},
]

MAX_BASE_SECONDS = 3 * 60 * 60
MAX_INCREMENT_SECONDS = 180


class GameError(Exception):
    """An action was rejected (illegal move, wrong turn, wrong state, ...)."""


@dataclass(frozen=True)
class TimeControl:
    """Base time plus Fischer increment. ``base_seconds == 0`` means no clock."""

    base_seconds: int
    increment_seconds: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.base_seconds <= MAX_BASE_SECONDS:
            raise GameError("Base time out of range")
        if not 0 <= self.increment_seconds <= MAX_INCREMENT_SECONDS:
            raise GameError("Increment out of range")
        if self.base_seconds == 0 and self.increment_seconds:
            raise GameError("A game without a clock cannot have an increment")

    @property
    def unlimited(self) -> bool:
        return self.base_seconds == 0

    @property
    def category(self) -> str:
        """Bullet/blitz/rapid/classical, by estimated duration of a 40-move game."""
        if self.unlimited:
            return "unlimited"
        estimate = self.base_seconds + 40 * self.increment_seconds
        if estimate < 180:
            return "bullet"
        if estimate < 480:
            return "blitz"
        if estimate < 1500:
            return "rapid"
        return "classical"

    @property
    def pgn(self) -> str:
        """Value of the PGN ``TimeControl`` tag."""
        return "-" if self.unlimited else f"{self.base_seconds}+{self.increment_seconds}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base_seconds,
            "increment": self.increment_seconds,
            "category": self.category,
        }


@dataclass(frozen=True)
class Player:
    """A participant: a registered user, or the engine (``user_id`` is None)."""

    user_id: int | None
    name: str
    is_engine: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.user_id, "name": self.name, "is_engine": self.is_engine}


@dataclass
class MoveRecord:
    """One played half-move and both clocks right after it."""

    uci: str
    san: str
    white_ms: int | None
    black_ms: int | None


class LiveGame:
    """One game from creation until it is finished and saved."""

    def __init__(
        self,
        game_id: str,
        host: Player,
        color_pref: str,
        time_control: TimeControl,
        *,
        vs_computer: bool = False,
        engine_level: int | None = None,
    ) -> None:
        if color_pref not in (WHITE, BLACK, "random"):
            raise GameError("Invalid colour choice")
        self.id = game_id
        self.host = host
        self.color_pref = color_pref
        self.time_control = time_control
        self.vs_computer = vs_computer
        self.engine_level = engine_level

        self.status = OPEN
        self.players: dict[str, Player | None] = {WHITE: None, BLACK: None}
        self.board = chess.Board()
        self.moves: list[MoveRecord] = []
        self.clock = ChessClock(
            time_control.base_seconds * 1000,
            time_control.increment_seconds * 1000,
            unlimited=time_control.unlimited,
        )

        self.draw_offer: str | None = None  # colour that offered
        self.takeback_request: str | None = None  # colour that asked
        self.rematch_offer: str | None = None
        self.rematch_game_id: str | None = None

        self.result = "*"
        self.termination: str | None = None
        self.created_at = iso(utc_now())
        self.started_at: str | None = None
        self.ended_at: str | None = None
        self.saved = False  # set by the manager once persisted

    # -- lifecycle ---------------------------------------------------------

    def start(self, opponent: Player, now: float, rng: random.Random | None = None) -> None:
        """Seat the opponent, resolve colours and start White's clock."""
        if self.status != OPEN:
            raise GameError("This game has already started")
        if opponent.user_id is not None and opponent.user_id == self.host.user_id:
            raise GameError("You cannot join your own game")
        host_color = self.color_pref
        if host_color == "random":
            host_color = (rng or random).choice([WHITE, BLACK])
        self.players[host_color] = self.host
        self.players[other(host_color)] = opponent
        self.status = LIVE
        self.started_at = iso(utc_now())
        self.clock.start(WHITE, now)

    def abort(self) -> None:
        """Cancel a game that never started."""
        if self.status != OPEN:
            raise GameError("Only a game without an opponent can be cancelled")
        self.status = ABORTED
        self.termination = "aborted"

    def _finish(self, result: str, termination: str, now: float) -> None:
        self.clock.stop(now)
        self.status = FINISHED
        self.result = result
        self.termination = termination
        self.ended_at = iso(utc_now())
        self.draw_offer = None
        self.takeback_request = None

    # -- queries -----------------------------------------------------------

    @property
    def turn(self) -> str:
        return WHITE if self.board.turn == chess.WHITE else BLACK

    def color_of(self, user_id: int | None) -> str | None:
        """Colour played by ``user_id`` (None for spectators and guests)."""
        if user_id is None:
            return None
        for color, player in self.players.items():
            if player is not None and player.user_id == user_id:
                return color
        return None

    def engine_color(self) -> str | None:
        for color, player in self.players.items():
            if player is not None and player.is_engine:
                return color
        return None

    def _require_live(self) -> None:
        if self.status != LIVE:
            raise GameError("The game is not in progress")

    # -- moves and clock ---------------------------------------------------

    def check_flag(self, now: float) -> bool:
        """End the game if the running clock hit zero. Returns True if it did."""
        if self.status != LIVE:
            return False
        loser = self.clock.flagged(now)
        if loser is None:
            return False
        winner = other(loser)
        # FIDE 6.9: no win on time if the opponent could never deliver mate.
        if self.board.has_insufficient_material(winner == WHITE):
            self._finish("1/2-1/2", "timeout_vs_insufficient_material", now)
        else:
            self._finish("1-0" if winner == WHITE else "0-1", "timeout", now)
        return True

    def move(self, color: str, uci: str, now: float) -> MoveRecord:
        """Validate and play a move for ``color``; detects the end of the game."""
        self._require_live()
        if self.check_flag(now):
            raise GameError("Time has run out")
        if color != self.turn:
            raise GameError("It is not your turn")
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise GameError("Malformed move") from None
        if move not in self.board.legal_moves:
            raise GameError("Illegal move")

        san = self.board.san(move)
        self.board.push(move)
        self.clock.press(color, now)
        snap = self.clock.snapshot(now)
        record = MoveRecord(uci=move.uci(), san=san, white_ms=snap[WHITE], black_ms=snap[BLACK])
        self.moves.append(record)

        # Moving implicitly declines the opponent's draw offer and voids takeback requests.
        if self.draw_offer == other(color):
            self.draw_offer = None
        self.takeback_request = None

        self._detect_end(now)
        return record

    def _detect_end(self, now: float) -> None:
        """Apply the automatic game-ending rules to the current position."""
        board = self.board
        if board.is_checkmate():
            # The side to move is mated, so the side that just moved wins.
            self._finish("0-1" if board.turn == chess.WHITE else "1-0", "checkmate", now)
        elif board.is_stalemate():
            self._finish("1/2-1/2", "stalemate", now)
        elif board.is_insufficient_material():
            self._finish("1/2-1/2", "insufficient_material", now)
        elif board.is_repetition(3):
            self._finish("1/2-1/2", "threefold_repetition", now)
        elif board.is_fifty_moves():
            self._finish("1/2-1/2", "fifty_move_rule", now)

    # -- resign / draw -----------------------------------------------------

    def resign(self, color: str, now: float) -> None:
        self._require_live()
        self._finish("0-1" if color == WHITE else "1-0", "resignation", now)

    def offer_draw(self, color: str) -> None:
        self._require_live()
        if self.draw_offer == color:
            raise GameError("You have already offered a draw")
        if self.draw_offer == other(color):
            raise GameError("Your opponent has offered a draw - accept or decline it")
        self.draw_offer = color

    def respond_draw(self, color: str, accept: bool, now: float) -> None:
        self._require_live()
        if self.draw_offer != other(color):
            raise GameError("There is no draw offer to answer")
        if accept:
            self._finish("1/2-1/2", "draw_agreement", now)
        else:
            self.draw_offer = None

    # -- takeback ----------------------------------------------------------

    def _plies_by(self, color: str) -> int:
        total = len(self.moves)
        return (total + 1) // 2 if color == WHITE else total // 2

    def request_takeback(self, color: str) -> None:
        self._require_live()
        if self._plies_by(color) == 0:
            raise GameError("You have not made a move yet")
        if self.takeback_request is not None:
            raise GameError("A takeback request is already pending")
        self.takeback_request = color

    def respond_takeback(self, color: str, accept: bool, now: float) -> None:
        """Answer the opponent's request; accepting rewinds to the requester's turn."""
        self._require_live()
        requester = other(color)
        if self.takeback_request != requester:
            raise GameError("There is no takeback request to answer")
        self.takeback_request = None
        if not accept:
            return
        # Undo the requester's last move, plus the reply if one was already played.
        plies = 1 if self.turn != requester else 2
        for _ in range(plies):
            self.board.pop()
            self.moves.pop()
        self.draw_offer = None
        if not self.clock.unlimited:
            if self.moves:
                white_ms, black_ms = self.moves[-1].white_ms, self.moves[-1].black_ms
            else:
                white_ms = black_ms = self.time_control.base_seconds * 1000
            self.clock.restore(white_ms, black_ms, self.turn, now)
        else:
            self.clock.start(self.turn, now)

    # -- rematch -----------------------------------------------------------

    def offer_rematch(self, color: str) -> bool:
        """Register a rematch offer. Returns True when both sides now agree."""
        if self.status != FINISHED:
            raise GameError("A rematch is only possible after the game")
        if self.rematch_game_id is not None:
            raise GameError("The rematch has already started")
        if self.rematch_offer == other(color):
            return True
        self.rematch_offer = color
        return False

    def decline_rematch(self, color: str) -> None:
        if self.rematch_offer == other(color):
            self.rematch_offer = None

    # -- serialization -----------------------------------------------------

    def legal_destinations(self) -> dict[str, list[str]]:
        """Legal moves as ``{from_square: [to_square, ...]}`` for move hints."""
        dests: dict[str, list[str]] = {}
        for move in self.board.legal_moves:
            targets = dests.setdefault(chess.square_name(move.from_square), [])
            target = chess.square_name(move.to_square)
            if target not in targets:  # promotions share a target square
                targets.append(target)
        return dests

    def winner(self) -> str | None:
        return {"1-0": WHITE, "0-1": BLACK}.get(self.result)

    def to_state(self, now: float) -> dict[str, Any]:
        """Full public state of the game; broadcast after every change."""
        check_square = None
        if self.board.is_check():
            check_square = chess.square_name(self.board.king(self.board.turn))
        live = self.status == LIVE
        return {
            "id": self.id,
            "status": self.status,
            "vs_computer": self.vs_computer,
            "engine_level": self.engine_level,
            "time_control": self.time_control.to_dict(),
            "host": self.host.name,
            "color_pref": self.color_pref,
            WHITE: self.players[WHITE].to_dict() if self.players[WHITE] else None,
            BLACK: self.players[BLACK].to_dict() if self.players[BLACK] else None,
            "fen": self.board.fen(),
            "turn": self.turn,
            "moves": [{"san": m.san, "uci": m.uci} for m in self.moves],
            "last_move": self.moves[-1].uci if self.moves else None,
            "check": check_square,
            "dests": self.legal_destinations() if live else {},
            "clock": self.clock.snapshot(now),
            "draw_offer": self.draw_offer,
            "takeback_request": self.takeback_request,
            "result": self.result,
            "termination": self.termination,
            "winner": self.winner(),
            "rematch": {"offered_by": self.rematch_offer, "game_id": self.rematch_game_id},
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    def to_record(self) -> dict[str, Any]:
        """Row to persist once the game is finished (see db.Database.save_game)."""
        white, black = self.players[WHITE], self.players[BLACK]
        if self.status != FINISHED or white is None or black is None:
            raise GameError("Only finished games can be saved")
        return {
            "id": self.id,
            "white_id": white.user_id,
            "black_id": black.user_id,
            "white_name": white.name,
            "black_name": black.name,
            "vs_computer": self.vs_computer,
            "engine_level": self.engine_level,
            "base_seconds": self.time_control.base_seconds,
            "increment_seconds": self.time_control.increment_seconds,
            "result": self.result,
            "termination": self.termination,
            "moves": [
                {"uci": m.uci, "san": m.san, "white_ms": m.white_ms, "black_ms": m.black_ms}
                for m in self.moves
            ],
            "ply_count": len(self.moves),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }
