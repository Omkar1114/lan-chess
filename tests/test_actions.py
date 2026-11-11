"""Resignation, draw offers, takebacks and rematch offers."""

import pytest

from app.game import FINISHED, LIVE, GameError

from .conftest import BLACK, WHITE, play

# -- resign --------------------------------------------------------------------


def test_resignation(game):
    game.move(WHITE, "e2e4", now=1.0)
    game.resign(WHITE, now=2.0)
    assert (game.status, game.result, game.termination) == (FINISHED, "0-1", "resignation")
    with pytest.raises(GameError):
        game.resign(BLACK, now=3.0)


# -- draw offers ---------------------------------------------------------------


def test_draw_offer_accepted(game):
    game.offer_draw(WHITE)
    assert game.to_state(0.0)["draw_offer"] == WHITE
    game.respond_draw(BLACK, accept=True, now=5.0)
    assert (game.result, game.termination) == ("1/2-1/2", "draw_agreement")


def test_draw_offer_declined_keeps_the_game_going(game):
    game.offer_draw(WHITE)
    game.respond_draw(BLACK, accept=False, now=5.0)
    assert game.status == LIVE and game.draw_offer is None
    game.offer_draw(WHITE)  # may offer again later


def test_draw_offer_rules(game):
    with pytest.raises(GameError):
        game.respond_draw(BLACK, accept=True, now=1.0)  # nothing to answer
    game.offer_draw(WHITE)
    with pytest.raises(GameError):
        game.offer_draw(WHITE)  # no duplicate offers
    with pytest.raises(GameError):
        game.respond_draw(WHITE, accept=True, now=1.0)  # cannot accept your own offer
    with pytest.raises(GameError):
        game.offer_draw(BLACK)  # must answer the pending offer instead


def test_moving_declines_the_opponents_offer_but_keeps_your_own(game):
    game.offer_draw(WHITE)
    game.move(WHITE, "e2e4", now=1.0)
    assert game.draw_offer == WHITE  # still pending for black
    game.move(BLACK, "e7e5", now=2.0)
    assert game.draw_offer is None  # black moved on: implicitly declined


# -- takebacks -----------------------------------------------------------------


def test_takeback_needs_a_move_and_an_answer_from_the_opponent(game):
    with pytest.raises(GameError):
        game.request_takeback(WHITE)  # nothing to take back yet
    game.move(WHITE, "e2e4", now=1.0)
    with pytest.raises(GameError):
        game.request_takeback(BLACK)  # black has not moved
    game.request_takeback(WHITE)
    with pytest.raises(GameError):
        game.request_takeback(WHITE)  # already pending
    with pytest.raises(GameError):
        game.respond_takeback(WHITE, accept=True, now=2.0)  # cannot accept your own


def test_takeback_before_the_reply_undoes_one_ply(game):
    game.move(WHITE, "e2e4", now=10.0)
    game.request_takeback(WHITE)
    game.respond_takeback(BLACK, accept=True, now=12.0)
    assert game.moves == [] and game.turn == WHITE
    assert game.board.fen().startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w")
    # Clocks return to their values before the undone move; white's runs again.
    assert game.to_state(12.0)["clock"] == {WHITE: 300_000, BLACK: 300_000, "running": WHITE}


def test_takeback_after_the_reply_undoes_two_plies(game):
    play(game, "e4 e5 Nf3", start=10.0, step=10.0)  # moves at t=10, 20, 30
    game.move(BLACK, "b8c6", now=40.0)
    game.request_takeback(WHITE)  # white wants Nf3 back although black replied
    game.respond_takeback(BLACK, accept=True, now=45.0)
    assert [m.san for m in game.moves] == ["e4", "e5"]
    assert game.turn == WHITE
    clock = game.to_state(45.0)["clock"]
    assert clock == {WHITE: 293_000, BLACK: 293_000, "running": WHITE}


def test_declined_takeback_changes_nothing(game):
    play(game, "e4 e5")
    game.request_takeback(BLACK)
    game.respond_takeback(WHITE, accept=False, now=5.0)
    assert len(game.moves) == 2 and game.takeback_request is None


def test_a_move_cancels_a_pending_takeback_request(game):
    play(game, "e4 e5")
    game.request_takeback(BLACK)
    game.move(WHITE, "g1f3", now=5.0)
    assert game.takeback_request is None
    with pytest.raises(GameError):
        game.respond_takeback(WHITE, accept=True, now=6.0)


def test_takeback_restores_repetition_history(game):
    play(game, "Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1")
    game.request_takeback(WHITE)
    game.respond_takeback(BLACK, accept=True, now=50.0)
    play(game, "Ng1 Ng8", start=60.0)
    assert game.termination == "threefold_repetition"


# -- rematch -------------------------------------------------------------------


def test_rematch_needs_both_players(game):
    with pytest.raises(GameError):
        game.offer_rematch(WHITE)  # game still running
    game.resign(BLACK, now=1.0)
    assert game.offer_rematch(WHITE) is False
    assert game.to_state(2.0)["rematch"]["offered_by"] == WHITE
    assert game.offer_rematch(BLACK) is True


def test_rematch_can_be_declined(game):
    game.resign(BLACK, now=1.0)
    game.offer_rematch(WHITE)
    game.decline_rematch(BLACK)
    assert game.rematch_offer is None
