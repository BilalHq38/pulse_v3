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
const { parsePhoneNumberFromString } = require("libphonenumber-js");

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

function readEnvValue(name, aliases = []) {
  const names = [name, ...aliases];
  for (const candidate of names) {
    const value = String(process.env[candidate] || "").trim();
    if (value) {
      return value;
    }
  }
  return "";
}

function requiredEnv(name, aliases = []) {
  const value = readEnvValue(name, aliases);
  return { name, value, aliases };
}

function readEnvInt(name, defaultValue, aliases = []) {
  const raw = readEnvValue(name, aliases);
  const parsed = Number.parseInt(String(raw || defaultValue), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : defaultValue;
}

function validateRequiredBridgeEnv() {
  const requirements = [
    requiredEnv("BRIDGE_PORT"),
    requiredEnv("PYTHON_BACKEND"),
    requiredEnv("WHATSAPP_WEBHOOK_PATH"),
    requiredEnv("WHATSAPP_BRIDGE_SECRET", ["BRIDGE_SECRET"]),
    requiredEnv("WHATSAPP_PHONE_NUMBER_ID", ["PHONE_NUMBER_ID"]),
    requiredEnv("WHATSAPP_BUSINESS_ACCOUNT_ID"),
    requiredEnv("WHATSAPP_WEBHOOK_SECRET", ["META_WEBHOOK_SECRET", "BRIDGE_WEBHOOK_SECRET"]),
  ];
  const missing = requirements.filter((item) => !item.value);
  const errors = [];
  if (missing.length > 0) {
    for (const item of missing) {
      const aliasText = item.aliases.length > 0 ? ` (aliases: ${item.aliases.join(", ")})` : "";
      errors.push(`- ${item.name}${aliasText}`);
    }
  }
  const bridgePort = Number.parseInt(String(readEnvValue("BRIDGE_PORT")), 10);
  if (!Number.isFinite(bridgePort) || bridgePort <= 0) {
    errors.push("- BRIDGE_PORT must be a positive integer");
  }
  try {
    // eslint-disable-next-line no-new
    new URL(readEnvValue("PYTHON_BACKEND"));
  } catch {
    errors.push("- PYTHON_BACKEND must be a valid absolute URL");
  }
  const webhookPath = readEnvValue("WHATSAPP_WEBHOOK_PATH");
  if (webhookPath && !webhookPath.startsWith("/")) {
    errors.push("- WHATSAPP_WEBHOOK_PATH must start with '/'");
  }
  if (errors.length > 0) {
    console.error("Bridge startup validation failed. Missing or invalid environment variables:");
    for (const error of errors) {
      console.error(error);
    }
    process.exit(1);
  }
}

validateRequiredBridgeEnv();

const BRIDGE_PORT = readEnvValue("BRIDGE_PORT");
const PYTHON_BACKEND = readEnvValue("PYTHON_BACKEND");
const WHATSAPP_WEBHOOK_PATH = readEnvValue("WHATSAPP_WEBHOOK_PATH");
const BRIDGE_SECRET = readEnvValue("WHATSAPP_BRIDGE_SECRET", ["BRIDGE_SECRET"]);
const DEFAULT_BRIDGE_COMPANY_ID = (process.env.BRIDGE_COMPANY_ID || "").trim();
const WHATSAPP_PHONE_NUMBER_ID = readEnvValue("WHATSAPP_PHONE_NUMBER_ID", ["PHONE_NUMBER_ID"]);
const WHATSAPP_BUSINESS_ACCOUNT_ID = readEnvValue("WHATSAPP_BUSINESS_ACCOUNT_ID");
const WEBHOOK_SIGNING_SECRET = readEnvValue("WHATSAPP_WEBHOOK_SECRET", ["META_WEBHOOK_SECRET", "BRIDGE_WEBHOOK_SECRET"]);
const MY_NUMBER = process.env.MY_WHATSAPP_NUMBER || "";
const BRIDGE_FORWARD_TIMEOUT_MS = readEnvInt("BRIDGE_FORWARD_TIMEOUT_MS", 10000);
const BRIDGE_READY_TIMEOUT_MS = readEnvInt("BRIDGE_READY_TIMEOUT_MS", 60000);
const BRIDGE_CHECK_READY_TIMEOUT_MS = readEnvInt("BRIDGE_CHECK_READY_TIMEOUT_MS", 60000);
const BRIDGE_JSON_LIMIT = readEnvValue("BRIDGE_JSON_LIMIT") || "10mb";
const WWEBJS_AUTH_TIMEOUT_MS = readEnvInt("WWEBJS_AUTH_TIMEOUT_MS", 180000);
const WWEBJS_READY_AFTER_INIT_TIMEOUT_MS = readEnvInt("WWEBJS_READY_AFTER_INIT_TIMEOUT_MS", 60000);
const WWEBJS_SESSION_INIT_DELAY_MS = readEnvInt("WWEBJS_SESSION_INIT_DELAY_MS", 2000);
const PUPPETEER_PROTOCOL_TIMEOUT_MS = readEnvInt("PUPPETEER_PROTOCOL_TIMEOUT_MS", 180000);
const PUPPETEER_NAVIGATION_TIMEOUT_MS = readEnvInt("PUPPETEER_NAVIGATION_TIMEOUT_MS", 180000);
const WHATSAPP_SEND_RETRY_ATTEMPTS = readEnvInt("WHATSAPP_SEND_RETRY_ATTEMPTS", 3);
const WHATSAPP_SEND_RETRY_BASE_DELAY_MS = readEnvInt("WHATSAPP_SEND_RETRY_BASE_DELAY_MS", 800);
const WHATSAPP_SEND_RETRY_MAX_DELAY_MS = readEnvInt("WHATSAPP_SEND_RETRY_MAX_DELAY_MS", 5000);

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
let sessionInitQueue = Promise.resolve();
let lastSessionInitStartedAt = 0;

// #region agent log
const _DEBUG_LOG = path.join(__dirname, "../../debug-bc3f0b.log");
function _dbgLog(hypothesisId, location, message, data) {
  try {
    const line = JSON.stringify({
      sessionId: "bc3f0b",
      runId: process.env.DEBUG_RUN_ID || "pre",
      hypothesisId,
      location,
      message,
      data: data || {},
      timestamp: Date.now(),
    });
    fs.appendFileSync(_DEBUG_LOG, `${line}\n`);
  } catch (_e) {
    /* ignore */
  }
}
// #endregion

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function logBridgeEvent(level, event, data = {}) {
  const payload = {
    ts: new Date().toISOString(),
    level,
    event,
    ...data,
  };
  const line = JSON.stringify(payload);
  if (level === "error") {
    console.error(line);
  } else if (level === "warn") {
    console.warn(line);
  } else {
    console.log(line);
  }
}

function maskPhone(value) {
  const digits = String(value || "").replace(/\D/g, "");
  if (!digits) return "";
  if (digits.length <= 4) return `***${digits}`;
  return `${"*".repeat(Math.max(3, digits.length - 4))}${digits.slice(-4)}`;
}

function extractMessageId(result) {
  if (!result || !result.id) return "";
  if (typeof result.id === "string") return result.id;
  return result.id.id || result.id._serialized || "";
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

function getFallbackRegion() {
  const r = String(process.env.WHATSAPP_DEFAULT_COUNTRY || "")
    .trim()
    .toUpperCase();
  if (r.length === 2 && /^[A-Z]{2}$/.test(r)) {
    return r;
  }
  return undefined;
}

function normalizePhoneNumber(value, fallbackCountry) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  const fb = fallbackCountry !== undefined ? fallbackCountry : getFallbackRegion();

  try {
    if (raw.startsWith("+")) {
      const parsed = parsePhoneNumberFromString(raw);
      if (parsed && typeof parsed.isValid === "function" && parsed.isValid()) {
        return String(parsed.format("E.164")).replace(/^\+/, "");
      }
    }

    const digits = raw.replace(/\D/g, "");
    if (!raw.startsWith("+") && digits.length >= 8) {
      const intl = parsePhoneNumberFromString(`+${digits}`);
      if (intl && intl.isValid && intl.isValid()) {
        return String(intl.format("E.164")).replace(/^\+/, "");
      }
    }

    if (fb) {
      const parsed = parsePhoneNumberFromString(raw, fb);
      if (parsed && parsed.isValid && parsed.isValid()) {
        return String(parsed.format("E.164")).replace(/^\+/, "");
      }
    }
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    console.warn(`[normalizePhoneNumber] Parse error for ${raw}: ${msg}`);
  }

  const loose = raw.replace(/\D/g, "");
  console.warn(
    `[normalizePhoneNumber] Could not parse ${raw} as valid international number, using raw digits`,
  );
  return loose;
}

function isNoLidErrorText(value) {
  return /no lid for user/i.test(String(value || ""));
}

function isTransientPuppeteerError(err) {
  const formatted = formatBridgeError(err);
  return /runtime\.callfunctionon timed out|protocolerror|target closed|session closed|execution context|navigation timeout|timeout|browser has disconnected|context destroyed/i.test(
    formatted.logMessage,
  );
}

function retryDelayMs(attempt) {
  const base = Math.max(100, WHATSAPP_SEND_RETRY_BASE_DELAY_MS);
  const max = Math.max(base, WHATSAPP_SEND_RETRY_MAX_DELAY_MS);
  return Math.min(max, base * (2 ** Math.max(0, attempt - 1)));
}

async function withTimeout(promise, timeoutMs, label) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function ensureRegisteredChatTarget(client, phoneDigits, directChatId, logContext = "") {
  if (typeof client.isRegisteredUser !== "function") {
    return { ok: true, chatId: directChatId, phoneDigits };
  }
  try {
    const hasWhatsApp = await client.isRegisteredUser(directChatId);
    if (hasWhatsApp) {
      return { ok: true, chatId: directChatId, phoneDigits };
    }
    return {
      ok: false,
      statusCode: 404,
      error: `Phone number ${phoneDigits} is not registered on WhatsApp.`,
      phoneDigits,
      logMessage: logContext,
    };
  } catch (err) {
    const formatted = formatBridgeError(err);
    return {
      ok: false,
      statusCode: 502,
      error:
        "WhatsApp could not resolve this recipient. Verify the full international phone number and retry.",
      details: formatted.clientMessage,
      phoneDigits,
      logMessage: [logContext, formatted.logMessage].filter(Boolean).join(" | "),
    };
  }
}

function serializeMediaForBrowser(media) {
  if (!media || typeof media !== "object") return null;
  const mimetype = String(media.mimetype || media.mimeType || "").trim();
  const data = String(media.data || "").trim();
  if (!mimetype || !data) return null;
  return {
    mimetype,
    data,
    filename: String(media.filename || media.name || "").trim(),
  };
}

async function sendMessageViaBrowserFallback(client, chatId, content, options = {}) {
  const media = serializeMediaForBrowser(content);
  return client.pupPage.evaluate(
    async ({ chatId: targetChatId, content: targetContent, options: targetOptions, media: targetMedia }) => {
      const chatWid = window.Store.WidFactory.createWid(targetChatId);
      let chat = window.Store.Chat.get(chatWid) || null;

      if (!chat && window.Store.Chat && typeof window.Store.Chat.find === "function") {
        try {
          chat = await window.Store.Chat.find(chatWid);
        } catch {
          chat = null;
        }
      }

      if (
        !chat &&
        window.Store.FindOrCreateChat &&
        typeof window.Store.FindOrCreateChat.findOrCreateLatestChat === "function"
      ) {
        try {
          chat = (await window.Store.FindOrCreateChat.findOrCreateLatestChat(chatWid))?.chat || null;
        } catch {
          chat = null;
        }
      }

      if (!chat) {
        return null;
      }

      const sendOptions = { ...(targetOptions || {}) };
      let sendContent = targetContent;

      if (targetMedia) {
        sendOptions.media = targetMedia;
        sendContent = "";
      }

      const sent = await window.WWebJS.sendMessage(chat, sendContent, sendOptions);
      return sent ? window.WWebJS.getMessageModel(sent) : null;
    },
    {
      chatId,
      content: typeof content === "string" ? content : String(content || ""),
      options: { ...(options || {}) },
      media,
    },
  );
}

async function sendMessageWithFallback(client, chatId, content, options = {}) {
  try {
    return await client.sendMessage(chatId, content, options);
  } catch (err) {
    const formatted = formatBridgeError(err);
    if (!isNoLidErrorText(formatted.logMessage)) {
      throw err;
    }
    console.warn(`[bridge] sendMessage hit No LID for user on ${chatId}; retrying with Chat.find fallback`);
    const fallbackResult = await sendMessageViaBrowserFallback(client, chatId, content, options);
    if (!fallbackResult) {
      throw new Error(
        "WhatsApp chat could not be resolved for this recipient. Verify the contact number includes country code.",
      );
    }
    return fallbackResult;
  }
}

async function sendMessageWithRetry(session, chatId, content, options = {}, context = {}) {
  const attempts = Math.max(1, WHATSAPP_SEND_RETRY_ATTEMPTS);
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      if (attempt > 1) {
        logBridgeEvent("info", "whatsapp.outgoing.retry", {
          scope: session.scopeKey,
          attempt,
          attempts,
          target: maskPhone(context.phoneDigits || chatId),
          message_type: context.messageType || "text",
        });
      }
      return await sendMessageWithFallback(session.client, chatId, content, options);
    } catch (err) {
      lastError = err;
      const formatted = formatBridgeError(err);
      const transient = isTransientPuppeteerError(err);
      logBridgeEvent(attempt >= attempts || !transient ? "error" : "warn", "whatsapp.outgoing.send_attempt_failed", {
        scope: session.scopeKey,
        attempt,
        attempts,
        transient,
        target: maskPhone(context.phoneDigits || chatId),
        message_type: context.messageType || "text",
        error: formatted.clientMessage,
      });

      if (!transient || attempt >= attempts) {
        throw err;
      }
      await sleep(retryDelayMs(attempt));
    }
  }

  throw lastError || new Error("Failed to send WhatsApp message");
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

  const directChatId = `${phoneDigits}@c.us`;

  if (typeof client.getNumberId === "function") {
    try {
      const resolved = await client.getNumberId(phoneDigits);
      if (resolved && typeof resolved === "object" && resolved._serialized) {
        return { ok: true, chatId: resolved._serialized, phoneDigits };
      }
      if (typeof resolved === "string" && resolved.trim()) {
        return { ok: true, chatId: resolved.trim(), phoneDigits };
      }
      return ensureRegisteredChatTarget(
        client,
        phoneDigits,
        directChatId,
        `[resolveChatTarget] getNumberId returned empty for ${phoneDigits}`,
      );
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      const fallbackResult = await ensureRegisteredChatTarget(
        client,
        phoneDigits,
        directChatId,
        `[resolveChatTarget] getNumberId failed for ${phoneDigits}: ${msg}`,
      );
      if (fallbackResult.ok) {
        return fallbackResult;
      }
      if (isNoLidErrorText(msg)) {
        fallbackResult.error =
          "WhatsApp could not resolve this recipient. Save the number with its country code and retry.";
      }
      return fallbackResult;
    }
  }

  return ensureRegisteredChatTarget(client, phoneDigits, directChatId);
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
  if (isSessionReady(session)) return "ready";
  if (session.lastQrString) return "need_qr";
  if (session.initPromise) return "initializing";
  if (session.lastInitError) return "disconnected";
  if (session.isAuthenticated) return "initializing";
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

  logBridgeEvent("info", "whatsapp.incoming.received", {
    scope: session.scopeKey,
    company_id: session.companyId || "",
    user_id: session.userId || "",
    message_id: msg && msg.id ? msg.id.id || msg.id._serialized || "" : "",
    from: maskPhone(senderPhone),
    has_media: isImageMessage,
    body_length: String(msg.body || "").length,
  });

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
      logBridgeEvent("error", "whatsapp.incoming.media_download_failed", {
        scope: session.scopeKey,
        message_id: msg && msg.id ? msg.id.id || msg.id._serialized || "" : "",
        error: trimText(err && err.message ? err.message : err, 400),
      });
      console.error(`[${session.scopeKey}] Media download failed: ${err.message}`);
    }
  }

  try {
    const metadata = {
      phone_number_id: WHATSAPP_PHONE_NUMBER_ID,
      bridge_scope: session.scopeKey,
    };
    if (session.phone || MY_NUMBER) {
      metadata.display_phone_number = session.phone || MY_NUMBER;
    }
    if (session.companyId || DEFAULT_BRIDGE_COMPANY_ID) {
      metadata.company_id = session.companyId || DEFAULT_BRIDGE_COMPANY_ID;
    }
    if (session.userId) {
      metadata.bridge_user_id = session.userId;
    }

    const messagePayload = {
      from: senderPhone,
      type: imagePayload ? "image" : "text",
      text: { body: msg.body || "" },
      timestamp: Math.floor(Date.now() / 1000),
      id: msg.id.id,
    };
    if (imagePayload) {
      messagePayload.image = imagePayload;
    }

    const payload = {
      entry: [{
        id: WHATSAPP_BUSINESS_ACCOUNT_ID,
        changes: [{
          value: {
            business_account_id: WHATSAPP_BUSINESS_ACCOUNT_ID,
            metadata,
            messages: [messagePayload],
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
      timeout: BRIDGE_FORWARD_TIMEOUT_MS,
    });
    logBridgeEvent("info", "whatsapp.incoming.forwarded", {
      scope: session.scopeKey,
      message_id: messagePayload.id || "",
      from: maskPhone(senderPhone),
      type: messagePayload.type,
    });
    console.log(`[${session.scopeKey}] Forwarded inbound message to Python backend`);
  } catch (err) {
    logBridgeEvent("error", "whatsapp.incoming.forward_failed", {
      scope: session.scopeKey,
      from: maskPhone(senderPhone),
      error: trimText(err && err.message ? err.message : err, 500),
    });
    console.error(`[${session.scopeKey}] Forward failed: ${err.message}`);
  }
}

function buildClient(session) {
  const chromeExecutablePath = readEnvValue("CHROME_BIN", ["PUPPETEER_EXECUTABLE_PATH"]);
  const puppeteerConfig = {
    headless: true,
    protocolTimeout: PUPPETEER_PROTOCOL_TIMEOUT_MS,
    timeout: PUPPETEER_NAVIGATION_TIMEOUT_MS,
    defaultViewport: { width: 1280, height: 900 },
    handleSIGINT: false,
    handleSIGTERM: false,
    handleSIGHUP: false,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--disable-extensions",
      "--disable-background-timer-throttling",
      "--disable-backgrounding-occluded-windows",
      "--disable-renderer-backgrounding",
      "--disable-features=site-per-process,Translate,BackForwardCache",
      "--disable-hang-monitor",
      "--disable-ipc-flooding-protection",
      "--disable-popup-blocking",
      "--disable-sync",
      "--metrics-recording-only",
      "--no-first-run",
      "--no-default-browser-check",
      "--window-size=1280,900",
      // Prevent Chromium from OOM-killing the renderer under memory pressure,
      // which causes TargetCloseError during whatsapp-web.js script injection.
      "--memory-pressure-off",
      "--disable-features=MemoryPressureBasedSourceBufferGC",
    ],
  };
  if (chromeExecutablePath) {
    puppeteerConfig.executablePath = chromeExecutablePath;
  }
  // #region agent log
  _dbgLog("H1", "bridge.js:buildClient", "puppeteer config", {
    scopeKey: session.scopeKey,
    hasChromeBin: Boolean(chromeExecutablePath),
    chromeBasename: chromeExecutablePath
      ? path.basename(String(chromeExecutablePath).split("?")[0])
      : "",
    headless: puppeteerConfig.headless,
    argCount: (puppeteerConfig.args && puppeteerConfig.args.length) || 0,
    protocolTimeout: puppeteerConfig.protocolTimeout,
    navigationTimeout: puppeteerConfig.timeout,
  });
  // #endregion
  return new Client({
    authStrategy: new LocalAuth({
      dataPath: WWEBJS_AUTH_DIR,
      clientId: session.scopeKey,
    }),
    authTimeoutMs: WWEBJS_AUTH_TIMEOUT_MS,
    puppeteer: puppeteerConfig,
  });
}

function attachClientHandlers(session) {
  const { client } = session;

  client.on("qr", (qr) => {
    session.lastQrString = String(qr || "");
    session.isReady = false;
    session.isAuthenticated = false;
    persistSessionMetadata(session);
    logBridgeEvent("info", "whatsapp.session.qr_required", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
    });
    console.log(`\n[${session.scopeKey}] Scan this QR code with WhatsApp:`);
    qrcode.generate(qr, { small: true });
  });

  client.on("authenticated", () => {
    session.lastQrString = "";
    session.isAuthenticated = true;
    persistSessionMetadata(session);
    logBridgeEvent("info", "whatsapp.session.authenticated", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
    });
    console.log(`[${session.scopeKey}] WhatsApp authenticated`);
  });

  client.on("auth_failure", (message) => {
    session.isReady = false;
    session.isAuthenticated = false;
    session.lastQrString = "";
    session.lastInitError = String(message || "Authentication failed");
    persistSessionMetadata(session);
    logBridgeEvent("error", "whatsapp.session.auth_failed", {
      scope: session.scopeKey,
      error: trimText(message, 400),
    });
    console.error(`[${session.scopeKey}] Auth failed: ${message}`);
  });

  client.on("ready", () => {
    session.isReady = true;
    session.isAuthenticated = true;
    session.lastReadyAt = Date.now();
    session.lastInitError = "";
    session.lastQrString = "";
    session.phone = String(
      (client.info && client.info.wid && client.info.wid.user) || MY_NUMBER || "",
    ).trim();
    persistSessionMetadata(session);
    // #region agent log
    _dbgLog("H4", "bridge.js:ready", "client ready", { scopeKey: session.scopeKey });
    // #endregion
    logBridgeEvent("info", "whatsapp.session.ready", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      phone: maskPhone(session.phone),
    });
    console.log(
      `[${session.scopeKey}] READY company=${session.companyId || "-"} user=${session.userId || "-"} phone=${session.phone || "-"}`,
    );
  });

  client.on("disconnected", (reason) => {
    session.isReady = false;
    session.isAuthenticated = false;
    session.lastQrString = "";
    session.phone = "";
    persistSessionMetadata(session);
    logBridgeEvent("warn", "whatsapp.session.disconnected", {
      scope: session.scopeKey,
      reason: trimText(reason, 300),
    });
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
    isAuthenticated: false,
    initPromise: null,
    lastInitError: "",
    lastReadyAt: 0,
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
  if (!session || isSessionReady(session)) return;
  if (session.initPromise) {
    // #region agent log
    _dbgLog("H2", "bridge.js:ensureSessionInitialized", "awaiting existing initPromise", {
      scopeKey: session.scopeKey,
    });
    // #endregion
    return session.initPromise;
  }

  const queued = sessionInitQueue.then(() => initializeSessionSequentially(session));
  sessionInitQueue = queued.catch(() => {});
  session.initPromise = queued.finally(() => {
    session.initPromise = null;
  });
  return session.initPromise;
}

function isSessionReady(session) {
  if (!session || !session.isReady || !session.client) return false;
  const page = session.client.pupPage;
  if (page && typeof page.isClosed === "function" && page.isClosed()) return false;
  return true;
}

async function waitForInitSlot(session) {
  const elapsed = Date.now() - lastSessionInitStartedAt;
  const delayMs = Math.max(0, WWEBJS_SESSION_INIT_DELAY_MS - elapsed);
  if (delayMs > 0) {
    logBridgeEvent("info", "whatsapp.session.init_delayed", {
      scope: session.scopeKey,
      delay_ms: delayMs,
    });
    await sleep(delayMs);
  }
  lastSessionInitStartedAt = Date.now();
}

async function configurePuppeteerPage(session) {
  const page = session && session.client && session.client.pupPage;
  if (!page) return;
  try {
    if (typeof page.setDefaultTimeout === "function") {
      page.setDefaultTimeout(PUPPETEER_PROTOCOL_TIMEOUT_MS);
    }
    if (typeof page.setDefaultNavigationTimeout === "function") {
      page.setDefaultNavigationTimeout(PUPPETEER_NAVIGATION_TIMEOUT_MS);
    }
  } catch (err) {
    logBridgeEvent("warn", "whatsapp.session.page_timeout_config_failed", {
      scope: session.scopeKey,
      error: trimText(err && err.message ? err.message : err, 300),
    });
  }
}

function rebuildSessionClient(session) {
  if (!session) return;
  try {
    if (session.client) {
      session.client.removeAllListeners();
    }
  } catch {
    /* ignore */
  }
  session.isReady = false;
  session.isAuthenticated = false;
  session.lastQrString = "";
  session.client = buildClient(session);
  attachClientHandlers(session);
}

async function waitForReadyOrQr(session, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isSessionReady(session)) return "ready";
    if (session.lastQrString) return "qr";
    if (session.lastInitError) return "failed";
    await sleep(500);
  }
  return isSessionReady(session) ? "ready" : "timeout";
}

