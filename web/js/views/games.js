// Browser for all finished games on the server.

import { h } from "../util.js";
import { pagedGames } from "./common.js";

export function mount(container) {
  const list = h("div");
  const input = h("input", { type: "search", placeholder: "Filter by player name", maxLength: 20 });
  let timer;
  const show = () => pagedGames(list, { player: input.value.trim() || null });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(show, 250);
  });
  container.append(h("section.card", {}, h("div.card-head", {}, h("h2", {}, "All games"), input), list));
  show();
}
