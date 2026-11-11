// Chessboard widget: renders a position and turns taps / drags into move requests.
//
// It knows nothing about chess rules. The server supplies the legal moves
// (`dests`: {from: [to, ...]}) and re-validates whatever move is requested.

import { session } from "./state.js";
import { h } from "./util.js";

const FILES = "abcdefgh";
const DRAG_THRESHOLD_PX = 5;

/** Piece placement and side to move of a FEN string. */
export function parseFen(fen) {
  const [placement, turn] = fen.split(" ");
  const pieces = new Map();
  placement.split("/").forEach((row, r) => {
    let file = 0;
    for (const char of row) {
      if (/\d/.test(char)) {
        file += Number(char);
      } else {
        const color = char === char.toUpperCase() ? "w" : "b";
        pieces.set(`${FILES[file]}${8 - r}`, color + char.toUpperCase());
        file += 1;
      }
    }
  });
  return { pieces, turn: turn === "b" ? "black" : "white" };
}

export class Board {
  /**
   * @param {HTMLElement} root  container element (gets the `board` class)
   * @param {{onMove?: (uci: string) => void}} options
   */
  constructor(root, { onMove } = {}) {
    this.root = root;
    this.onMove = onMove ?? (() => {});
    this.orientation = "white";
    this.pieces = new Map();
    this.turn = "white";
    this.dests = {};
    this.movable = null; // "white" | "black" | "both" | null
    this.lastMove = null; // uci
    this.check = null; // square of the king in check
    this.selected = null;
    this.drag = null;
    this.promotion = null;

    root.classList.add("board");
    root.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    root.addEventListener("pointermove", (e) => this.onPointerMove(e));
    root.addEventListener("pointerup", (e) => this.onPointerUp(e));
    root.addEventListener("pointercancel", () => this.cancelDrag());
    root.addEventListener("contextmenu", (e) => e.preventDefault());
    this.onPrefs = () => this.render();
    window.addEventListener("prefs-changed", this.onPrefs);
    this.render();
  }

  destroy() {
    window.removeEventListener("prefs-changed", this.onPrefs);
    this.cancelDrag();
  }

  /** Update any of: fen, orientation, dests, movable, lastMove, check. */
  set(state) {
    if (state.fen !== undefined) Object.assign(this, parseFen(state.fen));
    for (const key of ["orientation", "dests", "movable", "lastMove", "check"]) {
      if (state[key] !== undefined) this[key] = state[key];
    }
    this.cancelDrag();
    this.promotion = null;
    if (this.selected && !this.canSelect(this.selected)) this.selected = null;
    this.render();
  }

  flip() {
    this.set({ orientation: this.orientation === "white" ? "black" : "white" });
  }

  // -- geometry ---------------------------------------------------------------

  squareAt(x, y) {
    const rect = this.root.getBoundingClientRect();
    const col = Math.floor(((x - rect.left) / rect.width) * 8);
    const row = Math.floor(((y - rect.top) / rect.height) * 8);
    if (col < 0 || col > 7 || row < 0 || row > 7) return null;
    return this.squareName(row, col);
  }

  squareName(row, col) {
    const white = this.orientation === "white";
    return `${FILES[white ? col : 7 - col]}${white ? 8 - row : row + 1}`;
  }

  // -- rendering --------------------------------------------------------------

  pieceUrl(code) {
    return `/pieces/${session.prefs.piece_set}/${code}.svg`;
  }