async function initializeSessionSequentially(session) {
  if (!session || isSessionReady(session)) return;
  await waitForInitSlot(session);

  // #region agent log
  _dbgLog("H2", "bridge.js:ensureSessionInitialized", "start init", {
    scopeKey: session.scopeKey,
    isReady: session.isReady,
  });
  // #endregion

  removeChromiumSingletonLocks(sessionAuthDir(session.scopeKey));
  session.lastInitError = "";
  logBridgeEvent("info", "whatsapp.session.init_start", {
    scope: session.scopeKey,
    company_id: session.companyId || "",
    user_id: session.userId || "",
  });

  // Allow up to 3 attempts: the first crash is almost always a transient
  // TargetCloseError during WhatsApp Web script injection (OOM / slow start).
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    session.lastInitError = "";
    try {
      await withTimeout(
        session.client.initialize(),
        WWEBJS_AUTH_TIMEOUT_MS,
        `WhatsApp client initialize for ${session.scopeKey}`,
      );
      await configurePuppeteerPage(session);

      const readyState = await waitForReadyOrQr(session, WWEBJS_READY_AFTER_INIT_TIMEOUT_MS);
      if (readyState === "ready") {
        logBridgeEvent("info", "whatsapp.session.init_ready", {
          scope: session.scopeKey,
          attempt,
        });
        return;
      }
      if (readyState === "qr") {
        logBridgeEvent("info", "whatsapp.session.init_waiting_for_qr", {
          scope: session.scopeKey,
          attempt,
        });
        return;
      }
      throw new Error(`WhatsApp session did not reach ready state (${readyState})`);
    } catch (err) {
      const formatted = formatBridgeError(err);
      session.isReady = false;
      session.lastInitError = formatted.clientMessage;

      // #region agent log
      _dbgLog("H1", "bridge.js:ensureSessionInitialized", "initialize error", {
        scopeKey: session.scopeKey,
        name: err && err.name,
        message: err && err.message,
        code: err && err.code,
        cause:
          err && err.cause && (err.cause.message || String(err.cause).slice(0, 200)),
        stackLine:
          err && err.stack && String(err.stack).split("\n").slice(0, 4).join(" | "),
      });
      _dbgLog("H4", "bridge.js:ensureSessionInitialized", "initialize error (cdp layer)", {
        scopeKey: session.scopeKey,
        name: err && err.name,
        message: err && err.message,
      });
      // #endregion

      logBridgeEvent("error", "whatsapp.session.init_failed", {
        scope: session.scopeKey,
        attempt,
        transient: isTransientPuppeteerError(err),
        error: formatted.clientMessage,
      });
      console.error(`[${session.scopeKey}] Initialize failed: ${err.message || err}`);

      if (attempt >= 3 || !isTransientPuppeteerError(err)) {
        throw err;
      }

      try {
        await session.client.destroy();
      } catch {
        /* ignore cleanup failure */
      }
      removeChromiumSingletonLocks(sessionAuthDir(session.scopeKey));
      rebuildSessionClient(session);
      session.lastInitError = "";
      await sleep(retryDelayMs(attempt));
    }
  }
}

