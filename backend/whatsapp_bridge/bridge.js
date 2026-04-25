/**
 * whatsapp_bridge/bridge.js
 *
 * WhatsApp Web bridge with per-user or per-tenant scoped sessions.
 * Each scope gets its own LocalAuth directory, QR state, and socket.
 */

const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const QRImage = require("qrcode");
const express = require("express");
const axios = require("axios");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const util = require("util");

function loadEnvFile(filePath) {
  try {
    if (!fs.existsSync(filePath)) return;
    const text = fs.readFileSync(filePath, "utf8");
    for (const rawLine of text.split(/\r?\n/)) {
      const line = String(rawLine || "").trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      let value = line.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      if (key && !process.env[key]) process.env[key] = value;
    }
  } catch (err) {
    console.warn(`Failed to load env file ${filePath}: ${err.message}`);
  }
}

for (const candidate of [
  path.resolve(__dirname, "../../.env"),
  path.resolve(process.cwd(), ".env"),
]) {
  loadEnvFile(candidate);
}

const BRIDGE_PORT = process.env.BRIDGE_PORT || 3001;
const PYTHON_BACKEND = process.env.PYTHON_BACKEND || "http://localhost:8000";
const WHATSAPP_WEBHOOK_PATH = process.env.WHATSAPP_WEBHOOK_PATH || "/api/webhook/meta/whatsapp";
const BRIDGE_SECRET = process.env.BRIDGE_SECRET || process.env.WHATSAPP_BRIDGE_SECRET || "";
const DEFAULT_BRIDGE_COMPANY_ID = (process.env.BRIDGE_COMPANY_ID || "").trim();
const WHATSAPP_PHONE_NUMBER_ID = (process.env.WHATSAPP_PHONE_NUMBER_ID || process.env.PHONE_NUMBER_ID || "").trim();
const WHATSAPP_BUSINESS_ACCOUNT_ID = (process.env.WHATSAPP_BUSINESS_ACCOUNT_ID || "").trim();
const WEBHOOK_SIGNING_SECRET =
  process.env.WHATSAPP_WEBHOOK_SECRET ||
  process.env.META_WEBHOOK_SECRET ||
  process.env.BRIDGE_WEBHOOK_SECRET ||
  "";
const MY_NUMBER = process.env.MY_WHATSAPP_NUMBER || "";

const WWEBJS_AUTH_DIR = path.join(__dirname, ".wwebjs_auth");
const SESSION_METADATA_DIR = path.join(WWEBJS_AUTH_DIR, ".scope_meta");
fs.mkdirSync(WWEBJS_AUTH_DIR, { recursive: true });
fs.mkdirSync(SESSION_METADATA_DIR, { recursive: true });

const CHROMIUM_STALE_LOCK_NAMES = new Set([
  "SingletonLock",
  "SingletonSocket",
  "SingletonCookie",
  "lockfile",
]);

const sessions = new Map();

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function removeChromiumSingletonLocks(rootDir) {
  if (!rootDir || !fs.existsSync(rootDir)) return;
  const walk = (dir) => {
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!CHROMIUM_STALE_LOCK_NAMES.has(entry.name)) continue;
      try {
        fs.unlinkSync(full);
        console.log(`Cleared stale Chromium lock: ${full}`);
      } catch (err) {
        console.warn(`Could not remove ${full}: ${err.message}`);
      }
    }
  };
  walk(rootDir);
}

function buildMetaSignature(rawPayload) {
  if (!WEBHOOK_SIGNING_SECRET) {
    throw new Error("Webhook signing secret is not configured");
  }
  const digest = crypto
    .createHmac("sha256", WEBHOOK_SIGNING_SECRET)
    .update(rawPayload, "utf8")
    .digest("hex");
  return `sha256=${digest}`;
}

function parseDataUrl(dataUrl) {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:")) return null;
  const match = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
  if (!match) return null;
  return { mimeType: match[1], data: match[2] };
}