  render() {
    const prefs = session.prefs;
    const targets = this.selected && prefs.show_legal_moves ? (this.dests[this.selected] ?? []) : [];
    const last = prefs.highlight_last_move && this.lastMove ? this.lastMove : "";
    const squares = [];
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 8; col++) {
        const name = this.squareName(row, col);
        const fileIndex = FILES.indexOf(name[0]);
        const dark = (fileIndex + Number(name[1])) % 2 === 1;
        const piece = this.pieces.get(name);
        const classes = [dark ? "dark" : "light"];
        if (last.slice(0, 2) === name || last.slice(2, 4) === name) classes.push("last");
        if (this.selected === name) classes.push("selected");
        if (this.check === name) classes.push("check");
        squares.push(
          h(
            `div.sq.${classes.join(".")}`,
            { dataset: { sq: name } },
            prefs.show_coordinates && col === 0 && h("span.coord.rank", {}, name[1]),
            prefs.show_coordinates && row === 7 && h("span.coord.file", {}, name[0]),
            piece && h("div.piece", { style: `background-image:url(${this.pieceUrl(piece)})` }),
            targets.includes(name) && h(`div.hint${piece ? ".capture" : ""}`),
          ),
        );
      }
    }
    this.root.replaceChildren(...squares);
    if (this.promotion) this.root.append(this.promotionDialog());
  }

  // -- interaction ------------------------------------------------------------

  canSelect(square) {
    const piece = this.pieces.get(square);
    if (!piece || !this.movable || !this.dests[square]?.length) return false;
    const color = piece[0] === "w" ? "white" : "black";
    return color === this.turn && (this.movable === "both" || this.movable === color);
  }

  onPointerDown(event) {
    if (event.button > 0 || this.promotion) return;
    const square = this.squareAt(event.clientX, event.clientY);
    if (!square) return;

    // Second tap of tap-to-move: a legal target of the selected piece.
    if (this.selected && this.selected !== square && this.dests[this.selected]?.includes(square)) {
      event.preventDefault();
      this.requestMove(this.selected, square);
      return;
    }
    if (this.canSelect(square)) {
      event.preventDefault();
      this.drag = {
        from: square,
        x: event.clientX,
        y: event.clientY,
        active: false,
        wasSelected: this.selected === square,
        ghost: null,
      };
      this.selected = square;
      this.root.setPointerCapture?.(event.pointerId);
      this.render();
    } else if (this.selected) {
      this.selected = null;
      this.render();
    }
  }

  onPointerMove(event) {
    const drag = this.drag;
    if (!drag) return;
    if (!drag.active) {
      const moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y);
      if (moved < DRAG_THRESHOLD_PX) return;
      drag.active = true;
      const size = this.root.getBoundingClientRect().width / 8;
      drag.ghost = h("div.drag-piece", {
        style: `width:${size}px;height:${size}px;background-image:url(${this.pieceUrl(
          this.pieces.get(drag.from),
        )})`,
      });
      document.body.append(drag.ghost);
      this.root.querySelector(`[data-sq="${drag.from}"] .piece`)?.classList.add("dragging");
    }
    drag.ghost.style.transform = `translate(${event.clientX}px, ${event.clientY}px) translate(-50%, -50%)`;
  }

  onPointerUp(event) {
    const drag = this.drag;
    if (!drag) return;
    this.cancelDrag();
    if (drag.active) {
      const square = this.squareAt(event.clientX, event.clientY);
      if (square && square !== drag.from && this.dests[drag.from]?.includes(square)) {
        this.requestMove(drag.from, square);
        return;
      }
    } else if (drag.wasSelected) {
      this.selected = null; // tapping the selected piece again deselects it
    }
    this.render();
  }

  cancelDrag() {
    this.drag?.ghost?.remove();
    this.drag = null;
  }

  requestMove(from, to) {
    const piece = this.pieces.get(from);
    const promotes = piece?.[1] === "P" && (to[1] === "8" || to[1] === "1");
    if (promotes && !session.prefs.auto_queen) {
      this.promotion = { from, to, color: piece[0], openedAt: performance.now() };
      this.selected = null;
      this.render();
      return;
    }
    this.commitMove(from, to, promotes ? "q" : "");
  }

  /** Show the move immediately; the server's next state confirms or corrects it. */
  commitMove(from, to, promotion) {
    const piece = this.pieces.get(from);
    this.pieces.delete(from);
    this.pieces.set(to, promotion ? piece[0] + promotion.toUpperCase() : piece);
    this.lastMove = from + to;
    this.selected = null;
    this.promotion = null;
    this.dests = {};
    this.check = null;
    this.render();
    this.onMove(from + to + promotion);
  }

  promotionDialog() {
    const { from, to, color, openedAt } = this.promotion;
    // The tap that opened the dialog is followed by a synthetic click on whatever
    // is now under the finger - i.e. this dialog. Ignore that one.
    const stray = () => performance.now() - openedAt < 350;
    const cancel = () => {
      if (stray()) return;
      this.promotion = null;
      this.render();
    };
    return h(
      "div.promotion",
      { onpointerdown: (e) => e.stopPropagation(), onclick: cancel },
      h(
        "div.promotion-choices",
        { role: "dialog", "aria-label": "Promote pawn to" },
        ["q", "r", "b", "n"].map((kind) =>
          h("button", {
            type: "button",
            title: { q: "Queen", r: "Rook", b: "Bishop", n: "Knight" }[kind],
            style: `background-image:url(${this.pieceUrl(color + kind.toUpperCase())})`,
            onclick: (e) => {
              e.stopPropagation();
              if (!stray()) this.commitMove(from, to, kind);
            },
          }),
        ),
      ),
    );
  }
}