async function waitForReady(session, timeoutMs = BRIDGE_READY_TIMEOUT_MS) {
  if (!session) return false;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isSessionReady(session)) return true;
    if (session.lastQrString) return false;
    if (session.lastInitError && !session.initPromise) return false;
    await sleep(500);
  }
  return isSessionReady(session);
}

async function destroySession(session) {
  if (!session) return;
  // #region agent log
  _dbgLog("H5", "bridge.js:destroySession", "destroy start", { scopeKey: session.scopeKey });
  // #endregion
  try {
    if (session.client) await session.client.destroy();
  } catch (err) {
    console.warn(`[${session.scopeKey}] Destroy failed: ${err.message || err}`);
  }
  sessions.delete(session.scopeKey);
}

async function restoreSavedSessions() {
  const savedScopes = loadSavedScopes();
  // #region agent log
  _dbgLog("H2", "bridge.js:restoreSavedSessions", "scopes to restore", {
    count: savedScopes.length,
  });
  // #endregion
  for (const [index, scope] of savedScopes.entries()) {
    const session = createSession(scope);
    try {
      await ensureSessionInitialized(session);
    } catch (err) {
      const formatted = formatBridgeError(err);
      logBridgeEvent("error", "whatsapp.session.restore_failed", {
        scope: session.scopeKey,
        error: formatted.clientMessage,
      });
    }
    if (index < savedScopes.length - 1) {
      await sleep(WWEBJS_SESSION_INIT_DELAY_MS);
    }
  }
}

