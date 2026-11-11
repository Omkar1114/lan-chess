"""End-to-end tests through HTTP and WebSockets against a real app instance."""

from __future__ import annotations

import pytest

from app.auth import COOKIE_NAME


def signup(client, username: str, password: str = "correct-horse") -> dict[str, str]:
    """Register a user and return the headers that authenticate as them."""
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    headers = {"Cookie": f"{COOKIE_NAME}={response.cookies[COOKIE_NAME]}"}
    client.cookies.clear()  # every request states explicitly who is calling
    return headers


def state_after(ws, predicate, limit: int = 50) -> dict:
    """Read messages until a game state satisfies ``predicate``."""
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == "state" and predicate(message):
            return message
    raise AssertionError("expected state never arrived")


def start_game(client, alice, bob, **options) -> str:
    body = {"color": "white", "base_seconds": 300, "increment_seconds": 0, **options}
    game_id = client.post("/api/games", json=body, headers=alice).json()["id"]
    assert client.post(f"/api/games/{game_id}/join", headers=bob).status_code == 200
    return game_id


# -- accounts ------------------------------------------------------------------


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    config = client.get("/api/config").json()
    assert {"cburnett", "chessnut"} <= set(config["piece_sets"])
    assert any(p["category"] == "bullet" for p in config["presets"])


def test_register_login_logout(client):
    alice = signup(client, "alice")
    assert client.get("/api/me", headers=alice).json()["user"]["username"] == "alice"
    assert client.get("/api/me").json()["user"] is None

    # Duplicate names (case-insensitive), weak passwords and bad names are refused.
    for body, status in [
        ({"username": "ALICE", "password": "another-pass"}, 409),
        ({"username": "carol", "password": "short"}, 422),
        ({"username": "no spaces", "password": "long-enough"}, 422),
    ]:
        assert client.post("/api/auth/register", json=body).status_code == status

    bad = client.post("/api/auth/login", json={"username": "alice", "password": "wrong-pass"})
    assert bad.status_code == 401
    good = client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse"})
    assert good.status_code == 200
    session = {"Cookie": f"{COOKIE_NAME}={good.cookies[COOKIE_NAME]}"}
    client.cookies.clear()

    assert client.post("/api/auth/logout", headers=session).status_code == 204
    client.cookies.clear()
    assert client.get("/api/me", headers=session).json()["user"] is None  # session revoked
    assert client.get("/api/me", headers=alice).json()["user"] is not None  # other device kept


def test_passwords_are_stored_hashed(client):
    signup(client, "alice", "correct-horse")
    row = client.app.state.db.user_by_name("alice")
    assert "correct-horse" not in row["password_hash"]
    assert row["password_hash"].startswith("scrypt$")


def test_admin_account_comes_from_the_environment(client):
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-password"}
    )
    assert response.json()["is_admin"] is True


def test_preferences_are_saved_per_account(client):
    alice = signup(client, "alice")
    prefs = client.get("/api/me", headers=alice).json()["user"]["prefs"]
    assert prefs["piece_set"] == "cburnett" and prefs["auto_queen"] is False

    prefs.update(piece_set="chessnut", board_theme="custom", custom_dark="#112233", sounds=False)
    assert client.put("/api/me/prefs", json=prefs, headers=alice).status_code == 200
    stored = client.get("/api/me", headers=alice).json()["user"]["prefs"]
    assert stored["piece_set"] == "chessnut" and stored["custom_dark"] == "#112233"

    prefs["custom_dark"] = "not-a-colour"
    assert client.put("/api/me/prefs", json=prefs, headers=alice).status_code == 422
    assert client.put("/api/me/prefs", json=prefs).status_code == 401


# -- lobby and joining ---------------------------------------------------------


def test_hosting_requires_login_and_valid_options(client):
    assert client.post("/api/games", json={}).status_code == 401
    alice = signup(client, "alice")
    too_long = {"base_seconds": 10**6}
    assert client.post("/api/games", json=too_long, headers=alice).status_code == 400
    assert client.post("/api/games", json={"color": "green"}, headers=alice).status_code == 422


