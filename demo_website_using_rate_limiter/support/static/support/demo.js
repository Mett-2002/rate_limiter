"use strict";
const byId = id => document.getElementById(id);
const timeline = new RequestTimeline(byId("request-timeline"));
const initialUser = new URLSearchParams(location.search).get("user_id");
let userId = initialUser === "bob" ? "bob" : "alice";
let currentQuota = null;
let sending = false;

function feedback(text, kind = "") {
  byId("feedback").textContent = text;
  byId("feedback").className = "feedback " + kind;
}

function paintQuota(data) {
  currentQuota = data;
  timeline.setQuota(userId, data);
  byId("remaining").textContent = data.remaining;
  byId("limit").textContent = data.limit;
  byId("policy").textContent = `${data.limit} requests / ${data.window_seconds} seconds`;
  byId("connection").textContent = "API connected";
  byId("connection").className = "badge online";
  byId("window-info").textContent = `${data.used} used in the last ${data.window_seconds}s`;
  byId("slots").replaceChildren();
  const slots = Math.min(data.limit, 30);
  for (let index = 0; index < slots; index++) {
    const slot = document.createElement("span");
    slot.className = "slot" + (index < Math.round(data.used / data.limit * slots) ? " used" : "");
    slot.setAttribute("aria-hidden", "true");
    byId("slots").append(slot);
  }
  byId("slots").setAttribute("aria-label", `${data.used} used; ${data.remaining} available`);
  paintCountdown();
}

function paintCountdown() {
  if (currentQuota && currentQuota.remaining === 0 && currentQuota.next_available_at) {
    const seconds = Math.max(0, Math.ceil(currentQuota.next_available_at - Date.now() / 1000));
    byId("next-slot").textContent = seconds ? `Next place opens in about ${seconds}s. You can still try a request.` : "Checking for an expired request…";
  } else byId("next-slot").textContent = `Each accepted request expires after ${currentQuota ? currentQuota.window_seconds : 60} seconds.`;
}

async function refreshQuota() {
  const identity = userId;
  try {
    const response = await fetch(`/quota/?user_id=${encodeURIComponent(identity)}`, {cache: "no-store"});
    const data = await response.json();
    if (identity !== userId) return;
    if (!response.ok) throw new Error(data.detail || "Cannot read capacity.");
    paintQuota(data);
  } catch (error) {
    if (identity !== userId) return;
    currentQuota = null;
    byId("connection").textContent = "API unavailable";
    byId("connection").className = "badge offline";
    byId("remaining").textContent = "—";
    byId("next-slot").textContent = error.message;
  }
}

function switchUser(value) {
  if (sending) return;
  userId = value === "bob" ? "bob" : "alice";
  byId("identity").value = userId;
  currentQuota = null;
  byId("remaining").textContent = "—";
  timeline.setUser(userId);
  const url = new URL(location.href);
  url.searchParams.set("user_id", userId);
  history.replaceState(null, "", url);
  feedback(`Ready as ${userId}.`);
  refreshQuota();
}

byId("identity").addEventListener("change", () => switchUser(byId("identity").value));
byId("request-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (sending) return;
  const identity = userId;
  sending = true;
  byId("send").disabled = byId("identity").disabled = true;
  byId("send").textContent = "Sending…";
  feedback("Checking the rolling window…");
  try {
    const response = await fetch("/requests/", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value},
      body: JSON.stringify({user_id: identity}),
    });
    const data = await response.json();
    if (data.quota) paintQuota(data.quota);
    if (response.status === 200 || response.status === 429) timeline.record(identity, response.status);
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}: request failed.`);
    feedback(`HTTP 200 · Accepted for ${identity}.`, "success");
  } catch (error) { feedback(error.message, "error"); }
  finally {
    sending = false;
    byId("send").disabled = byId("identity").disabled = false;
    byId("send").textContent = "Send request →";
    refreshQuota();
  }
});

switchUser(userId);
setInterval(refreshQuota, 3000);
setInterval(paintCountdown, 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshQuota(); });