const app = express();
app.use(express.json({ limit: BRIDGE_JSON_LIMIT }));

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
  logBridgeEvent("info", "whatsapp.outgoing.request", {
    scope: session.scopeKey,
    company_id: session.companyId || "",
    user_id: session.userId || "",
    target: maskPhone(to),
    body_length: String(message || "").length,
    attachment_count: mediaItems.length,
    status: sessionStatus(session),
  });
  const ready = await waitForReady(session, BRIDGE_READY_TIMEOUT_MS);
  if (!ready) {
    logBridgeEvent("warn", "whatsapp.outgoing.not_ready", {
      scope: session.scopeKey,
      target: maskPhone(to),
      status: sessionStatus(session),
      last_error: session.lastInitError || "",
    });
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
    logBridgeEvent("warn", "whatsapp.outgoing.target_blocked", {
      scope: session.scopeKey,
      target: maskPhone(to),
      error: target.error,
      details: target.details || "",
    });
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
        const result = await sendMessageWithRetry(session, chatId, media, options, {
          phoneDigits: target.phoneDigits,
          messageType: "media",
        });
        sentIds.push(extractMessageId(result));
        if (index === 0 && options.caption) captionUsed = true;
      }
    }

    if (String(message || "").trim() && !captionUsed) {
      const textResult = await sendMessageWithRetry(session, chatId, message, {}, {
        phoneDigits: target.phoneDigits,
        messageType: "text",
      });
      sentIds.push(extractMessageId(textResult));
    }

    logBridgeEvent("info", "whatsapp.outgoing.sent", {
      scope: session.scopeKey,
      target: maskPhone(target.phoneDigits || to),
      message_count: sentIds.filter(Boolean).length,
      message_ids: sentIds.filter(Boolean),
    });
    console.log(`[${session.scopeKey}] Sent outbound WhatsApp message to ${target.phoneDigits || to}`);
    res.json({
      success: true,
      messageId: sentIds[0] || "",
      messageIds: sentIds,
      scope: session.scopeKey,
    });
  } catch (err) {
    const formatted = formatBridgeError(err);
    logBridgeEvent("error", "whatsapp.outgoing.failed", {
      scope: session.scopeKey,
      target: maskPhone(target.phoneDigits || to),
      error: formatted.clientMessage,
    });
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
  const ready = await waitForReady(session, BRIDGE_CHECK_READY_TIMEOUT_MS);
  if (!ready) {
    logBridgeEvent("warn", "whatsapp.check.not_ready", {
      scope: session.scopeKey,
      target: maskPhone(req.params.phone || ""),
      status: sessionStatus(session),
      last_error: session.lastInitError || "",
    });
    return res.status(503).json({
      error: "WhatsApp session is not ready for this account.",
      scope: session.scopeKey,
      status: sessionStatus(session),
    });
  }
  try {
    const checkDigits = normalizePhoneNumber(req.params.phone || "");
    if (!checkDigits) {
      return res.status(400).json({ error: "Phone number is missing or invalid.", scope: session.scopeKey });
    }
    const hasWhatsApp = await session.client.isRegisteredUser(`${checkDigits}@c.us`);
    logBridgeEvent("info", "whatsapp.check.completed", {
      scope: session.scopeKey,
      target: maskPhone(checkDigits),
      has_whatsapp: Boolean(hasWhatsApp),
    });
    res.json({ phone: req.params.phone, has_whatsapp: hasWhatsApp, scope: session.scopeKey });
  } catch (err) {
    logBridgeEvent("error", "whatsapp.check.failed", {
      scope: session.scopeKey,
      target: maskPhone(req.params.phone || ""),
      error: trimText(err && err.message ? err.message : err, 400),
    });
    res.status(500).json({ error: err.message, scope: session.scopeKey });
  }
});

const bridgePort = Number.parseInt(String(BRIDGE_PORT), 10);

const bridgeServer = app.listen(bridgePort, () => {
  // #region agent log
  const mu = process.memoryUsage();
  _dbgLog("H3", "bridge.js:listen", "bridge listening (heap/rss)", {
    heapMb: Math.round(mu.heapUsed / 1024 / 1024),
    rssMb: Math.round(mu.rss / 1024 / 1024),
  });
  // #endregion
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