// Public profile: statistics, game history and bulk PGN export.

import { api } from "../api.js";
import { fill, h, formatDate } from "../util.js";
import { pagedGames } from "./common.js";

export async function mount(container, { id: username }) {
  let user;
  try {
    user = await api(`/users/${encodeURIComponent(username)}`);
  } catch (error) {
    fill(container, h("div.card.narrow", {}, h("h2", {}, "User not found"), h("p.muted", {}, error.message)));
    return;
  }
  const { stats } = user;
  const score = stats.games ? Math.round(((stats.wins + stats.draws / 2) / stats.games) * 100) : 0;
  const stat = (label, value, kind = "") => h(`div.stat.${kind || "plain"}`, {}, h("strong", {}, value), h("span", {}, label));
  const list = h("div");

  container.append(
    h(
      "section.card",
      {},
      h("div.card-head", {}, h("div", {}, h("h2", {}, user.username), h("p.muted", {}, `Member since ${formatDate(user.created_at)}`)), stats.games > 0 && h("a.button", { href: `/api/users/${encodeURIComponent(user.username)}/games.pgn`, download: "" }, "Export all games (PGN)")),
      h("div.stats", {}, stat("Games", stats.games), stat("Wins", stats.wins, "win"), stat("Losses", stats.losses, "loss"), stat("Draws", stats.draws, "draw"), stat("Score", `${score}%`)),
    ),
    h("section.card", {}, h("h2", {}, "Game history"), list),
  );
  pagedGames(list, { player: user.username, perspective: user.id });
}
