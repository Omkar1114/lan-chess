// Session state shared across views: current user, server config, preferences.

import { api } from "./api.js";

export const THEMES = {
  brown: { name: "Wood", light: "#f0d9b5", dark: "#b58863" },
  green: { name: "Tournament", light: "#eeeed2", dark: "#769656" },
  blue: { name: "Ocean", light: "#dee3e6", dark: "#8ca2ad" },
  slate: { name: "Slate", light: "#c9cdd3", dark: "#6f7785" },
  purple: { name: "Amethyst", light: "#e8dff2", dark: "#8d6fb0" },
  sand: { name: "Desert", light: "#f3e5c6", dark: "#c98f56" },
  mint: { name: "Mint", light: "#e3f1e7", dark: "#5fa58a" },
};

export const DEFAULT_PREFS = {
  board_theme: "brown",
  custom_light: "#f0d9b5",
  custom_dark: "#b58863",
  piece_set: "cburnett",
  show_coordinates: true,
  highlight_last_move: true,
  show_legal_moves: true,
  sounds: true,
  auto_queen: false,
};

const GUEST_KEY = "lanchess.prefs";

export const session = {
  user: null,
  config: null,
  prefs: { ...DEFAULT_PREFS },
};

function readGuestPrefs() {
  try {
    return JSON.parse(localStorage.getItem(GUEST_KEY)) ?? {};
  } catch {
    return {};
  }
}

/** Colours of the active board theme. */
export function boardColors(prefs = session.prefs) {
  if (prefs.board_theme === "custom") return { light: prefs.custom_light, dark: prefs.custom_dark };
  return THEMES[prefs.board_theme] ?? THEMES.brown;
}

/** Push the theme colours into CSS variables used by every board on the page. */
export function applyPrefs() {
  const { light, dark } = boardColors();
  const root = document.documentElement.style;
  root.setProperty("--sq-light", light);
  root.setProperty("--sq-dark", dark);
  window.dispatchEvent(new Event("prefs-changed"));
}

/** Adopt a user (or null for guests) and load the matching preferences. */
export function setUser(user) {
  session.user = user;
  session.prefs = { ...DEFAULT_PREFS, ...(user ? user.prefs : readGuestPrefs()) };
  applyPrefs();
}

/** Load config and the current user; called once at start-up. */
export async function loadSession() {
  const [config, me] = await Promise.all([api("/config"), api("/me")]);
  session.config = config;
  setUser(me.user);
}

/** Apply new preferences and persist them: on the account, or locally for guests. */
export async function savePrefs(prefs) {
  session.prefs = { ...prefs };
  applyPrefs();
  if (session.user) {
    session.user.prefs = await api("/me/prefs", { method: "PUT", body: prefs });
  } else {
    try {
      localStorage.setItem(GUEST_KEY, JSON.stringify(prefs));
    } catch {
      /* private mode: preferences last for this page only */
    }
  }
}
