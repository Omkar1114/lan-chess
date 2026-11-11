"""PGN and JSON export."""

import io

import chess.pgn
import pytest

from app.game import LiveGame, TimeControl
from app.pgn import describe_outcome, game_to_json, game_to_pgn, games_to_pgn, positions

from .conftest import ALICE, BOB, WHITE, play


@pytest.fixture
def record(game):
    """Scholar's mate, one move every 2 seconds, in a 5+3 game."""
    play(game, "e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7#", start=2.0, step=2.0)
    return game.to_record()


def test_pgn_contains_the_standard_tags(record):
    pgn = game_to_pgn(record, site="Living room")
    headers = chess.pgn.read_game(io.StringIO(pgn)).headers
    assert headers["Event"] == "Casual game"
    assert headers["Site"] == "Living room"
    assert headers["Date"] == record["started_at"][:10].replace("-", ".")
    assert headers["White"] == "alice"
    assert headers["Black"] == "bob"
    assert headers["Result"] == "1-0"
    assert headers["TimeControl"] == "300+3"
    assert headers["Termination"] == "normal"
    # The Seven Tag Roster must come first and in this order.
    assert list(headers)[:7] == ["Event", "Site", "Date", "Round", "White", "Black", "Result"]


def test_pgn_round_trips_the_moves_and_clocks(record):
    parsed = chess.pgn.read_game(io.StringIO(game_to_pgn(record)))
    assert [m.uci() for m in parsed.mainline_moves()] == [m["uci"] for m in record["moves"]]
    assert parsed.end().board().is_checkmate()
    first = parsed.next()
    assert first.clock() == pytest.approx(301.0)  # 300 - 2 + 3
    assert "White wins by checkmate" in parsed.end().comment
    assert game_to_pgn(record).rstrip().endswith("1-0")


def test_pgn_termination_and_time_control_variants():
    timed_out = LiveGame("g1", ALICE, WHITE, TimeControl(60))
    timed_out.start(BOB, now=0.0)
    timed_out.move(WHITE, "e2e4", now=1.0)
    timed_out.check_flag(now=100.0)
    pgn = game_to_pgn(timed_out.to_record())
    assert '[Termination "time forfeit"]' in pgn and '[TimeControl "60+0"]' in pgn

    no_clock = LiveGame("g2", ALICE, WHITE, TimeControl(0))
    no_clock.start(BOB, now=0.0)
    no_clock.move(WHITE, "e2e4", now=1.0)
    no_clock.resign(WHITE, now=2.0)
    pgn = game_to_pgn(no_clock.to_record())
    assert '[TimeControl "-"]' in pgn and '[Result "0-1"]' in pgn
    assert "%clk" not in pgn


def test_bulk_export_holds_every_game(record):
    second = dict(record, id="other", result="1/2-1/2", termination="draw_agreement")
    stream = io.StringIO(games_to_pgn([record, second]))
    games = []
    while (parsed := chess.pgn.read_game(stream)) is not None:
        games.append(parsed)
    assert [g.headers["Result"] for g in games] == ["1-0", "1/2-1/2"]
    assert all(len(list(g.mainline_moves())) == 7 for g in games)
    assert games_to_pgn([]) == ""


def test_positions_give_a_fen_for_every_ply(record):
    fens = positions(record)
    assert len(fens) == len(record["moves"]) + 1
    assert fens[0] == chess.STARTING_FEN
    assert fens[1].startswith("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq")
    assert chess.Board(fens[-1]).is_checkmate()


def test_json_export(record):
    data = game_to_json(record)
    assert data["white"]["name"] == "alice" and data["result"] == "1-0"
    assert data["time_control"] == {"base": 300, "increment": 3, "category": "blitz"}
    assert data["outcome"] == "White wins by checkmate"
    assert [m["ply"] for m in data["moves"]] == list(range(1, 8))
    assert data["moves"][-1]["fen"] == data["final_fen"]


@pytest.mark.parametrize(
    ("result", "termination", "text"),
    [
        ("0-1", "resignation", "Black wins by resignation"),
        ("1/2-1/2", "stalemate", "Draw by stalemate"),
        ("1/2-1/2", "fifty_move_rule", "Draw by the fifty-move rule"),
    ],
)
def test_outcome_descriptions(result, termination, text):
    assert describe_outcome({"result": result, "termination": termination}) == text
