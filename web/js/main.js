// Application entry point: session bootstrap, header and hash-based routing.

import { api } from "./api.js";
import { loadSession, session, setUser } from "./state.js";
import { fill, h, navigate, toast } from "./util.js";

// route pattern -> [nav item to highlight, lazy view module]
const ROUTES = [
  [/^\/?$/, "lobby", () => import("./views/lobby.js")],
  [/^\/login$/, "", () => import("./views/auth.js")],
  [/^\/register$/, "", () => import("./views/auth.js")],
  [/^\/game\/([\w-]+)$/, "lobby", () => import("./views/game.js")],
  [/^\/games$/, "games", () => import("./views/games.js")],
  [/^\/user\/([\w-]+)$/, "games", () => import("./views/profile.js")],
  [/^\/analysis(?:\/([\w-]+))?$/, "analysis", () => import("./views/analysis.js")],
  [/^\/settings$/, "settings", () => import("./views/settings.js")],
];

const app = document.getElementById("app");
let cleanup = null; // teardown function of the mounted view
let routeToken = 0; // guards against a slow view mounting after a newer navigation

export function renderAccount() {
  const box = document.getElementById("account");
  if (session.user) {
    fill(
      box,
      h("a.user", { href: `#/user/${session.user.username}` }, session.user.username),
      h(
        "button.link",
        {
          type: "button",
          onclick: async () => {
            await api("/auth/logout", { method: "POST" });
            setUser(null);
            renderAccount();
            navigate("#/");
          },
        },
        "Log out",
      ),
    );
  } else {
    fill(
      box,
      h("a", { href: "#/login" }, "Log in"),
      session.config.allow_registration && h("a.button.small", { href: "#/register" }, "Sign up"),
    );
  }
}

async function route() {
  const token = ++routeToken;
  const path = location.hash.replace(/^#/, "") || "/";
  cleanup?.();
  cleanup = null;

  const match = ROUTES.map(([pattern, nav, load]) => ({ m: path.match(pattern), nav, load })).find(
    (r) => r.m,
  );
  if (!match) {
    fill(app, h("div.card.narrow", {}, h("h2", {}, "Page not found"), h("a", { href: "#/" }, "Back to the lobby")));
    return;
  }
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.dataset.nav === match.nav);
  }
  try {
    const view = await match.load();
    if (token !== routeToken) return;
    app.replaceChildren();
    window.scrollTo(0, 0);
    const teardown = await view.mount(app, { path, id: match.m[1] });
    if (token !== routeToken) teardown?.();
    else cleanup = teardown ?? null;
  } catch (error) {
    console.error(error);
    fill(app, h("div.card.narrow", {}, h("h2", {}, "Something went wrong"), h("p.muted", {}, error.message)));
  }
}

async function start() {
  try {
    await loadSession();
  } catch (error) {
    fill(app, h("p.center.muted", {}, `Cannot reach the server: ${error.message}`));
    setTimeout(start, 3000);
    return;
  }
  document.title = session.config.site_name;
  document.getElementById("site-name").textContent = session.config.site_name;
  renderAccount();
  window.addEventListener("hashchange", route);
  window.addEventListener("unhandledrejection", (event) => {
    if (event.reason?.message) toast(event.reason.message, "error");
  });
  route();
}

start();
