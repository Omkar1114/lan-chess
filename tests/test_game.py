"""Game rules: moves, special moves, endings and the game clock."""

import random

import pytest

from app.game import FINISHED, LIVE, OPEN, GameError, LiveGame, TimeControl

from .conftest import ALICE, BLACK, BOB, WHITE, play

# -- setup ---------------------------------------------------------------------


def test_host_colour_choice_is_respected():
    live = LiveGame("g", ALICE, BLACK, TimeControl(300))
    assert live.status == OPEN
    live.start(BOB, now=0.0)
    assert live.players[BLACK] == ALICE and live.players[WHITE] == BOB
    assert live.status == LIVE and live.clock.running == WHITE


def test_random_colour_uses_the_rng():
    seen = set()
    for seed in range(20):
        live = LiveGame("g", ALICE, "random", TimeControl(300))
        live.start(BOB, now=0.0, rng=random.Random(seed))
        seen.add(live.color_of(ALICE.user_id))
    assert seen == {WHITE, BLACK}


def test_cannot_join_own_game_or_a_started_game():
    live = LiveGame("g", ALICE, WHITE, TimeControl(300))
    with pytest.raises(GameError):
        live.start(ALICE, now=0.0)
    live.start(BOB, now=0.0)
    with pytest.raises(GameError):
        live.start(BOB, now=0.0)


def test_time_control_validation_and_categories():
    assert TimeControl(60, 0).category == "bullet"
    assert TimeControl(300, 3).category == "blitz"
    assert TimeControl(600, 5).category == "rapid"
    assert TimeControl(1800, 20).category == "classical"
    assert TimeControl(0, 0).unlimited
    with pytest.raises(GameError):
        TimeControl(-1, 0)
    with pytest.raises(GameError):
        TimeControl(0, 5)


# -- move validation -----------------------------------------------------------


def test_legal_move_is_recorded_with_san(game):
    record = game.move(WHITE, "e2e4", now=1.0)
    assert record.san == "e4"
    assert game.turn == BLACK
    assert game.to_state(1.0)["last_move"] == "e2e4"


@pytest.mark.parametrize("uci", ["e2e5", "e7e5", "zzzz", "", "e1g1"])
def test_illegal_or_malformed_moves_are_rejected(game, uci):
    with pytest.raises(GameError):
        game.move(WHITE, uci, now=1.0)
    assert game.moves == []


def test_moving_out_of_turn_is_rejected(game):
    with pytest.raises(GameError, match="not your turn"):
        game.move(BLACK, "e7e5", now=1.0)


def test_legal_destinations_cover_the_start_position(game):
    dests = game.legal_destinations()
    assert sorted(dests["e2"]) == ["e3", "e4"]
    assert sorted(dests["g1"]) == ["f3", "h3"]
    assert sum(len(v) for v in dests.values()) == 20


# -- special moves -------------------------------------------------------------


def test_castling_both_sides(game):
    play(game, "e4 e5 Nf3 Nc6 Bc4 Bc5 d3 d6 Be3 Bg4 Nc3 Qd7 Qd2")
    assert game.move(BLACK, "e8c8", now=20.0).san == "O-O-O"
    assert game.move(WHITE, "e1g1", now=21.0).san == "O-O"
    assert game.board.piece_at(6).symbol() == "K"  # g1


def test_en_passant(game):
    play(game, "e4 a6 e5 d5")
    record = game.move(WHITE, "e5d6", now=10.0)
    assert record.san == "exd6"
    assert game.board.piece_at(35) is None  # the d5 pawn is gone


