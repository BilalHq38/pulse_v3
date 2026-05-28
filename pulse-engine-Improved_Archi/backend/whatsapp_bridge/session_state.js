const SESSION_STATES = Object.freeze({
  IDLE: "idle",
  QR_REQUIRED: "qr_required",
  QR_SCANNED: "qr_scanned",
  AUTHENTICATED: "authenticated",
  INITIALIZING: "initializing",
  READY: "ready",
  RECONNECTING: "reconnecting",
  FAILED: "failed",
  DISCONNECTED: "disconnected",
});

const SESSION_PROGRESS = Object.freeze({
  [SESSION_STATES.IDLE]: 0,
  [SESSION_STATES.QR_REQUIRED]: 20,
  [SESSION_STATES.QR_SCANNED]: 45,
  [SESSION_STATES.AUTHENTICATED]: 65,
  [SESSION_STATES.INITIALIZING]: 80,
  [SESSION_STATES.READY]: 100,
  [SESSION_STATES.RECONNECTING]: 55,
  [SESSION_STATES.FAILED]: 0,
  [SESSION_STATES.DISCONNECTED]: 0,
});

const SESSION_MESSAGES = Object.freeze({
  [SESSION_STATES.IDLE]: "Starting WhatsApp session",
  [SESSION_STATES.QR_REQUIRED]: "Waiting for QR scan",
  [SESSION_STATES.QR_SCANNED]: "QR scanned successfully",
  [SESSION_STATES.AUTHENTICATED]: "Authenticating WhatsApp session",
  [SESSION_STATES.INITIALIZING]: "Finalizing WhatsApp connection",
  [SESSION_STATES.READY]: "Connected successfully",
  [SESSION_STATES.RECONNECTING]: "Reconnecting",
  [SESSION_STATES.FAILED]: "Failed to connect, please scan again",
  [SESSION_STATES.DISCONNECTED]: "Disconnected",
});

function normalizeSessionState(value) {
  const state = String(value || "").trim().toLowerCase();
  if (Object.values(SESSION_STATES).includes(state)) return state;
  if (state === "need_qr") return SESSION_STATES.QR_REQUIRED;
  if (state === "connected") return SESSION_STATES.READY;
  return SESSION_STATES.IDLE;
}

function progressForState(value) {
  return SESSION_PROGRESS[normalizeSessionState(value)] ?? 0;
}

function messageForState(value, options = {}) {
  const state = normalizeSessionState(value);
  if (state === SESSION_STATES.FAILED && options.retrying) {
    return "Failed to connect, retrying";
  }
  if (state === SESSION_STATES.RECONNECTING && options.retrying) {
    return "Failed to connect, retrying";
  }
  return SESSION_MESSAGES[state] || SESSION_MESSAGES[SESSION_STATES.IDLE];
}

function connectedForState(value) {
  return normalizeSessionState(value) === SESSION_STATES.READY;
}

module.exports = {
  SESSION_STATES,
  SESSION_PROGRESS,
  SESSION_MESSAGES,
  normalizeSessionState,
  progressForState,
  messageForState,
  connectedForState,
};
