// Live game room for players and spectators.
//
// The server pushes the complete game state after every change; this view just
// renders the latest one and sends the user's intentions back.

import { api, Socket } from "../api.js";
import { Board } from "../board.js";
import { play, playForMove } from "../sound.js";
import { session } from "../state.js";
import { copyText, fill, formatClock, h, moveRows, navigate, timeControlLabel, toast } from "../util.js";

const REASONS = {
  checkmate: "checkmate",
  stalemate: "stalemate",
  insufficient_material: "insufficient material",
  threefold_repetition: "threefold repetition",
  fifty_move_rule: "the fifty-move rule",
  timeout: "timeout",
  timeout_vs_insufficient_material: "timeout (opponent cannot mate)",
  resignation: "resignation",
  draw_agreement: "agreement",
};

const other = (color) => (color === "white" ? "black" : "white");
const cap = (text) => text[0].toUpperCase() + text.slice(1);

function outcomeText(state) {
  const reason = REASONS[state.termination] ?? state.termination;
  return state.winner ? `${cap(state.winner)} wins by ${reason}` : `Draw by ${reason}`;
}

/** Name, presence dot and clock of one side. */
function playerStrip() {
  const dot = h("span.dot");
  const name = h("span.name");
  const clock = h("span.clock");
  return { el: h("div.player", {}, dot, name, clock), dot, name, clock };
}