def test_lobby_updates_in_real_time(client):
    alice, bob = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/lobby") as lobby:
        assert lobby.receive_json() == {"type": "lobby", "open": [], "live": []}

        body = {"color": "white", "base_seconds": 180, "increment_seconds": 2}
        game_id = client.post("/api/games", json=body, headers=alice).json()["id"]
        snapshot = lobby.receive_json()
        assert [g["id"] for g in snapshot["open"]] == [game_id]
        assert snapshot["open"][0]["your_color"] == "black"
        assert snapshot["open"][0]["time_control"]["category"] == "blitz"

        # One open game per host, and nobody can join their own game.
        assert client.post("/api/games", json=body, headers=alice).status_code == 400
        assert client.post(f"/api/games/{game_id}/join", headers=alice).status_code == 400

        assert client.post(f"/api/games/{game_id}/join", headers=bob).status_code == 200
        snapshot = lobby.receive_json()
        assert snapshot["open"] == []
        assert snapshot["live"][0]["white"]["name"] == "alice"
        assert snapshot["live"][0]["black"]["name"] == "bob"

    assert client.post(f"/api/games/{game_id}/join", headers=bob).status_code == 400


def test_host_can_cancel_an_open_game(client):
    alice = signup(client, "alice")
    game_id = client.post("/api/games", json={}, headers=alice).json()["id"]
    with client.websocket_connect(f"/ws/game/{game_id}", headers=alice) as ws:
        assert ws.receive_json()["status"] == "open"
        ws.send_json({"type": "abort"})
        assert state_after(ws, lambda s: True)["status"] == "aborted"
    assert game_id not in client.app.state.manager.games


# -- playing -------------------------------------------------------------------


def test_full_game_over_websockets_is_validated_and_saved(client):
    alice, bob = signup(client, "alice"), signup(client, "bob")
    game_id = start_game(client, alice, bob)
    assert client.get(f"/api/games/{game_id}").json() == {
        "live": True,
        "id": game_id,
        "status": "live",
    }

    with (
        client.websocket_connect(f"/ws/game/{game_id}", headers=alice) as white,
        client.websocket_connect(f"/ws/game/{game_id}", headers=bob) as black,
        client.websocket_connect(f"/ws/game/{game_id}") as watcher,
    ):
        assert state_after(white, lambda s: s["black"]["connected"])["you"] == "white"
        assert state_after(black, lambda s: True)["you"] == "black"
        seen = state_after(watcher, lambda s: True)
        assert seen["you"] is None and seen["spectators"] == 1

        # The server rejects illegal moves, moves out of turn and moves by spectators.
        white.send_json({"type": "move", "uci": "e2e5"})
        black.send_json({"type": "move", "uci": "e7e5"})
        watcher.send_json({"type": "move", "uci": "e2e4"})
        for ws in (white, black, watcher):
            while (message := ws.receive_json())["type"] != "error":
                pass
            assert message["message"]

        # Fool's mate.
        for ws, uci in [(white, "f2f3"), (black, "e7e5"), (white, "g2g4"), (black, "d8h4")]:
            ws.send_json({"type": "move", "uci": uci})
            state_after(ws, lambda s, uci=uci: s["last_move"] == uci)

        final = state_after(watcher, lambda s: s["status"] == "finished")
        assert (final["result"], final["termination"], final["winner"]) == (
            "0-1",
            "checkmate",
            "black",
        )
        assert [m["san"] for m in final["moves"]] == ["f3", "e5", "g4", "Qh4#"]
        assert final["dests"] == {}

    # Saved automatically, visible publicly and exportable.
    saved = client.get(f"/api/games/{game_id}").json()
    assert saved["live"] is False and saved["outcome"] == "Black wins by checkmate"
    listing = client.get("/api/games").json()
    assert listing["total"] == 1 and listing["games"][0]["id"] == game_id
    assert client.get("/api/games", params={"player": "bob"}).json()["total"] == 1
    assert client.get("/api/games", params={"player": "nobody"}).json()["total"] == 0

    pgn = client.get(f"/api/games/{game_id}/pgn")
    assert "attachment" in pgn.headers["content-disposition"]
    assert '[White "alice"]' in pgn.text and "Qh4#" in pgn.text and pgn.text.strip()[-3:] == "0-1"
    assert client.get(f"/api/games/{game_id}/json").json()["moves"][3]["san"] == "Qh4#"
    fen = client.get(f"/api/games/{game_id}/fen", params={"ply": 1}).json()["fen"]
    assert fen.startswith("rnbqkbnr/pppppppp/8/8/8/5P2/PPPPP1PP/RNBQKBNR b")
    assert client.get(f"/api/games/{game_id}/fen", params={"ply": 99}).status_code == 400

    assert client.get("/api/users/alice").json()["stats"] == {
        "wins": 0,
        "losses": 1,
        "draws": 0,
        "games": 1,
    }
    assert client.get("/api/users/bob").json()["stats"]["wins"] == 1
    bulk = client.get("/api/users/bob/games.pgn").text
    assert bulk.count("[Event ") == 1
    assert client.get("/api/users/nobody").status_code == 404