def test_promotion_requires_and_honours_the_piece_choice(game):
    game.board.set_fen("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    with pytest.raises(GameError):
        game.move(WHITE, "a7a8", now=1.0)  # no piece chosen
    assert game.move(WHITE, "a7a8n", now=1.0).san == "a8=N"
    # Promotions to the same square appear once in the destination hints.
    game.board.set_fen("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    assert game.legal_destinations()["a7"] == ["a8"]


# -- automatic endings ---------------------------------------------------------


def test_checkmate(game):
    play(game, "e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7#")
    assert (game.status, game.result, game.termination) == (FINISHED, "1-0", "checkmate")
    assert game.winner() == WHITE
    assert game.clock.running is None
    with pytest.raises(GameError):
        game.move(BLACK, "e8f7", now=30.0)


def test_stalemate(game):
    game.board.set_fen("7k/5Q2/8/8/8/8/8/K7 w - - 0 1")
    game.move(WHITE, "f7g6", now=1.0)
    assert (game.result, game.termination) == ("1/2-1/2", "stalemate")


def test_threefold_repetition(game):
    play(game, "Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1")
    assert game.status == LIVE
    play(game, "Ng8", start=20.0)  # start position for the third time
    assert (game.result, game.termination) == ("1/2-1/2", "threefold_repetition")


def test_fifty_move_rule(game):
    game.board.set_fen("7k/8/8/8/8/8/R7/K7 w - - 99 80")
    game.move(WHITE, "a2a3", now=1.0)
    assert (game.result, game.termination) == ("1/2-1/2", "fifty_move_rule")


def test_insufficient_material(game):
    game.board.set_fen("8/8/8/4k3/8/8/1p6/1K6 w - - 0 1")
    game.move(WHITE, "b1b2", now=1.0)  # king takes the last pawn
    assert (game.result, game.termination) == ("1/2-1/2", "insufficient_material")


# -- clock integration ---------------------------------------------------------


def test_moves_consume_time_and_earn_increment(game):
    game.move(WHITE, "e2e4", now=10.0)  # thought for 10 s, +3 s increment
    game.move(BLACK, "e7e5", now=14.0)  # thought for 4 s, +3 s increment
    state = game.to_state(now=15.0)
    assert state["clock"] == {WHITE: 292_000, BLACK: 299_000, "running": WHITE}
    assert (game.moves[0].white_ms, game.moves[1].black_ms) == (293_000, 299_000)


def test_timeout_flags_the_side_to_move(game):
    game.move(WHITE, "e2e4", now=1.0)
    assert not game.check_flag(now=300.0)
    assert game.check_flag(now=301.0)  # black had exactly 300 s
    assert (game.result, game.termination) == ("1-0", "timeout")
    assert game.to_state(999.0)["clock"][BLACK] == 0


def test_move_after_flag_fall_ends_the_game_instead(game):
    with pytest.raises(GameError, match="Time has run out"):
        game.move(WHITE, "e2e4", now=300.5)
    assert (game.status, game.result, game.termination) == (FINISHED, "0-1", "timeout")
    assert game.moves == []


def test_timeout_is_a_draw_when_the_opponent_cannot_mate(game):
    game.board.set_fen("8/8/8/4k3/8/8/1P6/1K6 b - - 0 1")  # black has a bare king
    game.clock.restore(5_000, 5_000, WHITE, now=0.0)
    game.board.turn = True
    assert game.check_flag(now=6.0)
    assert (game.result, game.termination) == ("1/2-1/2", "timeout_vs_insufficient_material")


def test_game_without_clock_never_times_out():
    live = LiveGame("g", ALICE, WHITE, TimeControl(0))
    live.start(BOB, now=0.0)
    live.move(WHITE, "e2e4", now=10_000.0)
    assert not live.check_flag(now=10**9)
    assert live.to_state(1.0)["clock"] == {WHITE: None, BLACK: None, "running": None}


# -- records -------------------------------------------------------------------


def test_only_finished_games_produce_a_record(game):
    with pytest.raises(GameError):
        game.to_record()
    play(game, "f3 e5 g4 Qh4#")
    record = game.to_record()
    assert record["result"] == "0-1" and record["termination"] == "checkmate"
    assert record["white_name"] == "alice" and record["black_id"] == 2
    assert [m["san"] for m in record["moves"]] == ["f3", "e5", "g4", "Qh4#"]
    assert record["ply_count"] == 4
