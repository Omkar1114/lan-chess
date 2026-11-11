// Board and piece customisation. Changes apply instantly and are saved on the
// account (or in this browser for guests).

import { Board } from "../board.js";
import { play } from "../sound.js";
import { applyPrefs, savePrefs, session, THEMES } from "../state.js";
import { fill, h, toast } from "../util.js";

const PREVIEW_FEN = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 6 5";
const PREVIEW_DESTS = { e1: ["g1", "f1", "e2"], d2: ["d3", "d4"], f3: ["g5", "h4", "d4", "g1", "e5"], c4: ["d5", "b5", "a6", "d3", "e2", "f1", "b3", "e6", "f7"] };

const TOGGLES = [
  ["show_coordinates", "Show coordinates"],
  ["highlight_last_move", "Highlight the last move"],
  ["show_legal_moves", "Show legal move hints"],
  ["sounds", "Move sounds"],
  ["auto_queen", "Always promote to a queen"],
];

export function mount(container) {
  let prefs = { ...session.prefs };
  let saveTimer = null;
  const saved = h("span.muted");

  const boardEl = h("div");
  const board = new Board(boardEl, { onMove: () => setTimeout(resetPreview, 700) });
  function resetPreview() {
    board.set({ fen: PREVIEW_FEN, dests: PREVIEW_DESTS, movable: "white", lastMove: "f8c5", check: null });
    board.selected = "f3";
    board.render();
  }

  /** Apply locally at once; persist shortly after (colour pickers fire rapidly). */
  function update(patch) {
    prefs = { ...prefs, ...patch };
    session.prefs = { ...prefs };
    applyPrefs();
    renderChoices();
    saved.textContent = "";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        saveTimer = null;
        await savePrefs(prefs);
        saved.textContent = "Saved ✓";
      } catch (error) {
        toast(error.message, "error");
      }
    }, 300);
  }

  const themes = h("div.swatches");
  const pieceSets = h("div.swatches");
  const customRow = h("div.row.custom-colors");

  function swatch(colors, label, active, onclick) {
    return h(
      `button.swatch${active ? ".active" : ""}`,
      { type: "button", onclick, title: label },
      h("span.swatch-board", { style: `background:conic-gradient(${colors.dark} 25%, ${colors.light} 0 50%, ${colors.dark} 0 75%, ${colors.light} 0) 0 0/100% 100%` }),
      h("span", {}, label),
    );
  }

  function renderChoices() {
    fill(
      themes,
      Object.entries(THEMES).map(([key, theme]) => swatch(theme, theme.name, prefs.board_theme === key, () => update({ board_theme: key }))),
      swatch({ light: prefs.custom_light, dark: prefs.custom_dark }, "Custom", prefs.board_theme === "custom", () => update({ board_theme: "custom" })),
    );
    customRow.hidden = prefs.board_theme !== "custom";
    fill(
      pieceSets,
      session.config.piece_sets.map((set) =>
        h(
          `button.swatch.pieces${prefs.piece_set === set ? ".active" : ""}`,
          { type: "button", onclick: () => update({ piece_set: set }) },
          h("span.piece-preview", {}, ["wK", "bQ", "wN"].map((code) => h("img", { src: `/pieces/${set}/${code}.svg`, alt: "" }))),
          h("span", {}, set[0].toUpperCase() + set.slice(1)),
        ),
      ),
    );
  }

  const colorInput = (key, label) => h("label", {}, label, h("input", { type: "color", value: prefs[key], oninput: (e) => update({ [key]: e.target.value }) }));
  customRow.append(colorInput("custom_light", "Light squares"), colorInput("custom_dark", "Dark squares"));

  const toggles = TOGGLES.map(([key, label]) =>
    h(
      "label.switch",
      {},
      h("input", {
        type: "checkbox",
        checked: prefs[key],
        onchange: (e) => {
          update({ [key]: e.target.checked });
          if (key === "sounds" && e.target.checked) play("move");
        },
      }),
      h("span", {}, label),
    ),
  );

  container.append(
    h(
      "div.settings",
      {},
      h(
        "section.card",
        {},
        h("div.card-head", {}, h("h2", {}, "Board & pieces"), saved),
        !session.user && h("p.muted", {}, "You are not logged in: settings are kept in this browser only. Log in to use them on every device."),
        h("h3", {}, "Board theme"),
        themes,
        customRow,
        h("h3", {}, "Piece set"),
        pieceSets,
        h("h3", {}, "Behaviour"),
        h("div.toggles", {}, toggles),
      ),
      h("section.card.preview", {}, h("h2", {}, "Preview"), boardEl, h("p.muted", {}, "Try it: tap or drag a white piece.")),
    ),
  );
  renderChoices();
  resetPreview();

  return () => {
    if (saveTimer) savePrefs(prefs).catch(() => {}); // flush a pending save
    clearTimeout(saveTimer);
    board.destroy();
  };
}