def test_reconnect_restores_the_game_state(client):
    alice, bob = signup(client, "alice"), signup(client, "bob")
    game_id = start_game(client, alice, bob)
    with client.websocket_connect(f"/ws/game/{game_id}", headers=alice) as white:
        white.send_json({"type": "move", "uci": "e2e4"})
        state_after(white, lambda s: s["last_move"] == "e2e4")
    # Connection dropped; a new one sees the same position and seat.
    with client.websocket_connect(f"/ws/game/{game_id}", headers=alice) as white:
        state = white.receive_json()
        assert state["you"] == "white" and state["turn"] == "black"
        assert [m["san"] for m in state["moves"]] == ["e4"]
        assert state["clock"]["running"] == "black"
        assert state["black"]["connected"] is False


def test_draw_takeback_and_rematch_flow(client):
    alice, bob = signup(client, "alice"), signup(client, "bob")
    game_id = start_game(client, alice, bob)
    with (
        client.websocket_connect(f"/ws/game/{game_id}", headers=alice) as white,
        client.websocket_connect(f"/ws/game/{game_id}", headers=bob) as black,
    ):
        white.send_json({"type": "move", "uci": "e2e4"})
        state_after(black, lambda s: s["last_move"] == "e2e4")

        white.send_json({"type": "takeback_request"})
        assert state_after(black, lambda s: s["takeback_request"])["takeback_request"] == "white"
        black.send_json({"type": "takeback_accept"})
        assert state_after(white, lambda s: not s["moves"])["turn"] == "white"

        black.send_json({"type": "draw_offer"})
        state_after(white, lambda s: s["draw_offer"] == "black")
        white.send_json({"type": "draw_decline"})
        state_after(black, lambda s: s["draw_offer"] is None)
        white.send_json({"type": "draw_offer"})
        state_after(black, lambda s: s["draw_offer"] == "white")
        black.send_json({"type": "draw_accept"})
        final = state_after(white, lambda s: s["status"] == "finished")
        assert (final["result"], final["termination"]) == ("1/2-1/2", "draw_agreement")

        white.send_json({"type": "rematch_offer"})
        state_after(black, lambda s: s["rematch"]["offered_by"] == "white")
        black.send_json({"type": "rematch_offer"})
        rematch_id = state_after(white, lambda s: s["rematch"]["game_id"])["rematch"]["game_id"]

    with client.websocket_connect(f"/ws/game/{rematch_id}", headers=alice) as ws:
        state = ws.receive_json()
        assert state["you"] == "black"  # colours are swapped
        assert state["white"]["name"] == "bob" and state["status"] == "live"

    assert client.get("/api/users/alice").json()["stats"]["draws"] == 1


def test_timeout_is_detected_by_the_server_ticker(client):
    alice, bob = signup(client, "alice"), signup(client, "bob")
    game_id = start_game(client, alice, bob, base_seconds=1)
    with client.websocket_connect(f"/ws/game/{game_id}", headers=bob) as black:
        final = state_after(black, lambda s: s["status"] == "finished")
    assert (final["result"], final["termination"]) == ("0-1", "timeout")
    assert final["clock"]["white"] == 0


