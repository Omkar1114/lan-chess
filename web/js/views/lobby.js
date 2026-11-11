// Lobby: host a game, and see open and live games update in real time.

import { api, Socket } from "../api.js";
import { session } from "../state.js";
import { fill, h, navigate, timeControlLabel, toast } from "../util.js";
import { playerLink } from "./common.js";

const FORM_KEY = "lanchess.newgame";

function savedForm() {
  try {
    return JSON.parse(localStorage.getItem(FORM_KEY)) ?? {};
  } catch {
    return {};
  }
}

/** Radio-button group rendered as a segmented control. */
function segmented(name, options, value) {
  return h(
    "div.segmented",
    {},
    options.map(([key, label, disabled]) =>
      h("label", { class: disabled ? "disabled" : "" }, h("input", { type: "radio", name, value: key, checked: key === value, disabled }), h("span", {}, label)),
    ),
  );
}

function createForm() {
  const { config } = session;
  const saved = savedForm();
  const opponent = !config.engine_available ? "human" : (saved.opponent ?? "human");

  const presetOptions = [];
  for (const category of ["bullet", "blitz", "rapid", "classical"]) {
    presetOptions.push(
      h(
        "optgroup",
        { label: category[0].toUpperCase() + category.slice(1) },
        config.presets
          .filter((p) => p.category === category)
          .map((p) => h("option", { value: `${p.base}+${p.increment}` }, `${p.base / 60} min + ${p.increment} s`)),
      ),
    );
  }
  const timeSelect = h("select", { name: "time" }, presetOptions, h("option", { value: "0+0" }, "No clock"), h("option", { value: "custom" }, "Custom…"));
  timeSelect.value = saved.time ?? "300+3";
  if (!timeSelect.value) timeSelect.value = "300+3";

  const minutes = h("input", { type: "number", name: "minutes", min: 0.25, max: 180, step: 0.25, value: saved.minutes ?? 10 });
  const increment = h("input", { type: "number", name: "increment", min: 0, max: 180, step: 1, value: saved.increment ?? 5 });
  const custom = h("div.row.custom-time", {}, h("label", {}, "Minutes per side", minutes), h("label", {}, "Increment (seconds)", increment));

  const levelSelect = h(
    "select",
    { name: "level" },
    config.engine_levels.map((l) => h("option", { value: l.level }, `${l.level} · ${l.name}`)),
  );
  levelSelect.value = saved.level ?? 3;
  const levelRow = h("label", {}, "Difficulty", levelSelect);

  const submit = h("button.primary", { type: "submit" }, "Create game");
  const form = h(
    "form.card",
    {},
    h("h2", {}, "New game"),
    h("div.field", {}, h("span.label", {}, "Opponent"), segmented("opponent", [["human", "Another player"], ["computer", "Computer", !config.engine_available]], opponent)),
    levelRow,
    h("div.field", {}, h("span.label", {}, "I play as"), segmented("color", [["white", "White"], ["random", "Random"], ["black", "Black"]], saved.color ?? "random")),
    h("label", {}, "Time control", timeSelect),
    custom,
    submit,
  );

  const sync = () => {
    const data = new FormData(form);
    levelRow.hidden = data.get("opponent") !== "computer";
    custom.hidden = timeSelect.value !== "custom";
  };
  form.addEventListener("change", sync);
  sync();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    let base, inc;
    if (timeSelect.value === "custom") {
      base = Math.round(Number(minutes.value) * 60);
      inc = Math.round(Number(increment.value));
      if (!(base >= 15)) return toast("Custom time must be at least 15 seconds", "error");
    } else {
      [base, inc] = timeSelect.value.split("+").map(Number);
    }
    localStorage.setItem(
      FORM_KEY,
      JSON.stringify({ opponent: data.get("opponent"), color: data.get("color"), time: timeSelect.value, minutes: minutes.value, increment: increment.value, level: levelSelect.value }),
    );
    submit.disabled = true;
    try {
      const game = await api("/games", {
        method: "POST",
        body: {
          color: data.get("color"),
          base_seconds: base,
          increment_seconds: inc,
          vs_computer: data.get("opponent") === "computer",
          engine_level: Number(levelSelect.value),
        },
      });
      navigate(`#/game/${game.id}`);
    } catch (error) {
      toast(error.message, "error");
      submit.disabled = false;
    }
  });
  return form;
}

export function mount(container) {
  const me = session.user;
  const openList = h("div.list");
  const liveList = h("div.list");
  const status = h("span.muted");

  const side = me
    ? createForm()
    : h(
        "div.card",
        {},
        h("h2", {}, "Welcome"),
        h("p", {}, "Log in to host or join a game. Watching live games needs no account."),
        h("div.row", {}, h("a.button.primary", { href: "#/login" }, "Log in"), session.config.allow_registration && h("a.button", { href: "#/register" }, "Sign up")),
      );

  container.append(
    h(
      "div.lobby",
      {},
      side,
      h(
        "div.stack",
        {},
        h("section.card", {}, h("h2", {}, "Open games ", status), openList),
        h("section.card", {}, h("h2", {}, "Live games"), liveList),
      ),
    ),
  );

  async function join(id) {
    try {
      await api(`/games/${id}/join`, { method: "POST" });
      navigate(`#/game/${id}`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function render({ open, live }) {
    fill(
      openList,
      open.length
        ? open.map((game) => {
            const mine = me && game.host_id === me.id;
            return h(
              "div.item",
              {},
              h("div.grow", {}, h("strong", {}, game.host), h("span.muted", {}, ` · ${timeControlLabel(game.time_control)} · you play ${game.your_color}`)),
              mine
                ? h("a.button.small", { href: `#/game/${game.id}` }, "Your game")
                : me
                  ? h("button.primary.small", { type: "button", onclick: () => join(game.id) }, "Join")
                  : h("a.button.small", { href: "#/login" }, "Log in to join"),
            );
          })
        : h("p.muted", {}, "No one is waiting for an opponent."),
    );
    fill(
      liveList,
      live.length
        ? live.map((game) => {
            const mine = me && (game.white.id === me.id || game.black.id === me.id);
            return h(
              "div.item",
              { class: mine ? "mine" : "" },
              h("div.grow", {}, playerLink(game.white), h("span.muted", {}, " vs "), playerLink(game.black), h("span.muted", {}, ` · ${timeControlLabel(game.time_control)}`)),
              h("a.button.small", { href: `#/game/${game.id}`, class: mine ? "primary" : "" }, mine ? "Resume" : "Watch"),
            );
          })
        : h("p.muted", {}, "No games in progress."),
    );
  }

  const socket = new Socket("/ws/lobby", {
    onMessage: (message) => message.type === "lobby" && render(message),
    onStatus: (online) => (status.textContent = online ? "" : "· reconnecting…"),
  });
  return () => socket.close();
}
