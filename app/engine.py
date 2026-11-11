"""Stockfish integration over UCI.

Stockfish is a separate GPLv3 program. It is never linked into this code base:
we start the binary found on the system as a child process and talk to it over
stdin/stdout using the UCI text protocol (through python-chess).

Two usages:
* play:     one shared, long-lived process answers "best move" requests in turn;
* analysis: each open analysis board gets its own process for the lifetime of
            its WebSocket, capped by ``max_analysis`` to bound CPU usage.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from typing import Any

import chess
import chess.engine

log = logging.getLogger("lanchess.engine")

# Difficulty levels: Stockfish "Skill Level" (0-20), search depth cap and time cap.
LEVELS: dict[int, dict[str, Any]] = {
    1: {"name": "Beginner", "skill": 0, "depth": 1, "time": 0.05},
    2: {"name": "Novice", "skill": 2, "depth": 2, "time": 0.1},
    3: {"name": "Casual", "skill": 5, "depth": 4, "time": 0.2},
    4: {"name": "Club", "skill": 8, "depth": 6, "time": 0.3},
    5: {"name": "Strong club", "skill": 11, "depth": 8, "time": 0.5},
    6: {"name": "Expert", "skill": 14, "depth": 11, "time": 0.8},
    7: {"name": "Master", "skill": 17, "depth": 14, "time": 1.2},
    8: {"name": "Maximum", "skill": 20, "depth": 20, "time": 2.0},
}

ANALYSIS_DEPTH = 26
ANALYSIS_SECONDS = 20.0


class AnalysisSession:
    """A dedicated engine process for one analysis board."""

    def __init__(self, service: EngineService, engine: chess.engine.UciProtocol) -> None:
        self._service = service
        self._engine = engine

    async def analyse(self, board: chess.Board):
        """Yield successively deeper evaluations of ``board`` (async generator)."""
        limit = chess.engine.Limit(depth=ANALYSIS_DEPTH, time=ANALYSIS_SECONDS)
        with await self._engine.analysis(board, limit) as analysis:
            async for info in analysis:
                score, pv = info.get("score"), info.get("pv")
                if score is None or not pv:
                    continue
                white = score.white()
                yield {
                    "depth": info.get("depth", 0),
                    "cp": white.score(),
                    "mate": white.mate(),
                    "best": pv[0].uci(),
                    "line": board.variation_san(pv[:12]),
                }

    async def close(self) -> None:
        with contextlib.suppress(Exception):  # the process may already be gone
            await asyncio.wait_for(self._engine.quit(), timeout=2)
        self._service._analysis_closed()


class EngineService:
    """Owns all Stockfish processes of the application."""

    def __init__(self, path: str, threads: int = 1, hash_mb: int = 32, max_analysis: int = 2):
        self.path = path
        self.threads = threads
        self.hash_mb = hash_mb
        self.max_analysis = max_analysis
        self._play_engine: chess.engine.UciProtocol | None = None
        self._play_lock = asyncio.Lock()
        self._analysis_count = 0

    @property
    def available(self) -> bool:
        return bool(self.path)

    async def _spawn(self) -> chess.engine.UciProtocol:
        _, engine = await chess.engine.popen_uci(self.path)
        await engine.configure({"Threads": self.threads, "Hash": self.hash_mb})
        return engine

    async def best_move(
        self, board: chess.Board, level: int, max_time: float | None = None
    ) -> chess.Move:
        """Ask the play engine for a move at the given difficulty level."""
        params = LEVELS.get(level, LEVELS[4])
        think = params["time"] if max_time is None else max(0.01, min(params["time"], max_time))
        limit = chess.engine.Limit(time=think, depth=params["depth"])
        async with self._play_lock:
            for attempt in (1, 2):  # restart once if the process died
                try:
                    if self._play_engine is None:
                        self._play_engine = await self._spawn()
                    result = await self._play_engine.play(
                        board, limit, options={"Skill Level": params["skill"]}
                    )
                    if result.move is not None:
                        return result.move
                    break
                except (chess.engine.EngineError, OSError) as exc:
                    log.warning("Play engine failed (attempt %d): %s", attempt, exc)
                    self._play_engine = None
        # Last resort so a game never gets stuck on a broken engine.
        return random.choice(list(board.legal_moves))

    async def open_analysis(self) -> AnalysisSession | None:
        """Start an analysis engine, or return None when unavailable / at capacity."""
        if not self.available or self._analysis_count >= self.max_analysis:
            return None
        self._analysis_count += 1
        try:
            engine = await self._spawn()
        except (chess.engine.EngineError, OSError) as exc:
            self._analysis_count -= 1
            log.warning("Could not start analysis engine: %s", exc)
            return None
        return AnalysisSession(self, engine)

    def _analysis_closed(self) -> None:
        self._analysis_count = max(0, self._analysis_count - 1)

    async def close(self) -> None:
        if self._play_engine is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._play_engine.quit(), timeout=2)
            self._play_engine = None