export function mount(container, { id }) {
  let state = null;
  let flipped = false;
  let clockBase = null; // last clock snapshot + local receive time
  let resignArmed = false;
  let resignTimer = null;
  let followedRematch = false;

  const boardEl = h("div");
  const top = playerStrip();
  const bottom = playerStrip();
  const panel = h("aside.panel");
  const banner = h("div.banner", { hidden: true }, "Connection lost - reconnecting…");
  const layout = h("div.game-layout", {}, h("div.board-col", {}, banner, top.el, boardEl, bottom.el), panel);
  container.append(layout);

  const send = (type, extra = {}) => socket.send({ type, ...extra });
  const board = new Board(boardEl, { onMove: (uci) => send("move", { uci }) });

  // -- clocks -----------------------------------------------------------------

  function orientation() {
    const mine = state.you ?? (state.is_host && state.color_pref === "black" ? "black" : "white");
    return flipped ? other(mine) : mine;
  }

  function tickClocks() {
    if (!state || !clockBase) return;
    const elapsed = performance.now() - clockBase.at;
    const bottomColor = orientation();
    for (const [strip, color] of [[bottom, bottomColor], [top, other(bottomColor)]]) {
      let ms = clockBase[color];
      const running = clockBase.running === color;
      if (ms !== null && running) ms = Math.max(0, ms - elapsed);
      strip.clock.textContent = formatClock(ms);
      strip.clock.classList.toggle("running", running);
      strip.clock.classList.toggle("low", ms !== null && ms < 20000 && state.status === "live");
    }
  }
  const clockTimer = setInterval(tickClocks, 100);

  // -- rendering --------------------------------------------------------------

  function renderPlayers() {
    const bottomColor = orientation();
    for (const [strip, color] of [[bottom, bottomColor], [top, other(bottomColor)]]) {
      const player = state[color];
      strip.el.classList.toggle("to-move", state.status === "live" && state.turn === color);
      strip.dot.className = `dot ${player?.connected ? "online" : ""}`;
      strip.dot.title = player?.connected ? "Connected" : "Disconnected";
      fill(
        strip.name,
        h(`span.color-chip.${color}`),
        player ? (player.id ? h("a", { href: `#/user/${player.name}` }, player.name) : player.name) : h("span.muted", {}, "Waiting for opponent…"),
        state.you === color && h("span.muted", {}, " (you)"),
      );
    }
  }

  function moveList() {
    const rows = moveRows(state.moves);
    const list = h(
      "div.moves",
      {},
      rows.length
        ? rows.map((row) =>
            h(
              "div.move-row",
              {},
              h("span.num", {}, `${row.number}.`),
              row.cells.map((cell) => h(`span.san${cell && cell.index === state.moves.length - 1 ? ".current" : ""}`, {}, cell?.move.san ?? "…")),
            ),
          )
        : h("p.muted", {}, "No moves yet."),
    );
    requestAnimationFrame(() => (list.scrollTop = list.scrollHeight));
    return list;
  }

  function button(label, onclick, { kind = "", disabled = false } = {}) {
    return h(`button${kind ? `.${kind}` : ""}`, { type: "button", onclick, disabled }, label);
  }

  function offers() {
    const { you } = state;
    if (!you || state.status !== "live") return null;
    const boxes = [];
    const ask = (text, accept, decline) =>
      h("div.offer", {}, h("span", {}, text), h("div.row", {}, button("Accept", () => send(accept), { kind: "primary.small" }), button("Decline", () => send(decline), { kind: "small" })));
    if (state.draw_offer === other(you)) boxes.push(ask("Your opponent offers a draw.", "draw_accept", "draw_decline"));
    if (state.draw_offer === you) boxes.push(h("div.offer.sent", {}, "Draw offer sent - waiting for an answer."));
    if (state.takeback_request === other(you)) boxes.push(ask("Your opponent asks to take back a move.", "takeback_accept", "takeback_decline"));
    if (state.takeback_request === you) boxes.push(h("div.offer.sent", {}, "Takeback request sent - waiting for an answer."));
    return boxes;
  }

  function actions() {
    const { you, status } = state;
    const flip = button("Flip board", () => {
      flipped = !flipped;
      render();
    });
    if (status === "live" && you) {
      const myMoves = Math.floor((state.moves.length + (you === "white" ? 1 : 0)) / 2);
      return [
        button("Offer draw", () => send("draw_offer"), { disabled: Boolean(state.draw_offer) }),
        button("Takeback", () => send("takeback_request"), { disabled: Boolean(state.takeback_request) || myMoves === 0 }),
        button(resignArmed ? "Confirm resign" : "Resign", resign, { kind: resignArmed ? "danger" : "" }),
        flip,
      ];
    }
    if (status === "finished") {
      const { offered_by: offeredBy, game_id: nextId } = state.rematch;
      const items = [];
      if (nextId) items.push(h("a.button.primary", { href: `#/game/${nextId}` }, "Go to the rematch"));
      else if (you && offeredBy === other(you)) {
        items.push(button("Accept rematch", () => send("rematch_offer"), { kind: "primary" }), button("Decline", () => send("rematch_decline")));
      } else if (you) {
        items.push(button(offeredBy === you ? "Rematch offered…" : "Rematch", () => send("rematch_offer"), { kind: "primary", disabled: offeredBy === you }));
      }
      items.push(h("a.button", { href: `#/analysis/${state.id}` }, "Analysis board"), flip);
      return items;
    }
    return [flip];
  }

  function resign() {
    if (resignArmed) {
      clearTimeout(resignTimer);
      resignArmed = false;
      send("resign");
      return;
    }
    resignArmed = true;
    renderPanel();
    resignTimer = setTimeout(() => {
      resignArmed = false;
      renderPanel();
    }, 3000);
  }

  function statusLine() {
    if (state.status === "open") return "Waiting for an opponent";
    if (state.status === "aborted") return "This game was cancelled";
    if (state.status === "finished") return outcomeText(state);
    if (state.you) return state.turn === state.you ? "Your move" : "Opponent's move";
    return `${cap(state.turn)} to move`;
  }

  function waitingRoom() {
    const url = location.href;
    const me = session.user;
    return h(
      "div.waiting",
      {},
      h("p", {}, state.is_host ? "Your game is listed in the lobby. You can also share this link:" : `${state.host} is waiting for an opponent.`),
      state.is_host && h("div.row", {}, h("input", { readOnly: true, value: url, onfocus: (e) => e.target.select() }), button("Copy", () => copyText(url), { kind: "small" })),
      state.is_host
        ? button("Cancel game", () => send("abort"), { kind: "danger" })
        : me
          ? button("Join this game", async () => {
              try {
                await api(`/games/${id}/join`, { method: "POST" });
              } catch (error) {
                toast(error.message, "error");
              }
            }, { kind: "primary" })
          : h("a.button.primary", { href: "#/login" }, "Log in to join"),
    );
  }

  function renderPanel() {
    const finished = state.status === "finished";
    fill(
      panel,
      h(`div.status${finished ? ".finished" : ""}${state.status === "live" && state.you && state.turn === state.you ? ".your-turn" : ""}`, {}, h("strong", {}, statusLine()), finished && h("span.result-score", {}, state.result.replace("1/2-1/2", "½-½"))),
      h("p.meta", {}, timeControlLabel(state.time_control), state.vs_computer && " · vs computer", state.spectators > 0 && ` · ${state.spectators} watching`),
      state.status === "open" && waitingRoom(),
      state.status === "aborted" && h("a.button.primary", { href: "#/" }, "Back to the lobby"),
      offers(),
      state.status !== "open" && state.status !== "aborted" && moveList(),
      h("div.actions", {}, actions()),
    );
  }

  function render() {
    const canMove = state.status === "live" && state.you && state.turn === state.you;
    board.set({
      fen: state.fen,
      orientation: orientation(),
      dests: canMove ? state.dests : {},
      movable: canMove ? state.you : null,
      lastMove: state.last_move,
      check: state.check,
    });
    renderPlayers();
    renderPanel();
    tickClocks();
  }

  // -- incoming state ---------------------------------------------------------

  function playSounds(previous) {
    if (!previous) return;
    const you = state.you;
    if (state.moves.length > previous.moves.length) playForMove(state.moves.at(-1).san);
    if (previous.status !== state.status) {
      if (state.status === "finished") play("end");
      else if (state.status === "live") play("notify");
    }
    if (you) {
      const incoming = (key) => state[key] === other(you) && previous[key] !== state[key];
      if (incoming("draw_offer") || incoming("takeback_request")) play("notify");
      if (state.rematch.offered_by === other(you) && !previous.rematch.offered_by) play("notify");
    }
  }

  async function onGone() {
    socket.close();
    try {
      const saved = await api(`/games/${id}`);
      if (!saved.live) return navigate(`#/analysis/${id}`);
    } catch {
      /* fall through */
    }
    fill(container, h("div.card.narrow", {}, h("h2", {}, "Game not found"), h("p.muted", {}, "It may have been cancelled."), h("a.button.primary", { href: "#/" }, "Back to the lobby")));
  }

  function onMessage(message) {
    if (message.type === "error") return toast(message.message, "error");
    if (message.type === "gone") return onGone();
    if (message.type !== "state") return;
    const previous = state;
    state = message;
    clockBase = { ...state.clock, at: performance.now() };
    playSounds(previous);
    render();
    // Both players follow the rematch automatically; spectators get a link.
    if (state.rematch.game_id && state.you && !followedRematch) {
      followedRematch = true;
      navigate(`#/game/${state.rematch.game_id}`);
    }
  }

  const socket = new Socket(`/ws/game/${id}`, {
    onMessage,
    onStatus: (online) => (banner.hidden = online),
  });

  return () => {
    socket.close();
    clearInterval(clockTimer);
    clearTimeout(resignTimer);
    board.destroy();
  };
}
