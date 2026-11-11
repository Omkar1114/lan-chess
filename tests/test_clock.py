"""Server-authoritative clock."""

import pytest

from app.clock import BLACK, WHITE, ChessClock


def test_counts_down_only_for_the_running_side():
    clock = ChessClock(60_000)
    clock.start(WHITE, now=10.0)
    assert clock.remaining_ms(WHITE, now=12.5) == 57_500
    assert clock.remaining_ms(BLACK, now=12.5) == 60_000


def test_press_banks_time_adds_increment_and_switches():
    clock = ChessClock(60_000, increment_ms=2_000)
    clock.start(WHITE, now=0.0)
    clock.press(WHITE, now=5.0)
    assert clock.remaining[WHITE] == 57_000  # 60 - 5 + 2
    assert clock.running == BLACK
    clock.press(BLACK, now=6.0)
    assert clock.remaining[BLACK] == 61_000  # 60 - 1 + 2
    assert clock.running == WHITE


def test_press_by_the_wrong_side_is_rejected():
    clock = ChessClock(60_000)
    clock.start(WHITE, now=0.0)
    with pytest.raises(ValueError):
        clock.press(BLACK, now=1.0)


def test_flag_falls_exactly_at_zero_and_never_goes_negative():
    clock = ChessClock(3_000)
    clock.start(WHITE, now=0.0)
    assert clock.flagged(now=2.999) is None
    assert clock.flagged(now=3.0) == WHITE
    assert clock.remaining_ms(WHITE, now=99.0) == 0


def test_stop_freezes_both_sides():
    clock = ChessClock(10_000)
    clock.start(BLACK, now=0.0)
    clock.stop(now=4.0)
    assert clock.running is None
    assert clock.remaining_ms(BLACK, now=100.0) == 6_000
    assert clock.flagged(now=100.0) is None


def test_restore_resets_times_and_running_side():
    clock = ChessClock(10_000)
    clock.start(WHITE, now=0.0)
    clock.restore(7_000, 8_000, BLACK, now=50.0)
    assert clock.snapshot(now=51.0) == {WHITE: 7_000, BLACK: 7_000, "running": BLACK}


def test_unlimited_clock_never_flags():
    clock = ChessClock(0, unlimited=True)
    clock.start(WHITE, now=0.0)
    clock.press(WHITE, now=10_000.0)
    assert clock.flagged(now=99_999.0) is None
    assert clock.snapshot(now=1.0) == {WHITE: None, BLACK: None, "running": None}
