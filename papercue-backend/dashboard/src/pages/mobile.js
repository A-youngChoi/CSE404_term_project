// Presenter phone view: polls the shared simulation state and shows only the current prompt.
import { pres } from "../api.js";
import { h, mount } from "../dom.js";
import { P } from "../lab/common.js";
import { mobileCard } from "../lab/mobileCard.js";

export const POLL_MS = 500;

/** Remaining display time, advanced locally between polls while the researcher's playback is running. */
export function interpolateRemaining(state, receivedAt, now) {
  if (state?.remaining_seconds === null || state?.remaining_seconds === undefined) return undefined;
  const drift = state.playing ? ((now - receivedAt) / 1000) * (state.speed ?? 1) : 0;
  return Math.max(0, state.remaining_seconds - drift);
}

export async function renderMobile(root, params) {
  const runId = params[0];
  const screen = h("div", { class: "mobile-screen" });
  mount(root, h("div", { class: "mobile-page" }, screen,
    h("p", { class: "mobile-hint" }, P.mobile.hint)));
  let state = null;
  let receivedAt = Date.now();
  const paint = () => mount(screen, mobileCard(state, { remaining: interpolateRemaining(state, receivedAt, Date.now()) }));
  const poll = async () => {
    if (!root.isConnected) return;
    try {
      state = runId ? await pres.runMobile(runId) : await pres.mobile();
      receivedAt = Date.now();
    } catch {
      state = { run_id: null };
    }
    paint();
    setTimeout(poll, POLL_MS);
  };
  const ticker = setInterval(() => (root.isConnected ? state && paint() : clearInterval(ticker)), 200);
  await poll();
}
