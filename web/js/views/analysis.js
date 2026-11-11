// Analysis board: replay a saved game, explore side lines and consult Stockfish.
//
// Exploration never touches the saved game: side-line moves only live in this
// view, and are validated through POST /api/position.

import { api, Socket } from "../api.js";
import { Board } from "../board.js";
import { playForMove } from "../sound.js";
import { session } from "../state.js";
import { copyText, fill, formatDate, h, moveRows, navigate, timeControlLabel, toast } from "../util.js";
import { playerLink } from "./common.js";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

/** Number of plies played before `fen`, from its move counter (for move numbering). */
function plyOffset(fen) {
  const parts = fen.split(" ");
  return (Number(parts[5] || 1) - 1) * 2 + (parts[1] === "b" ? 1 : 0);
}

export async function mount(container, { id }) {
  let game = null;
  let startFen = START_FEN;
  let mainline = []; // [{san, uci, fen}] - the saved game, never modified
  let ply = 0; // position on the main line (0 = start)
  let variation = null; // {base, moves: [{san, uci, fen}], index} - the user's side line
  let engineOn = session.config.engine_available;
  let engineSocket = null;
  const positions = new Map(); // fen -> legal moves etc. (server answers, cached)

  if (id) {
    const data = await api(`/games/${id}`);
    if (data.live) return navigate(`#/game/${id}`);
    game = data;
    startFen = data.start_fen;
    mainline = data.moves;
    ply = mainline.length;
  }

  // -- DOM ----------------------------------------------------------------------

  const boardEl = h("div");
  const evalFill = h("div.eval-fill");
  const evalBar = h("div.eval-bar", { title: "Engine evaluation" }, evalFill);
  const evalScore = h("strong.eval-score", {}, "–");
  const evalDepth = h("span.muted");
  const evalLine = h("p.eval-line");
  const mainMoves = h("div.moves");
  const sideLine = h("div.variation", { hidden: true });
  const fenInput = h("input.fen", { readOnly: Boolean(id), spellcheck: false, "aria-label": "FEN of the current position" });
  const board = new Board(boardEl, { onMove });

  const engineToggle = h("input", {
    type: "checkbox",
    checked: engineOn,
    disabled: !session.config.engine_available,
    onchange: (e) => {
      engineOn = e.target.checked;
      if (engineOn) analyse();
      else stopEngine("Engine off");
    },
  });

  const nav = (label, title, action) => h("button", { type: "button", title, onclick: action }, label);
  const controls = h(
    "div.controls",
    {},
    nav("⏮", "First move (Home)", () => go("first")),
    nav("◀", "Previous move (←)", () => go("prev")),
    nav("▶", "Next move (→)", () => go("next")),
    nav("⏭", "Last move (End)", () => go("last")),
    nav("⇅", "Flip board (F)", () => { board.flip(); evalBar.classList.toggle("flipped"); }),
  );

  const header = game
    ? h(
        "div.game-head",
        {},
        h("div", {}, h("span.color-chip.white"), playerLink(game.white), h("span.muted", {}, " vs "), h("span.color-chip.black"), playerLink(game.black)),
        h("p.meta", {}, `${game.outcome} · ${game.result.replace("1/2-1/2", "½-½")}`),
        h("p.meta", {}, `${timeControlLabel(game.time_control)} · ${formatDate(game.ended_at)}`),
      )
    : h("div.game-head", {}, h("strong", {}, "Free analysis"), h("p.meta", {}, "Move the pieces for both sides, or paste a FEN below."));

  const exports_ = h(
    "div.actions",
    {},
    h("button", { type: "button", onclick: () => copyText(currentFen()) }, "Copy FEN"),
    game && h("a.button", { href: `/api/games/${id}/pgn`, download: "" }, "PGN"),
    game && h("a.button", { href: `/api/games/${id}/json`, download: "" }, "JSON"),
    !game && h("button", { type: "button", onclick: loadFen }, "Load FEN"),
    !game && h("button", { type: "button", onclick: () => { fenInput.value = START_FEN; loadFen(); } }, "Reset"),
  );

  container.append(
    h(
      "div.game-layout.analysis",
      {},
      h("div.board-col", {}, h("div.board-with-eval", {}, evalBar, boardEl)),
      h(
        "aside.panel",
        {},
        header,
        h("div.engine", {}, h("label.switch", {}, engineToggle, h("span", {}, "Stockfish")), evalScore, evalDepth, evalLine),
        mainMoves,
        sideLine,
        controls,
        h("label.fen-label", {}, "FEN", fenInput),
        exports_,
      ),
    ),
  );

  // -- position helpers -----------------------------------------------------------

  function current() {
    if (variation && variation.index > 0) return variation.moves[variation.index - 1];
    return ply > 0 ? mainline[ply - 1] : { fen: startFen, uci: null };
  }
  const currentFen = () => current().fen;

  async function positionInfo(fen) {
    if (!positions.has(fen)) positions.set(fen, await api("/position", { method: "POST", body: { fen } }));
    return positions.get(fen);
  }

  async function show() {
    const { fen, uci } = current();
    board.set({ fen, lastMove: uci, dests: {}, movable: "both", check: null });
    fenInput.value = fen;
    renderMoves();
    setEval(null);
    try {
      const info = await positionInfo(fen);
      if (fen !== currentFen()) return; // the user moved on meanwhile
      board.set({ dests: info.dests, check: info.check });
      if (info.game_over) {
        stopEngine(info.check ? "Checkmate" : `Game over (${info.result.replace("1/2-1/2", "½-½")})`);
        setBar(info.result === "1-0" ? 100 : info.result === "0-1" ? 0 : 50);
      } else analyse();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // -- navigation -----------------------------------------------------------------

  function go(where) {
    if (where === "first") {
      variation = null;
      ply = 0;
    } else if (where === "last") {
      if (variation) variation.index = variation.moves.length;
      else ply = mainline.length;
    } else if (where === "prev") {
      if (variation && variation.index > 0) {
        variation.index -= 1; // index 0 = branch point, side line kept for "next"
      } else {
        if (variation) leaveVariation();
        ply = Math.max(0, ply - 1);
      }
    } else if (where === "next") {
      if (variation) variation.index = Math.min(variation.moves.length, variation.index + 1);
      else ply = Math.min(mainline.length, ply + 1);
    }
    show();
  }

  function leaveVariation() {
    ply = variation.base;
    variation = null;
  }

  async function onMove(uci) {
    // Following the game or the existing side line is just a step forward.
    if (mainline[ply]?.uci === uci && (!variation || variation.index === 0)) {
      variation = null;
      ply += 1;
      playForMove(mainline[ply - 1].san);
      return show();
    }
    if (variation && variation.moves[variation.index]?.uci === uci) {
      variation.index += 1;
      playForMove(current().san);
      return show();
    }
    try {
      const result = await api("/position", { method: "POST", body: { fen: currentFen(), uci } });
      positions.set(result.fen, result);
      variation ??= { base: ply, moves: [], index: 0 };
      variation.moves.length = variation.index; // drop the old continuation
      variation.moves.push({ san: result.san, uci, fen: result.fen });
      variation.index += 1;
      playForMove(result.san);
    } catch (error) {
      toast(error.message, "error");
    }
    show();
  }

  async function loadFen() {
    const fen = fenInput.value.trim();
    try {
      const info = await api("/position", { method: "POST", body: { fen } });
      positions.set(info.fen, info);
      startFen = info.fen;
      mainline = [];
      variation = null;
      ply = 0;
    } catch (error) {
      toast(error.message, "error");
    }
    show();
  }

  // -- move lists -----------------------------------------------------------------

  function renderRows(moves, startPly, isCurrent, onPick) {
    return moveRows(moves, startPly).map((row) =>
      h(
        "div.move-row",
        {},
        h("span.num", {}, `${row.number}.`),
        row.cells.map((cell) =>
          cell
            ? h(`span.san.clickable${isCurrent(cell.index) ? ".current" : ""}`, { onclick: () => onPick(cell.index) }, cell.move.san)
            : h("span.san", {}, "…"),
        ),
      ),
    );
  }

  function renderMoves() {
    const offset = plyOffset(startFen);
    mainMoves.hidden = !mainline.length;
    fill(
      mainMoves,
      renderRows(mainline, offset, (i) => (!variation || variation.index === 0) && i === ply - 1, (i) => {
        variation = null;
        ply = i + 1;
        show();
      }),
    );
    mainMoves.querySelector(".current")?.scrollIntoView({ block: "nearest" });

    sideLine.hidden = !variation;
    if (variation) {
      fill(
        sideLine,
        h("div.variation-head", {}, h("strong", {}, game ? "Your line" : "Moves"), game && h("button.small", { type: "button", onclick: () => { leaveVariation(); show(); } }, "Back to the game")),
        h("div.moves", {}, renderRows(variation.moves, offset + variation.base, (i) => i === variation.index - 1, (i) => {
          variation.index = i + 1;
          show();
        })),
      );
    }
  }

  // -- engine ---------------------------------------------------------------------

  function setBar(whitePercent) {
    evalFill.style.height = `${whitePercent}%`;
  }

  function setEval(info) {
    if (!info) {
      evalDepth.textContent = "";
      evalLine.textContent = "";
      evalScore.textContent = engineOn ? "…" : "–";
      return;
    }
    if (info.mate !== null) {
      evalScore.textContent = `#${info.mate}`;
      setBar(info.mate > 0 ? 100 : 0);
    } else {
      const pawns = info.cp / 100;
      evalScore.textContent = `${pawns > 0 ? "+" : ""}${pawns.toFixed(2)}`;
      // Logistic squash: ±4 pawns already fills most of the bar.
      setBar(50 + 50 * (2 / (1 + Math.exp(-0.004 * info.cp)) - 1));
    }
    evalDepth.textContent = `depth ${info.depth}`;
    evalLine.textContent = info.line;
  }

  function stopEngine(label) {
    engineSocket?.send({ type: "stop" }, false);
    evalScore.textContent = label;
    evalDepth.textContent = "";
    evalLine.textContent = "";
  }

  function analyse() {
    if (!engineOn) return;
    if (!engineSocket) {
      engineSocket = new Socket("/ws/analysis", {
        onMessage: (message) => {
          if (message.type === "unavailable") {
            engineSocket.close();
            engineSocket = null;
            engineOn = false;
            engineToggle.checked = false;
            stopEngine("Engine busy");
            evalLine.textContent = "All analysis engines are in use. Try again in a moment.";
          } else if (message.type === "info" && message.fen === currentFen() && engineOn) {
            setEval(message);
          }
        },
        // After a reconnect the server has a fresh engine: ask again.
        onStatus: (online) => online && engineOn && engineSocket?.send({ type: "analyse", fen: currentFen() }, false),
      });
      return; // onStatus sends the first request once connected
    }
    engineSocket.send({ type: "analyse", fen: currentFen() }, false);
  }

  if (!session.config.engine_available) stopEngine("Engine not installed");

  // -- keyboard -------------------------------------------------------------------

  const KEYS = { ArrowLeft: "prev", ArrowRight: "next", Home: "first", ArrowUp: "first", End: "last", ArrowDown: "last" };
  function onKey(event) {
    if (event.target.matches?.("input, textarea, select") || event.ctrlKey || event.metaKey) return;
    if (KEYS[event.key]) {
      event.preventDefault();
      go(KEYS[event.key]);
    } else if (event.key.toLowerCase() === "f") {
      board.flip();
      evalBar.classList.toggle("flipped");
    }
  }
  window.addEventListener("keydown", onKey);

  show();
  return () => {
    window.removeEventListener("keydown", onKey);
    engineSocket?.close();
    board.destroy();
  };
}
