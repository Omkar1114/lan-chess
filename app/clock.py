"""Server-authoritative chess clock with Fischer increment.

The clock never reads the system time itself: every method receives ``now``
(seconds from a monotonic source). That keeps it deterministic and trivially
testable, and guarantees one consistent timestamp per handled event.
"""

from __future__ import annotations

WHITE = "white"
BLACK = "black"


def other(color: str) -> str:
    """Return the opposite colour."""
    return BLACK if color == WHITE else WHITE


class ChessClock:
    """Two countdown timers of which at most one is running."""

    def __init__(self, base_ms: int, increment_ms: int = 0, unlimited: bool = False) -> None:
        self.increment_ms = increment_ms
        self.unlimited = unlimited
        self.remaining: dict[str, int] = {WHITE: base_ms, BLACK: base_ms}
        self.running: str | None = None
        self._since: float = 0.0

    def start(self, color: str, now: float) -> None:
        """Start (or switch to) ``color``'s timer without touching stored times."""
        self.running = color
        self._since = now

    def remaining_ms(self, color: str, now: float) -> int:
        """Time left for ``color`` at ``now``, never negative."""
        value = self.remaining[color]
        if self.running == color and not self.unlimited:
            value -= int((now - self._since) * 1000)
        return max(0, value)

    def flagged(self, now: float) -> str | None:
        """Return the colour whose time has run out, if any."""
        if self.unlimited or self.running is None:
            return None
        return self.running if self.remaining_ms(self.running, now) <= 0 else None

    def press(self, color: str, now: float) -> None:
        """``color`` completed a move: bank the time, add the increment, switch sides."""
        if self.running != color:
            raise ValueError(f"{color}'s clock is not running")
        if not self.unlimited:
            self.remaining[color] = self.remaining_ms(color, now) + self.increment_ms
        self.start(other(color), now)

    def stop(self, now: float) -> None:
        """Freeze both timers (game over)."""
        if self.running is not None:
            self.remaining[self.running] = self.remaining_ms(self.running, now)
            self.running = None

    def restore(self, white_ms: int, black_ms: int, running: str | None, now: float) -> None:
        """Reset both timers to known values (used when a takeback is accepted)."""
        self.remaining = {WHITE: white_ms, BLACK: black_ms}
        self.running = running
        self._since = now

    def snapshot(self, now: float) -> dict[str, int | str | None]:
        """Serializable view sent to clients, which then tick locally."""
        if self.unlimited:
            return {WHITE: None, BLACK: None, "running": None}
        return {
            WHITE: self.remaining_ms(WHITE, now),
            BLACK: self.remaining_ms(BLACK, now),
            "running": self.running,
        }
