const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bridgeSource = fs.readFileSync(path.join(__dirname, "../bridge.js"), "utf8");

function functionBody(name) {
  const start = bridgeSource.indexOf(`function ${name}`);
  assert.ok(start >= 0, `${name} function not found`);
  const next = bridgeSource.indexOf("\nfunction ", start + 1);
  return bridgeSource.slice(start, next >= 0 ? next : bridgeSource.length);
}

test("QR event stores the latest QR string and marks QR required", () => {
  const qrHandlerStart = bridgeSource.indexOf('client.on("qr"');
  assert.ok(qrHandlerStart >= 0, "QR handler not found");
  const qrHandler = bridgeSource.slice(qrHandlerStart, bridgeSource.indexOf('client.on("authenticated"', qrHandlerStart));

  assert.match(qrHandler, /session\.lastQrString\s*=/);
  assert.match(qrHandler, /SESSION_STATES\.QR_REQUIRED/);
  assert.match(qrHandler, /whatsapp\.qr\.generated/);
  assert.match(qrHandler, /qr_present/);
});

test("/qr endpoint returns a frontend data URL when a QR string exists", () => {
  const qrRouteStart = bridgeSource.indexOf('app.get("/qr"');
  assert.ok(qrRouteStart >= 0, "/qr route not found");
  const qrRoute = bridgeSource.slice(qrRouteStart, bridgeSource.indexOf('app.post("/disconnect"', qrRouteStart));

  assert.match(qrRoute, /QRImage\.toDataURL/);
  assert.match(qrRoute, /startsWith\("data:image\/"\)/);
  assert.match(qrRoute, /qr_data_url:\s*safeDataUrl/);
  assert.match(qrRoute, /whatsapp\.qr\.status_requested/);
});

test("mobile outbound backfill is disabled before getChats or fetchMessages can run", () => {
  assert.match(bridgeSource, /WHATSAPP_ENABLE_MOBILE_OUTBOUND_BACKFILL,\s*false/);
  const syncBody = functionBody("syncRecentMobileOutboundMessages");
  const gateIndex = syncBody.indexOf("mobileOutboundBackfillEnabled");
  const getChatsIndex = syncBody.indexOf("getChats");
  const fetchMessagesIndex = syncBody.indexOf("fetchMessages");

  assert.ok(gateIndex >= 0, "backfill gate not found");
  assert.ok(getChatsIndex > gateIndex, "getChats must be behind the backfill gate");
  assert.ok(fetchMessagesIndex > gateIndex, "fetchMessages must be behind the backfill gate");
});

test("reaction events pass through replay guard before forwarding", () => {
  const reactionBody = functionBody("forwardInboundReaction");
  const guardIndex = reactionBody.indexOf("guardMessageReplay");
  const forwardIndex = reactionBody.indexOf("postWebhookPayload");

  assert.ok(guardIndex >= 0, "reaction replay guard not found");
  assert.ok(forwardIndex > guardIndex, "reaction forwarding must occur after replay guard");
  assert.match(reactionBody, /whatsapp\.reaction\.backfill_ignored/);
  assert.match(reactionBody, /whatsapp\.reaction\.duplicate_ignored/);
  assert.match(reactionBody, /whatsapp\.reaction\.forwarded/);
});