def test_unknown_game_socket_reports_gone(client):
    with client.websocket_connect("/ws/game/does-not-exist") as ws:
        assert ws.receive_json() == {"type": "gone"}


def test_admin_can_delete_games(client):
    alice, bob = signup(client, "alice"), signup(client, "bob")
    game_id = start_game(client, alice, bob)
    assert client.delete(f"/api/games/{game_id}", headers=alice).status_code == 403
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    admin = {"Cookie": f"{COOKIE_NAME}={login.cookies[COOKIE_NAME]}"}
    client.cookies.clear()
    assert client.delete(f"/api/games/{game_id}", headers=admin).status_code == 204
    assert client.delete(f"/api/games/{game_id}", headers=admin).status_code == 404


# -- analysis helper -----------------------------------------------------------


def test_position_endpoint_validates_exploration_moves(client):
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    info = client.post("/api/position", json={"fen": start}).json()
    assert info["turn"] == "white" and len(info["dests"]) == 10 and info["san"] is None

    moved = client.post("/api/position", json={"fen": start, "uci": "g1f3"}).json()
    assert moved["san"] == "Nf3" and moved["turn"] == "black"

    assert client.post("/api/position", json={"fen": start, "uci": "g1g3"}).status_code == 400
    assert client.post("/api/position", json={"fen": "definitely not a fen"}).status_code == 400

    mate = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
    info = client.post("/api/position", json={"fen": mate}).json()
    assert info["game_over"] and info["result"] == "0-1" and info["check"] == "e1"


# -- engine (needs a Stockfish binary on the machine) --------------------------


@pytest.fixture
def engine_client(client):
    if not client.app.state.engine.available:
        pytest.skip("Stockfish is not installed")
    return client


def test_play_against_the_computer(engine_client):
    client = engine_client
    alice = signup(client, "alice")
    body = {"color": "white", "base_seconds": 0, "vs_computer": True, "engine_level": 1}
    game_id = client.post("/api/games", json=body, headers=alice).json()["id"]
    with client.websocket_connect(f"/ws/game/{game_id}", headers=alice) as ws:
        state = ws.receive_json()
        assert state["status"] == "live" and state["black"]["is_engine"]
        ws.send_json({"type": "move", "uci": "e2e4"})
        state = state_after(ws, lambda s: len(s["moves"]) == 2)
        assert state["turn"] == "white"

        # The engine grants takebacks (back to our move) and refuses draws.
        ws.send_json({"type": "takeback_request"})
        state_after(ws, lambda s: len(s["moves"]) == 0)
        ws.send_json({"type": "draw_offer"})
        while (message := ws.receive_json())["type"] != "error":
            pass
        assert "declines" in message["message"]

        ws.send_json({"type": "resign"})
        state_after(ws, lambda s: s["status"] == "finished")
        ws.send_json({"type": "rematch_offer"})
        rematch = state_after(ws, lambda s: s["rematch"]["game_id"])["rematch"]["game_id"]

    # In the rematch the engine has White and moves first, unprompted.
    with client.websocket_connect(f"/ws/game/{rematch}", headers=alice) as ws:
        state = state_after(ws, lambda s: len(s["moves"]) == 1)
        assert state["you"] == "black" and state["white"]["is_engine"]


def test_analysis_socket_streams_evaluations(engine_client):
    fen = "r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 4"  # mated
    before = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    with engine_client.websocket_connect("/ws/analysis") as ws:
        ws.send_json({"type": "analyse", "fen": fen})  # game over: silently ignored
        ws.send_json({"type": "analyse", "fen": before})
        info = ws.receive_json()
        while info.get("mate") is None:
            info = ws.receive_json()
        assert info["type"] == "info" and info["fen"] == before
        assert info["mate"] == 1 and info["best"] == "h5f7" and "Qxf7#" in info["line"]
