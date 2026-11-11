// Pieces of UI used by more than one view.

import { api } from "../api.js";
import { h, formatDate, timeControlLabel } from "../util.js";

/** Player name linking to the profile (engines have no profile). */
export function playerLink(player) {
  return player.id ? h("a", { href: `#/user/${player.name}` }, player.name) : h("span", {}, player.name);
}

/** Table of finished games; `perspective` (a user id) colours results as win/loss. */
export function gamesTable(games, perspective = null) {
  if (!games.length) return h("p.muted", {}, "No games yet.");
  const badge = (game) => {
    let kind = "draw";
    if (perspective && game.result !== "1/2-1/2") {
      const won = (game.result === "1-0") === (game.white.id === perspective);
      kind = won ? "win" : "loss";
    }
    return h(`span.result.${kind}`, {}, game.result.replace("1/2-1/2", "½-½"));
  };
  return h(
    "div.table-wrap",
    {},
    h(
      "table.games",
      {},
      h("thead", {}, h("tr", {}, ["White", "Black", "Result", "Ended by", "Time", "Moves", "Date", ""].map((t) => h("th", {}, t)))),
      h(
        "tbody",
        {},
        games.map((game) =>
          h(
            "tr",
            {},
            h("td", {}, playerLink(game.white)),
            h("td", {}, playerLink(game.black)),
            h("td", {}, badge(game)),
            h("td.muted", {}, game.outcome.replace(/^.* by /, "")),
            h("td", {}, timeControlLabel(game.time_control)),
            h("td", {}, Math.ceil(game.ply_count / 2)),
            h("td.muted", {}, formatDate(game.ended_at)),
            h("td", {}, h("a.button.small", { href: `#/analysis/${game.id}` }, "Open")),
          ),
        ),
      ),
    ),
  );
}

/** Paginated list of saved games, optionally restricted to one player. */
export function pagedGames(container, { player = null, perspective = null } = {}) {
  let page = 1;
  async function load() {
    const query = new URLSearchParams({ page, page_size: 15 });
    if (player) query.set("player", player);
    const data = await api(`/games?${query}`);
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    const step = (delta) => () => {
      page += delta;
      load();
    };
    container.replaceChildren(
      gamesTable(data.games, perspective),
      pages > 1 &&
        h(
          "div.pager",
          {},
          h("button", { type: "button", disabled: page <= 1, onclick: step(-1) }, "‹ Newer"),
          h("span.muted", {}, `Page ${page} of ${pages}`),
          h("button", { type: "button", disabled: page >= pages, onclick: step(1) }, "Older ›"),
        ) || "",
    );
  }
  load();
  return { reload: load };
}