function attachmentToMedia(attachment) {
  if (!attachment || String(attachment.type || "").toLowerCase() !== "image") return null;
  const parsed = parseDataUrl(attachment.url || attachment.file_url || "");
  if (!parsed) return null;
  const fallbackExt = parsed.mimeType.split("/")[1] || "jpg";
  const filename = attachment.name || `image.${fallbackExt}`;
  return new MessageMedia(parsed.mimeType, parsed.data, filename);
}

function trimText(value, maxLength = 500) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 3)}...`;
}

function isLowSignalErrorText(value) {
  const text = trimText(value, 400);
  return !text || text.length <= 2 || /^[a-z]$/i.test(text);
}

function formatBridgeError(err) {
  const constructorName =
    err && err.constructor && typeof err.constructor.name === "string" ? err.constructor.name : "";
  const message = typeof err === "string" ? err : trimText(err && err.message ? err.message : "", 400);
  const rawStack = trimText(err && err.stack ? err.stack : "", 1500);
  const stackHeadline = trimText(
    rawStack
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line && line !== message),
    400,
  );
  const stack = rawStack;
  const inspected = trimText(util.inspect(err, { depth: 4, breakLength: 120 }), 1500);
  const detailParts = [message, constructorName, stack, inspected].filter(Boolean);
  const preferredMessage = !isLowSignalErrorText(message)
    ? message
    : !isLowSignalErrorText(stackHeadline)
      ? stackHeadline
      : !isLowSignalErrorText(inspected)
        ? inspected
        : message;
  return {
    clientMessage: preferredMessage || inspected || constructorName || "Failed to send WhatsApp message",
    logMessage: detailParts.join(" | "),
  };
}

function normalizePhoneNumber(value) {
  return String(value || "").replace(/\D/g, "");
}

async function resolveChatTarget(client, to) {
  const phoneDigits = normalizePhoneNumber(to);
  if (!phoneDigits) {
    return {
      ok: false,
      statusCode: 400,
      error: "Recipient phone number is missing or invalid.",
      phoneDigits: "",
    };
  }

  if (typeof client.getNumberId === "function") {
    try {
      const resolved = await client.getNumberId(phoneDigits);
      if (resolved && typeof resolved === "object" && resolved._serialized) {
        return { ok: true, chatId: resolved._serialized, phoneDigits };
      }
      if (typeof resolved === "string" && resolved.trim()) {
        return { ok: true, chatId: resolved.trim(), phoneDigits };
      }
    } catch (err) {
      const formatted = formatBridgeError(err);
      return {
        ok: false,
        statusCode: 502,
        error: `Could not resolve WhatsApp recipient ${phoneDigits}.`,
        details: formatted.clientMessage,
        logMessage: `getNumberId failed: ${formatted.logMessage}`,
        phoneDigits,
      };
    }
  }

  const fallbackChatId = `${phoneDigits}@c.us`;
  if (typeof client.isRegisteredUser === "function") {
    try {
      const hasWhatsApp = await client.isRegisteredUser(fallbackChatId);
      if (!hasWhatsApp) {
        return {
          ok: false,
          statusCode: 404,
          error: `Phone number ${phoneDigits} is not registered on WhatsApp.`,
          phoneDigits,
        };
      }
    } catch (err) {
      const formatted = formatBridgeError(err);
      return {
        ok: false,
        statusCode: 502,
        error: `Could not verify WhatsApp recipient ${phoneDigits}.`,
        details: formatted.clientMessage,
        logMessage: `isRegisteredUser failed: ${formatted.logMessage}`,
        phoneDigits,
      };
    }
  }

  return { ok: true, chatId: fallbackChatId, phoneDigits };
}

function requireBridgeSecret(req, res) {
  if (!BRIDGE_SECRET) {
    res.status(500).json({ error: "BRIDGE_SECRET is not configured" });
    return false;
  }
  if (String(req.headers["x-bridge-secret"] || "") !== BRIDGE_SECRET) {
    res.status(401).json({ error: "Invalid secret" });
    return false;
  }
  return true;
}

function sanitizeScopeFragment(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return cleaned.slice(0, 80) || "default";
}

function normalizeScope(input = {}) {
  const companyId = String(input.companyId || DEFAULT_BRIDGE_COMPANY_ID || "").trim();
  const userId = String(input.userId || "").trim();
  if (userId) {
    return {
      scopeKey: `user-${sanitizeScopeFragment(companyId || "global")}-${sanitizeScopeFragment(userId)}`,
      companyId,
      userId,
    };
  }
  if (companyId) {
    return {
      scopeKey: `company-${sanitizeScopeFragment(companyId)}`,
      companyId,
      userId: "",
    };
  }
  return {
    scopeKey: "default",
    companyId: DEFAULT_BRIDGE_COMPANY_ID,
    userId: "",
  };
}

function scopeMetadataPath(scopeKey) {
  return path.join(SESSION_METADATA_DIR, `${scopeKey}.json`);
}

function persistSessionMetadata(session) {
  const payload = {
    scopeKey: session.scopeKey,
    companyId: session.companyId || "",
    userId: session.userId || "",
    phone: session.phone || "",
    updatedAt: new Date().toISOString(),
  };
  try {
    fs.writeFileSync(scopeMetadataPath(session.scopeKey), JSON.stringify(payload, null, 2), "utf8");
  } catch (err) {
    console.warn(`Could not persist bridge metadata for ${session.scopeKey}: ${err.message}`);
  }
}

function loadSavedScopes() {
  try {
    return fs
      .readdirSync(SESSION_METADATA_DIR)
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        try {
          const raw = fs.readFileSync(path.join(SESSION_METADATA_DIR, name), "utf8");
          return JSON.parse(raw);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}

function sessionAuthDir(scopeKey) {
  return path.join(WWEBJS_AUTH_DIR, `session-${scopeKey}`);
}

function sessionStatus(session) {
  if (!session) return "missing";
  if (session.isReady) return "ready";
  if (session.lastQrString) return "need_qr";
  if (session.initPromise) return "initializing";
  return "disconnected";
}

function sessionFromRequest(req) {
  return normalizeScope({
    companyId:
      req.headers["x-bridge-company-id"] ||
      req.query.company_id ||
      (req.body && req.body.company_id) ||
      DEFAULT_BRIDGE_COMPANY_ID,
    userId:
      req.headers["x-bridge-user-id"] ||
      req.query.user_id ||
      (req.body && req.body.user_id) ||
      "",
  });
}

async function forwardInboundMessage(session, msg) {
  if (msg.isGroupMsg || msg.from === "status@broadcast") return;

  const senderPhone = String(msg.from || "").replace("@c.us", "").replace(/\D/g, "");
  const senderName = msg._data && msg._data.notifyName ? msg._data.notifyName : `WhatsApp ${senderPhone}`;
  const isImageMessage = Boolean(msg.hasMedia);
  let imagePayload = null;

  if (isImageMessage) {
    try {
      const media = await msg.downloadMedia();
      if (media && typeof media.mimetype === "string" && media.mimetype.startsWith("image/")) {
        imagePayload = {
          data_url: `data:${media.mimetype};base64,${media.data}`,
          filename: media.filename || "",
          mime_type: media.mimetype,
          size: Number((msg._data && msg._data.size) || 0),
          caption: msg.body || "",
        };
      }
    } catch (err) {
      console.error(`[${session.scopeKey}] Media download failed: ${err.message}`);
    }
  }

  try {
    const payload = {
      entry: [{
        id: WHATSAPP_BUSINESS_ACCOUNT_ID || undefined,
        changes: [{
          value: {
            business_account_id: WHATSAPP_BUSINESS_ACCOUNT_ID || undefined,
            metadata: {
              phone_number_id: WHATSAPP_PHONE_NUMBER_ID,
              display_phone_number: session.phone || MY_NUMBER,
              company_id: session.companyId || DEFAULT_BRIDGE_COMPANY_ID,
              bridge_scope: session.scopeKey,
              bridge_user_id: session.userId || "",
            },
            messages: [{
              from: senderPhone,
              type: imagePayload ? "image" : "text",
              text: { body: msg.body || "" },
              image: imagePayload || undefined,
              timestamp: Math.floor(Date.now() / 1000),
              id: msg.id.id,
            }],
            contacts: [{
              profile: { name: senderName },
              wa_id: senderPhone,
            }],
          },
        }],
      }],
    };

    const rawPayload = JSON.stringify(payload);
    const signature = buildMetaSignature(rawPayload);

    await axios.post(`${PYTHON_BACKEND}${WHATSAPP_WEBHOOK_PATH}`, rawPayload, {
      headers: {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature,
        "X-Bridge-Secret": BRIDGE_SECRET,
        "X-Bridge-Company-Id": session.companyId || "",
        "X-Bridge-User-Id": session.userId || "",
      },
      timeout: 10000,
    });
    console.log(`[${session.scopeKey}] Forwarded inbound message to Python backend`);
  } catch (err) {
    console.error(`[${session.scopeKey}] Forward failed: ${err.message}`);
  }
}

function buildClient(session) {
  return new Client({
    authStrategy: new LocalAuth({
      dataPath: WWEBJS_AUTH_DIR,
      clientId: session.scopeKey,
    }),
    puppeteer: {
      headless: true,
      executablePath: process.env.CHROME_BIN || process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    },
  });
}

function attachClientHandlers(session) {
  const { client } = session;

  client.on("qr", (qr) => {
    session.lastQrString = String(qr || "");
    session.isReady = false;
    persistSessionMetadata(session);
    console.log(`\n[${session.scopeKey}] Scan this QR code with WhatsApp:`);
    qrcode.generate(qr, { small: true });
  });

  client.on("authenticated", () => {
    session.lastQrString = "";
    persistSessionMetadata(session);
    console.log(`[${session.scopeKey}] WhatsApp authenticated`);
  });

  client.on("auth_failure", (message) => {
    session.isReady = false;
    session.lastQrString = "";
    persistSessionMetadata(session);
    console.error(`[${session.scopeKey}] Auth failed: ${message}`);
  });

  client.on("ready", () => {
    session.isReady = true;
    session.lastQrString = "";
    session.phone = String(
      (client.info && client.info.wid && client.info.wid.user) || MY_NUMBER || "",
    ).trim();
    persistSessionMetadata(session);
    console.log(
      `[${session.scopeKey}] READY company=${session.companyId || "-"} user=${session.userId || "-"} phone=${session.phone || "-"}`,
    );
  });

  client.on("disconnected", (reason) => {
    session.isReady = false;
    session.lastQrString = "";
    session.phone = "";
    persistSessionMetadata(session);
    console.log(`[${session.scopeKey}] Disconnected: ${reason}`);
  });

  client.on("message", async (msg) => {
    await forwardInboundMessage(session, msg);
  });
}

function createSession(scopeInput) {
  const scope = normalizeScope(scopeInput);
  const existing = sessions.get(scope.scopeKey);
  if (existing) {
    if (scope.companyId) existing.companyId = scope.companyId;
    if (scope.userId) existing.userId = scope.userId;
    persistSessionMetadata(existing);
    return existing;
  }

  const session = {
    scopeKey: scope.scopeKey,
    companyId: scope.companyId,
    userId: scope.userId,
    phone: "",
    isReady: false,
    initPromise: null,
    lastQrString: "",
    client: null,
  };
  session.client = buildClient(session);
  attachClientHandlers(session);
  sessions.set(session.scopeKey, session);
  persistSessionMetadata(session);
  return session;
}

async function ensureSessionInitialized(session) {
  if (!session || session.isReady) return;
  if (session.initPromise) return session.initPromise;

  removeChromiumSingletonLocks(sessionAuthDir(session.scopeKey));
  session.initPromise = Promise.resolve()
    .then(() => session.client.initialize())
    .catch((err) => {
      console.error(`[${session.scopeKey}] Initialize failed: ${err.message || err}`);
      throw err;
    })
    .finally(() => {
      session.initPromise = null;
    });
  return session.initPromise;
}

async function waitForReady(session, timeoutMs = 12000) {
  if (!session) return false;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (session.isReady) return true;
    if (session.lastQrString) return false;
    await sleep(250);
  }
  return session.isReady;
}

async function destroySession(session) {
  if (!session) return;
  try {
    if (session.client) await session.client.destroy();
  } catch (err) {
    console.warn(`[${session.scopeKey}] Destroy failed: ${err.message || err}`);
  }
  sessions.delete(session.scopeKey);
}

async function restoreSavedSessions() {
  const savedScopes = loadSavedScopes();
  for (const scope of savedScopes) {
    const session = createSession(scope);
    ensureSessionInitialized(session).catch(() => {});
  }
}

const app = express();
app.use(express.json({ limit: "10mb" }));

app.get("/health", (_req, res) => {
  res.json({
    status: sessions.size > 0 ? "running" : "idle",
    bridgeSecretConfigured: Boolean(BRIDGE_SECRET),
    webhookSecretConfigured: Boolean(WEBHOOK_SIGNING_SECRET),
    active_sessions: Array.from(sessions.values()).map((session) => ({
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      phone: session.phone || "",
      status: sessionStatus(session),
    })),
  });
});

app.get("/session", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  ensureSessionInitialized(session).catch(() => {});
  res.json({
    status: sessionStatus(session),
    phone: session.phone || "",
    scope: session.scopeKey,
    bridgeSecretConfigured: Boolean(BRIDGE_SECRET),
  });
});

app.get("/qr", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  ensureSessionInitialized(session).catch(() => {});

  if (session.isReady) {
    return res.json({ qr_data_url: "", bridge_status: "ready", scope: session.scopeKey });
  }
  if (!session.lastQrString) {
    return res.json({ qr_data_url: "", bridge_status: sessionStatus(session), scope: session.scopeKey });
  }

  QRImage.toDataURL(session.lastQrString, { errorCorrectionLevel: "M", width: 280 }, (err, dataUrl) => {
    if (err) {
      console.error(`[${session.scopeKey}] QR PNG error: ${err.message}`);
      return res.status(500).json({ error: err.message });
    }
    res.json({ qr_data_url: dataUrl, bridge_status: "need_qr", scope: session.scopeKey });
  });
});

app.post("/disconnect", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const scope = sessionFromRequest(req);
  const session = sessions.get(scope.scopeKey);
  if (!session) {
    return res.json({ success: true, status: "disconnected", scope: scope.scopeKey });
  }

  try {
    session.isReady = false;
    session.lastQrString = "";
    session.phone = "";
    try {
      await session.client.logout();
    } catch (err) {
      console.warn(`[${session.scopeKey}] Logout warning: ${err.message || err}`);
    }
    await destroySession(session);
    res.json({ success: true, status: "disconnected", scope: scope.scopeKey });
  } catch (err) {
    console.error(`[${scope.scopeKey}] Disconnect failed: ${err.message || err}`);
    res.status(500).json({ success: false, error: err.message || "Disconnect failed" });
  }
});

app.post("/send", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;

  const { to, message, attachments } = req.body || {};
  const mediaItems = Array.isArray(attachments)
    ? attachments.map(attachmentToMedia).filter(Boolean)
    : [];
  if (!to || (!String(message || "").trim() && mediaItems.length === 0)) {
    return res.status(400).json({ error: "Missing 'to' or content" });
  }

  const session = createSession(sessionFromRequest(req));
  ensureSessionInitialized(session).catch(() => {});
  const ready = await waitForReady(session, 12000);
  if (!ready) {
    return res.status(503).json({
      error: session.lastQrString
        ? "WhatsApp session is not connected for this account. Please scan the QR code first."
        : "WhatsApp session is still starting. Please retry in a few seconds.",
      scope: session.scopeKey,
      status: sessionStatus(session),
    });
  }

  const target = await resolveChatTarget(session.client, to);
  if (!target.ok) {
    console.warn(
      `[${session.scopeKey}] Send blocked for ${String(to || "").trim() || "(empty)"}: ${
        target.logMessage || target.error
      }`,
    );
    return res.status(target.statusCode || 400).json({
      success: false,
      error: target.error,
      details: target.details || "",
      scope: session.scopeKey,
    });
  }

  const chatId = target.chatId;
  try {
    const sentIds = [];
    let captionUsed = false;

    if (mediaItems.length > 0) {
      for (let index = 0; index < mediaItems.length; index += 1) {
        const media = mediaItems[index];
        const options = index === 0 && String(message || "").trim() ? { caption: message } : {};
        const result = await session.client.sendMessage(chatId, media, options);
        sentIds.push(result.id.id);
        if (index === 0 && options.caption) captionUsed = true;
      }
    }

    if (String(message || "").trim() && !captionUsed) {
      const textResult = await session.client.sendMessage(chatId, message);
      sentIds.push(textResult.id.id);
    }

    console.log(`[${session.scopeKey}] Sent outbound WhatsApp message to ${target.phoneDigits || to}`);
    res.json({
      success: true,
      messageId: sentIds[0] || "",
      messageIds: sentIds,
      scope: session.scopeKey,
    });
  } catch (err) {
    const formatted = formatBridgeError(err);
    console.error(
      `[${session.scopeKey}] Send failed to ${target.phoneDigits || to}: ${formatted.logMessage}`,
    );
    res.status(500).json({
      success: false,
      error: formatted.clientMessage,
      scope: session.scopeKey,
    });
  }
});

app.get("/check/:phone", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  ensureSessionInitialized(session).catch(() => {});
  const ready = await waitForReady(session, 8000);
  if (!ready) {
    return res.status(503).json({
      error: "WhatsApp session is not ready for this account.",
      scope: session.scopeKey,
      status: sessionStatus(session),
    });
  }
  try {
    const hasWhatsApp = await session.client.isRegisteredUser(
      `${String(req.params.phone || "").replace(/\D/g, "")}@c.us`,
    );
    res.json({ phone: req.params.phone, has_whatsapp: hasWhatsApp, scope: session.scopeKey });
  } catch (err) {
    res.status(500).json({ error: err.message, scope: session.scopeKey });
  }
});

const bridgePortNumber = Number.parseInt(String(BRIDGE_PORT), 10);
const bridgePort = Number.isFinite(bridgePortNumber) ? bridgePortNumber : 3001;

const bridgeServer = app.listen(bridgePort, () => {
  console.log(`WhatsApp bridge HTTP listening on port ${bridgePort}`);
});

bridgeServer.on("error", (err) => {
  if (err && err.code === "EADDRINUSE") {
    console.error(`Bridge port ${bridgePort} is already in use.`);
    process.exit(1);
    return;
  }
  console.error(`Bridge failed to start: ${err.message || err}`);
  process.exit(1);
});

console.log("Starting scoped WhatsApp bridge...");
removeChromiumSingletonLocks(WWEBJS_AUTH_DIR);
restoreSavedSessions().catch((err) => {
  console.error(`Failed to restore saved WhatsApp sessions: ${err.message || err}`);
});
