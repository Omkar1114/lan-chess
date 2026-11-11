"""Export of saved games as PGN and JSON."""

from __future__ import annotations

from typing import Any

import chess
import chess.pgn

from .game import TimeControl

# PGN standard values for the Termination tag, keyed by our detailed reason.
_PGN_TERMINATION = {
    "timeout": "time forfeit",
    "timeout_vs_insufficient_material": "time forfeit",
}

# Human-readable sentence for each way a game can end.
TERMINATION_TEXT = {
    "checkmate": "checkmate",
    "stalemate": "stalemate",
    "insufficient_material": "insufficient material",
    "threefold_repetition": "threefold repetition",
    "fifty_move_rule": "the fifty-move rule",
    "timeout": "timeout",
    "timeout_vs_insufficient_material": "timeout against insufficient mating material",
    "resignation": "resignation",
    "draw_agreement": "mutual agreement",
}


def describe_outcome(record: dict[str, Any]) -> str:
    """E.g. 'White wins by checkmate' or 'Draw by stalemate'."""
    reason = TERMINATION_TEXT.get(record["termination"], record["termination"])
    if record["result"] == "1-0":
        return f"White wins by {reason}"
    if record["result"] == "0-1":
        return f"Black wins by {reason}"
    return f"Draw by {reason}"


def positions(record: dict[str, Any]) -> list[str]:
    """FEN of every position of the game: index 0 is the start, index n is after ply n."""
    board = chess.Board()
    fens = [board.fen()]
    for move in record["moves"]:
        board.push(chess.Move.from_uci(move["uci"]))
        fens.append(board.fen())
    return fens


def game_to_pgn(record: dict[str, Any], site: str = "LAN Chess") -> str:
    """Render one saved game as PGN with the Seven Tag Roster plus clock info."""
    game = chess.pgn.Game()
    tc = TimeControl(record["base_seconds"], record["increment_seconds"])
    date, _, time = record["started_at"].rstrip("Z").partition("T")

    headers = game.headers
    headers["Event"] = "Casual game vs computer" if record["vs_computer"] else "Casual game"
    headers["Site"] = site
    headers["Date"] = date.replace("-", ".")
    headers["Round"] = "-"
    headers["White"] = record["white_name"]
    headers["Black"] = record["black_name"]
    headers["Result"] = record["result"]
    headers["UTCDate"] = date.replace("-", ".")
    headers["UTCTime"] = time
    headers["TimeControl"] = tc.pgn
    headers["Termination"] = _PGN_TERMINATION.get(record["termination"], "normal")
    headers["PlyCount"] = str(len(record["moves"]))

    node: chess.pgn.GameNode = game
    for index, move in enumerate(record["moves"]):
        node = node.add_variation(chess.Move.from_uci(move["uci"]))
        # [%clk] comment: time left for the side that just moved.
        clock_ms = move["white_ms"] if index % 2 == 0 else move["black_ms"]
        if clock_ms is not None:
            node.set_clock(clock_ms / 1000)
    if record["moves"]:
        node.comment = f"{node.comment} {describe_outcome(record)}.".strip()

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    return game.accept(exporter) + "\n"


def games_to_pgn(records: list[dict[str, Any]], site: str = "LAN Chess") -> str:
    """Several games in one PGN file (games are separated by a blank line)."""
    return "\n".join(game_to_pgn(record, site) for record in records)


def game_to_json(record: dict[str, Any]) -> dict[str, Any]:
    """Self-contained JSON document describing a saved game."""
    tc = TimeControl(record["base_seconds"], record["increment_seconds"])
    fens = positions(record)
    return {
        "id": record["id"],
        "white": {"id": record["white_id"], "name": record["white_name"]},
        "black": {"id": record["black_id"], "name": record["black_name"]},
        "vs_computer": record["vs_computer"],
        "engine_level": record["engine_level"],
        "time_control": tc.to_dict(),
        "result": record["result"],
        "termination": record["termination"],
        "outcome": describe_outcome(record),
        "started_at": record["started_at"],
        "ended_at": record["ended_at"],
        "start_fen": fens[0],
        "final_fen": fens[-1],
        "moves": [
            {
                "ply": index + 1,
                "san": move["san"],
                "uci": move["uci"],
                "fen": fens[index + 1],
                "white_ms": move["white_ms"],
                "black_ms": move["black_ms"],
            }
            for index, move in enumerate(record["moves"])
        ],
    }
