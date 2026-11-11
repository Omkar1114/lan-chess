// Move sounds synthesised with the Web Audio API - no audio files to ship or license.

import { session } from "./state.js";

let context = null;

function audio() {
  const Ctor = window.AudioContext ?? window.webkitAudioContext;
  if (!Ctor) return null;
  context ??= new Ctor();
  if (context.state === "suspended") context.resume();
  return context;
}

// Browsers only allow audio after a user gesture; unlock on the first one.
window.addEventListener("pointerdown", () => audio(), { once: true });

/** Short percussive "tock": a pitched blip with a fast decay. */
function blip(ctx, { freq, duration, type = "triangle", gain = 0.25, delay = 0 }) {
  const start = ctx.currentTime + delay;
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  osc.frequency.exponentialRampToValueAtTime(freq * 0.6, start + duration);
  amp.gain.setValueAtTime(gain, start);
  amp.gain.exponentialRampToValueAtTime(0.001, start + duration);
  osc.connect(amp).connect(ctx.destination);
  osc.start(start);
  osc.stop(start + duration);
}

const SOUNDS = {
  move: [{ freq: 520, duration: 0.07 }],
  capture: [
    { freq: 300, duration: 0.09, type: "square", gain: 0.18 },
    { freq: 180, duration: 0.12, delay: 0.03 },
  ],
  check: [
    { freq: 700, duration: 0.08 },
    { freq: 940, duration: 0.12, delay: 0.08 },
  ],
  end: [
    { freq: 520, duration: 0.18, type: "sine" },
    { freq: 390, duration: 0.18, type: "sine", delay: 0.16 },
    { freq: 260, duration: 0.4, type: "sine", delay: 0.32 },
  ],
  notify: [
    { freq: 660, duration: 0.1, type: "sine" },
    { freq: 880, duration: 0.14, type: "sine", delay: 0.1 },
  ],
};

export function play(name) {
  if (!session.prefs.sounds) return;
  const ctx = audio();
  if (!ctx) return;
  for (const part of SOUNDS[name] ?? []) blip(ctx, part);
}

/** Pick the sound for a move from its SAN notation. */
export function playForMove(san) {
  if (san.includes("#") || san.includes("+")) play("check");
  else if (san.includes("x")) play("capture");
  else play("move");
}
