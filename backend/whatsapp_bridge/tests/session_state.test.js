const test = require("node:test");
const assert = require("node:assert/strict");

const {
  SESSION_STATES,
  connectedForState,
  messageForState,
  normalizeSessionState,
  progressForState,
} = require("../session_state");

test("QR generated returns qr_required with progress 20", () => {
  assert.equal(normalizeSessionState("need_qr"), SESSION_STATES.QR_REQUIRED);
  assert.equal(progressForState(SESSION_STATES.QR_REQUIRED), 20);
  assert.equal(messageForState(SESSION_STATES.QR_REQUIRED), "Waiting for QR scan");
});

test("authenticated and initializing states expose mid-connection progress", () => {
  assert.equal(progressForState(SESSION_STATES.AUTHENTICATED), 65);
  assert.equal(progressForState(SESSION_STATES.INITIALIZING), 80);
  assert.equal(messageForState(SESSION_STATES.AUTHENTICATED), "Authenticating WhatsApp session");
  assert.equal(messageForState(SESSION_STATES.INITIALIZING), "Finalizing WhatsApp connection");
});

test("ready state is connected and progress 100", () => {
  assert.equal(connectedForState(SESSION_STATES.READY), true);
  assert.equal(progressForState(SESSION_STATES.READY), 100);
  assert.equal(messageForState(SESSION_STATES.READY), "Connected successfully");
});

test("retrying failure shows retrying message before final failed copy", () => {
  assert.equal(messageForState(SESSION_STATES.RECONNECTING, { retrying: true }), "Failed to connect, retrying");
  assert.equal(messageForState(SESSION_STATES.FAILED, { retrying: false }), "Failed to connect, please scan again");
});
