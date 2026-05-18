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
const {
  SESSION_STATES,
  connectedForState,
  messageForState,
  progressForState,
} = require("./session_state");
const {
  normalizeInboundPhone,
  resolveInboundSenderIdentity,
  resolveOutboundRecipientIdentity,
  stringifyIdentityValue,
} = require("./inbound_identity");

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
const BRIDGE_READY_TIMEOUT_MS = readEnvInt("BRIDGE_READY_TIMEOUT_MS", 180000);
const BRIDGE_CHECK_READY_TIMEOUT_MS = readEnvInt("BRIDGE_CHECK_READY_TIMEOUT_MS", 180000);
const BRIDGE_JSON_LIMIT = readEnvValue("BRIDGE_JSON_LIMIT") || "10mb";
const WWEBJS_AUTH_TIMEOUT_MS = readEnvInt("WWEBJS_AUTH_TIMEOUT_MS", 180000);
const WWEBJS_READY_AFTER_INIT_TIMEOUT_MS = readEnvInt("WWEBJS_READY_AFTER_INIT_TIMEOUT_MS", 180000);
const WWEBJS_SESSION_INIT_DELAY_MS = readEnvInt("WWEBJS_SESSION_INIT_DELAY_MS", 2000);
const WWEBJS_INIT_MAX_ATTEMPTS = readEnvInt("WWEBJS_INIT_MAX_ATTEMPTS", 3);
const WWEBJS_INIT_RETRY_BASE_DELAY_MS = readEnvInt("WWEBJS_INIT_RETRY_BASE_DELAY_MS", 3000);
const WWEBJS_INIT_RETRY_MAX_DELAY_MS = readEnvInt("WWEBJS_INIT_RETRY_MAX_DELAY_MS", 20000);
const PUPPETEER_PROTOCOL_TIMEOUT_MS = readEnvInt("PUPPETEER_PROTOCOL_TIMEOUT_MS", 180000);
const PUPPETEER_NAVIGATION_TIMEOUT_MS = readEnvInt("PUPPETEER_NAVIGATION_TIMEOUT_MS", 180000);
const WHATSAPP_SEND_RETRY_ATTEMPTS = readEnvInt("WHATSAPP_SEND_RETRY_ATTEMPTS", 3);
const WHATSAPP_SEND_RETRY_BASE_DELAY_MS = readEnvInt("WHATSAPP_SEND_RETRY_BASE_DELAY_MS", 800);
const WHATSAPP_SEND_RETRY_MAX_DELAY_MS = readEnvInt("WHATSAPP_SEND_RETRY_MAX_DELAY_MS", 5000);
const WHATSAPP_HISTORY_DEFAULT_LIMIT = readEnvInt("WHATSAPP_HISTORY_DEFAULT_LIMIT", 50);
const WHATSAPP_HISTORY_MAX_LIMIT = readEnvInt("WHATSAPP_HISTORY_MAX_LIMIT", 100);
const PLATFORM_OUTBOUND_ECHO_TTL_MS = readEnvInt("WHATSAPP_PLATFORM_ECHO_TTL_MS", 5000);
const PLATFORM_OUTBOUND_ECHO_BUCKET_MS = readEnvInt("WHATSAPP_PLATFORM_ECHO_BUCKET_MS", 3000);
const MOBILE_OUTBOUND_BACKFILL_INTERVAL_MS = readEnvInt("WHATSAPP_MOBILE_OUTBOUND_BACKFILL_INTERVAL_MS", 15000);
const MOBILE_OUTBOUND_BACKFILL_LOOKBACK_MS = readEnvInt("WHATSAPP_MOBILE_OUTBOUND_BACKFILL_LOOKBACK_MS", 10 * 60 * 1000);
const MOBILE_OUTBOUND_BACKFILL_CHAT_LIMIT = readEnvInt("WHATSAPP_MOBILE_OUTBOUND_BACKFILL_CHAT_LIMIT", 30);
const MOBILE_OUTBOUND_BACKFILL_MESSAGE_LIMIT = readEnvInt("WHATSAPP_MOBILE_OUTBOUND_MESSAGE_LIMIT", 20);
const MOBILE_OUTBOUND_FORWARD_CACHE_TTL_MS = readEnvInt("WHATSAPP_MOBILE_OUTBOUND_FORWARD_CACHE_TTL_MS", 60 * 60 * 1000);
const BRIDGE_WEBHOOK_MIN_INTERVAL_MS = readEnvInt("BRIDGE_WEBHOOK_MIN_INTERVAL_MS", 150);
const BRIDGE_WEBHOOK_RETRY_ATTEMPTS = readEnvInt("BRIDGE_WEBHOOK_RETRY_ATTEMPTS", 4);
const BRIDGE_WEBHOOK_RETRY_BASE_DELAY_MS = readEnvInt("BRIDGE_WEBHOOK_RETRY_BASE_DELAY_MS", 1000);
const BRIDGE_WEBHOOK_RETRY_MAX_DELAY_MS = readEnvInt("BRIDGE_WEBHOOK_RETRY_MAX_DELAY_MS", 12000);
const BRIDGE_WEBHOOK_DEDUP_TTL_MS = readEnvInt("BRIDGE_WEBHOOK_DEDUP_TTL_MS", 5 * 60 * 1000);
const OUTBOUND_MEDIA_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/gif",
  "video/mp4",
  "video/webm",
  "video/quicktime",
]);

function isPlaceholderValue(value) {
  return /^replace-with-|^your-|^placeholder$/i.test(String(value || "").trim());
}

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

function bridgeTraceId(req) {
  return String(
    (req && req.headers && (req.headers["x-trace-id"] || req.headers["x-request-id"])) || "",
  ).trim();
}

function initRetryDelayMs(attempt) {
  const base = Math.max(250, WWEBJS_INIT_RETRY_BASE_DELAY_MS);
  const max = Math.max(base, WWEBJS_INIT_RETRY_MAX_DELAY_MS);
  return Math.min(max, base * (2 ** Math.max(0, attempt - 1)));
}

function sessionLogContext(session, extra = {}) {
  return {
    scope: session && session.scopeKey ? session.scopeKey : "",
    company_id: session && session.companyId ? session.companyId : "",
    user_id: session && session.userId ? session.userId : "",
    attempt: session && session.initAttempt ? session.initAttempt : 0,
    state: session && session.state ? session.state : SESSION_STATES.IDLE,
    trace_id: session && session.traceId ? session.traceId : "",
    elapsed_ms: session && session.stateStartedAt ? Date.now() - session.stateStartedAt : 0,
    ...extra,
  };
}

function setSessionState(session, state, extra = {}) {
  if (!session) return;
  const nextState = String(state || SESSION_STATES.IDLE);
  if (session.state !== nextState) {
    session.state = nextState;
    session.stateStartedAt = Date.now();
  }
  session.updatedAt = Date.now();
  if (Object.prototype.hasOwnProperty.call(extra, "retrying")) {
    session.retrying = Boolean(extra.retrying);
  }
  if (Object.prototype.hasOwnProperty.call(extra, "lastError")) {
    session.lastInitError = String(extra.lastError || "");
  }
  persistSessionMetadata(session);
}

function sessionSnapshot(session) {
  if (!session) {
    const state = SESSION_STATES.DISCONNECTED;
    return {
      connected: false,
      state,
      status: state,
      bridge_status: state,
      progress: progressForState(state),
      message: messageForState(state),
      phone: "",
      qr: "",
      retrying: false,
      last_error: "",
      updated_at: new Date().toISOString(),
      scope: "",
      company_id: "",
      user_id: "",
    };
  }
  const state = isSessionReady(session) ? SESSION_STATES.READY : session.state || SESSION_STATES.IDLE;
  return {
    connected: connectedForState(state),
    state,
    status: state,
    bridge_status: state,
    progress: progressForState(state),
    message: messageForState(state, { retrying: session.retrying }),
    phone: state === SESSION_STATES.READY ? session.phone || "" : "",
    qr: state === SESSION_STATES.QR_REQUIRED ? session.lastQrString || "" : "",
    retrying: Boolean(session.retrying),
    last_error: session.lastInitError || "",
    updated_at: new Date(session.updatedAt || Date.now()).toISOString(),
    scope: session.scopeKey,
    company_id: session.companyId || "",
    user_id: session.userId || "",
    bridgeSecretConfigured: Boolean(BRIDGE_SECRET),
  };
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

function inferAttachmentMimeType(attachment, fallback = "") {
  const explicit = String(
    (attachment && (attachment.mime_type || attachment.mimeType || attachment.mimetype)) || fallback || "",
  ).split(";")[0].trim().toLowerCase();
  if (explicit) return explicit;
  const type = String((attachment && (attachment.type || attachment.file_type)) || "").trim().toLowerCase();
  if (type === "video") return "video/mp4";
  if (type === "image") return "image/jpeg";
  return "";
}

function mediaKindForMime(mimeType) {
  const normalized = String(mimeType || "").trim().toLowerCase();
  if (normalized.startsWith("image/")) return "image";
  if (normalized.startsWith("video/")) return "video";
  return "file";
}

async function attachmentToMedia(attachment) {
  if (!attachment || typeof attachment !== "object") return null;
  const sourceUrl = String(attachment.url || attachment.file_url || "").trim();
  const declaredMime = inferAttachmentMimeType(attachment);
  const declaredKind = mediaKindForMime(declaredMime || String(attachment.type || ""));
  if (declaredMime && !OUTBOUND_MEDIA_MIME_TYPES.has(declaredMime)) {
    const error = `Unsupported WhatsApp media type: ${declaredMime}`;
    logBridgeEvent("warn", "whatsapp.outbound.attachment_unsupported", {
      media_type: declaredKind,
      mime_type: declaredMime,
      size: Number(attachment.size || attachment.file_size || 0),
      filename: String(attachment.name || attachment.file_name || "").slice(0, 120),
      reason: error,
    });
    throw new Error(error);
  }
  let parsed = parseDataUrl(sourceUrl);
  if (!parsed && sourceUrl) {
    try {
      const absoluteUrl = sourceUrl.startsWith("/") ? `${PYTHON_BACKEND}${sourceUrl}` : sourceUrl;
      if (/^https?:\/\//i.test(absoluteUrl)) {
        const response = await axios.get(absoluteUrl, { responseType: "arraybuffer", timeout: BRIDGE_FORWARD_TIMEOUT_MS });
        const mimeType = inferAttachmentMimeType(attachment, response.headers["content-type"]);
        if (OUTBOUND_MEDIA_MIME_TYPES.has(mimeType)) {
          parsed = { mimeType, data: Buffer.from(response.data).toString("base64") };
        } else {
          const error = `Unsupported fetched WhatsApp media type: ${mimeType || "unknown"}`;
          logBridgeEvent("warn", "whatsapp.outbound.attachment_unsupported", {
            url: sourceUrl.slice(0, 200),
            media_type: mediaKindForMime(mimeType),
            mime_type: mimeType,
            size: Number(response.headers["content-length"] || attachment.size || attachment.file_size || 0),
            reason: error,
          });
          throw new Error(error);
        }
      }
    } catch (err) {
      logBridgeEvent("warn", "whatsapp.outbound.attachment_fetch_failed", {
        url: sourceUrl.slice(0, 200),
        media_type: declaredKind,
        mime_type: declaredMime,
        size: Number(attachment.size || attachment.file_size || 0),
        error: trimText(err && err.message ? err.message : err, 300),
      });
      throw err;
    }
  }
  if (!parsed) {
    throw new Error("Attachment media could not be converted for WhatsApp");
  }
  parsed.mimeType = String(parsed.mimeType || declaredMime || "").split(";")[0].trim().toLowerCase();
  if (!OUTBOUND_MEDIA_MIME_TYPES.has(parsed.mimeType)) {
    throw new Error(`Unsupported WhatsApp media type: ${parsed.mimeType || "unknown"}`);
  }
  const fallbackExt = parsed.mimeType.split("/")[1] || "jpg";
  const filename = attachment.name || attachment.file_name || `${mediaKindForMime(parsed.mimeType)}.${fallbackExt}`;
  logBridgeEvent("info", "whatsapp.outbound.attachment_prepared", {
    media_type: mediaKindForMime(parsed.mimeType),
    mime_type: parsed.mimeType,
    size: Number(attachment.size || attachment.file_size || Buffer.byteLength(parsed.data || "", "base64")),
    filename: String(filename || "").slice(0, 120),
  });
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
    state: session.state || SESSION_STATES.IDLE,
    retrying: Boolean(session.retrying),
    lastError: session.lastInitError || "",
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
  if (!session) return SESSION_STATES.DISCONNECTED;
  return sessionSnapshot(session).state;
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

function messageTimestampSeconds(msg) {
  const raw = Number((msg && msg.timestamp) || (msg && msg._data && msg._data.t) || 0);
  if (Number.isFinite(raw) && raw > 0) {
    return raw > 10_000_000_000 ? Math.floor(raw / 1000) : Math.floor(raw);
  }
  return Math.floor(Date.now() / 1000);
}

function normalizeWhatsAppParticipant(value) {
  const raw = stringifyIdentityValue(value);
  if (!raw || raw === "status@broadcast" || /@g\.us$/i.test(raw)) {
    return { raw, phone: "", digits: "" };
  }
  const normalized = normalizeInboundPhone(raw) || normalizeInboundPhone(raw.replace(/@(c\.us|s\.whatsapp\.net)$/i, ""));
  if (normalized) {
    return { raw, phone: normalized.e164, digits: normalized.digits };
  }
  const digits = normalizePhoneNumber(raw);
  return {
    raw,
    phone: digits ? `+${digits}` : "",
    digits,
  };
}

async function resolveOutboundTargetIdentity(msg) {
  let chat = null;
  if (msg && typeof msg.getChat === "function") {
    try {
      chat = await msg.getChat();
    } catch {
      chat = null;
    }
  }
  const candidates = [
    msg && msg.to,
    msg && msg.id && msg.id.remote,
    msg && msg._data && msg._data.to,
    chat && chat.id,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeWhatsAppParticipant(candidate);
    if (normalized.digits) {
      return {
        ...normalized,
        chat,
        name: stringifyIdentityValue((chat && chat.name) || (msg && msg._data && msg._data.notifyName) || ""),
      };
    }
  }
  return { raw: "", phone: "", digits: "", chat, name: "" };
}

async function resolveMessageChat(msg) {
  if (!msg || typeof msg.getChat !== "function") return null;
  try {
    return await msg.getChat();
  } catch {
    return null;
  }
}

/**
 * Attempt to resolve a WhatsApp @lid (Linked Device ID) JID to an E.164 phone number.
 *
 * WhatsApp's multi-device protocol assigns @lid JIDs (e.g. "182974364528890@lid") to
 * contacts in some sessions.  These are NOT phone numbers — they are internal device
 * identifiers — so all normal phone-parsing paths fail for them.
 *
 * Resolution strategy:
 *   1. client.getContactById(lidJid) → contact.number
 *   2. client.getNumberId(digitsFromLid) → resolved._serialized → strip @c.us
 *
 * Both calls are best-effort; failure is silently swallowed.
 *
 * @param {object} session - Active bridge session (must have session.client).
 * @param {string}  lidJid  - Raw JID string such as "182974364528890@lid".
 * @returns {Promise<{e164: string, digits: string}|null>} Resolved identity or null.
 */
async function resolveLidContact(session, lidJid) {
  const raw = String(lidJid || "").trim();
  if (!raw || !raw.toLowerCase().includes("@lid") || !session || !session.client) return null;

  // Strategy 1: getContactById — returns the contact object with a .number field
  if (typeof session.client.getContactById === "function") {
    try {
      const contact = await session.client.getContactById(raw);
      if (contact && contact.number) {
        const resolved = normalizeInboundPhone(String(contact.number));
        if (resolved) {
          logBridgeEvent("info", "whatsapp.outbound.lid_resolved_via_contact", {
            scope: session.scopeKey,
            company_id: session.companyId || "",
            user_id: session.userId || "",
            raw_lid: raw,
            resolved_digits: maskPhone(resolved.digits),
          });
          return resolved;
        }
      }
    } catch {
      // getContactById may throw for contacts not yet in the local store; fall through
    }
  }

  // Strategy 2: getNumberId — WhatsApp maps the numeric part to a registered @c.us JID
  const lidDigits = raw.replace(/@lid$/i, "").replace(/\D/g, "");
  if (lidDigits && typeof session.client.getNumberId === "function") {
    try {
      const numberId = await session.client.getNumberId(lidDigits);
      if (numberId) {
        const rawUser = String(
          (typeof numberId === "object" && numberId.user) ||
          (typeof numberId === "string" && numberId) ||
          "",
        ).replace(/@.*$/, "").trim();
        if (rawUser) {
          const resolved = normalizeInboundPhone(rawUser);
          if (resolved) {
            logBridgeEvent("info", "whatsapp.outbound.lid_resolved_via_number_id", {
              scope: session.scopeKey,
              company_id: session.companyId || "",
              user_id: session.userId || "",
              raw_lid: raw,
              resolved_digits: maskPhone(resolved.digits),
            });
            return resolved;
          }
        }
      }
    } catch {
      // getNumberId may fail for unknown contacts; fall through
    }
  }

  return null;
}

async function fetchProfilePictureUrl(session, identity) {
  const raw = stringifyIdentityValue(identity);
  if (!session || !session.client || !raw || typeof session.client.getProfilePicUrl !== "function") return "";
  try {
    return stringifyIdentityValue(await session.client.getProfilePicUrl(raw));
  } catch {
    return "";
  }
}

function chatIdentityValue(value) {
  const raw = stringifyIdentityValue(value);
  return raw || "";
}

function linkedDeviceIdentity(session) {
  const digits = normalizePhoneNumber((session && session.phone) || MY_NUMBER || "");
  return {
    phone: digits ? `+${digits}` : "",
    digits,
  };
}

async function forwardGroupMessage(session, msg, options = {}) {
  if (!msg || !msg.isGroupMsg || msg.from === "status@broadcast") {
    return { forwarded: false, skipped: true };
  }

  const direction = msg.fromMe ? "outbound" : "inbound";
  const chat = await resolveMessageChat(msg);
  const rawFrom = stringifyIdentityValue(msg.from);
  const rawTo = stringifyIdentityValue(msg.to);
  const msgAuthor = stringifyIdentityValue(msg.author || (msg.id && msg.id.participant));
  const msgIdRemote = stringifyIdentityValue(msg.id && msg.id.remote);
  const groupId = chatIdentityValue((chat && chat.id) || msgIdRemote || rawFrom || rawTo);
  const groupName = stringifyIdentityValue((chat && chat.name) || (msg._data && msg._data.notifyName) || groupId);
  const messageId = extractMessageId(msg);
  let mediaPayload = null;
  const ownIdentity = linkedDeviceIdentity(session);

  let senderIdentity = null;
  let senderPhone = "";
  let senderDigits = "";
  let senderNameSaved = "";
  let senderPushname = "";
  let senderName = "";

  if (direction === "inbound") {
    senderIdentity = await resolveInboundSenderIdentity(msg);
    senderPhone = senderIdentity.senderPhone || "";
    senderDigits = senderIdentity.senderPhoneDigits || senderIdentity.rawSenderDigits || "";
    senderNameSaved = senderIdentity.rawFields.contact_name_saved || "";
    senderPushname = senderIdentity.rawFields.contact_pushname || (msg._data && msg._data.notifyName) || "";
    senderName = senderNameSaved || senderPushname || `WhatsApp ${senderPhone || senderDigits || "group participant"}`;
    if (!senderIdentity.isValid) {
      logBridgeEvent("warn", "whatsapp.group.inbound.identity_unresolved", {
        scope: session.scopeKey,
        company_id: session.companyId || "",
        user_id: session.userId || "",
        group_id: groupId,
        raw_from: rawFrom,
        msg_author: msgAuthor,
        msg_id_remote: msgIdRemote,
        candidates: senderIdentity.candidates.map((item) => ({ label: item.label, raw: item.raw })),
      });
      return { forwarded: false, skipped: true, reason: "invalid_group_sender_identity" };
    }
  } else {
    senderPhone = ownIdentity.phone;
    senderDigits = ownIdentity.digits;
    senderName = session.companyId ? `Agent (${session.companyId})` : "WhatsApp Linked Device";
    if (!options.historySync && hasMobileOutboundForwarded(session, msg)) {
      return { forwarded: false, duplicate: true, skipped: true };
    }
    if (!options.historySync && await isPlatformOutboundEcho(session, msg, { digits: groupId })) {
      rememberMobileOutboundForward(session, msg);
      logBridgeEvent("info", "whatsapp.group.outbound.duplicate_skipped", {
        scope: session.scopeKey,
        company_id: session.companyId || "",
        user_id: session.userId || "",
        group_id: groupId,
        message_id: messageId,
        reason: "platform_send_echo",
      });
      return { forwarded: false, duplicate: true, skipped: true };
    }
  }

  try {
    mediaPayload = await downloadMediaPayload(session, msg, `whatsapp.group.${direction}`);
    const senderProfilePictureUrl = (senderIdentity && senderIdentity.rawFields.profile_picture_url)
      ? senderIdentity.rawFields.profile_picture_url
      : "";
    const groupProfilePictureUrl = await fetchProfilePictureUrl(session, groupId);
    const businessAccountId = isPlaceholderValue(WHATSAPP_BUSINESS_ACCOUNT_ID) ? "" : WHATSAPP_BUSINESS_ACCOUNT_ID;
    const metadata = bridgeMetadata(session, {
      direction,
      bridge_event_type: options.historySync || options.source === "history_sync" ? "history_sync" : (direction === "outbound" ? "message_create" : "message"),
      is_group_message: true,
      group_id: groupId,
      group_name: groupName,
      group_profile_picture_url: groupProfilePictureUrl,
      suppress_ai: direction !== "inbound",
    });
    if (senderProfilePictureUrl) {
      metadata.profile_picture_url = senderProfilePictureUrl;
    }

    const messagePayload = {
      from: direction === "inbound" ? (senderDigits || senderPhone) : groupId,
      customer_phone: direction === "inbound" ? senderPhone : "",
      customer_phone_digits: direction === "inbound" ? senderDigits : "",
      sender_name: senderName,
      type: mediaPayload ? mediaPayload.type : "text",
      text: { body: msg.body || "" },
      timestamp: messageTimestampSeconds(msg),
      id: messageId,
      provider_event_id: messageId,
      idempotency_key: messageId ? `whatsapp:${session.companyId || DEFAULT_BRIDGE_COMPANY_ID || ""}:${direction}:group:${messageId}` : "",
      web_bridge: {
        source: "whatsapp_web_bridge",
        direction,
        from_me: direction === "outbound",
        is_group_message: true,
        suppress_ai: direction !== "inbound",
        group_id: groupId,
        group_name: groupName,
        group_sender_phone: senderPhone,
        group_sender_phone_digits: senderDigits,
        group_sender_name: senderName,
        group_profile_picture_url: groupProfilePictureUrl,
        sender_name_saved: senderNameSaved,
        sender_pushname: senderPushname,
        raw_from: rawFrom,
        msg_from: rawFrom,
        msg_to: rawTo,
        msg_author: msgAuthor,
        msg_id_remote: msgIdRemote,
        msg_id_id: stringifyIdentityValue(msg.id && msg.id.id),
        msg_id_serialized: stringifyIdentityValue(msg.id && msg.id._serialized),
        raw_sender_id: direction === "inbound" && senderIdentity ? senderIdentity.rawSenderId : senderPhone,
        provider_sender_id: direction === "inbound" && senderIdentity ? senderIdentity.providerSenderId : (rawFrom || msgIdRemote || groupId),
        selected_identity_source: direction === "inbound" && senderIdentity ? senderIdentity.selectedSource : "linked_device",
        sender_phone: senderPhone,
        sender_phone_digits: senderDigits,
        chat_id: groupId,
        profile_picture_url: senderProfilePictureUrl,
      },
    };
    if (mediaPayload) {
      const payloadForType = { ...mediaPayload };
      delete payloadForType.type;
      messagePayload[mediaPayload.type] = payloadForType;
    }

    const contactProfile = {
      name: senderName || groupName,
      name_saved: senderNameSaved,
      name_push: senderPushname,
      group_name: groupName,
      picture: senderProfilePictureUrl || groupProfilePictureUrl,
      profile_picture: senderProfilePictureUrl || groupProfilePictureUrl,
    };
    const payload = {
      entry: [{
        id: businessAccountId || `whatsapp-web:${session.scopeKey}`,
        changes: [{
          value: {
            business_account_id: businessAccountId,
            metadata,
            messages: [messagePayload],
            contacts: [{
              profile: contactProfile,
              wa_id: senderDigits || groupId,
              web_bridge: {
                sender_phone: senderPhone,
                provider_sender_id: messagePayload.web_bridge.provider_sender_id,
                contact_id: senderIdentity ? senderIdentity.rawFields.contact_id || "" : "",
                profile_picture_url: contactProfile.profile_picture,
                group_profile_picture_url: groupProfilePictureUrl,
                is_group_message: true,
                group_id: groupId,
                group_name: groupName,
              },
            }],
          },
        }],
      }],
    };

    const response = await postWebhookPayload(session, payload, {
      returnResults: Boolean(options.returnResults),
    });
    const duplicate = responseHasDuplicate(response.data);
    if (direction === "outbound") rememberMobileOutboundForward(session, msg);
    logBridgeEvent("info", duplicate ? "whatsapp.group.message_duplicate_skipped" : "whatsapp.group.message_forwarded", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      message_id: messageId,
      group_id: groupId,
      group_name: groupName,
      direction,
      sender: maskPhone(senderDigits || senderPhone),
      backend_status: response.status,
    });
    return { forwarded: !duplicate, duplicate, data: response.data };
  } catch (err) {
    const status = err && err.response && err.response.status;
    if (status === 409) {
      if (direction === "outbound") rememberMobileOutboundForward(session, msg);
      return { forwarded: false, duplicate: true, skipped: true };
    }
    logBridgeEvent("error", "whatsapp.group.message_forward_failed", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      group_id: groupId,
      direction,
      message_id: messageId,
      error: trimText(err && err.message ? err.message : err, 500),
    });
    return { forwarded: false, error: err && err.message ? err.message : String(err || "") };
  }
}

async function downloadMediaPayload(session, msg, eventPrefix) {
  if (!msg || !msg.hasMedia) return null;
  let mediaPayload = null;
  try {
    const media = await msg.downloadMedia();
    if (media && typeof media.mimetype === "string") {
      const mimeType = media.mimetype;
      const mediaType = mimeType.startsWith("image/")
        ? "image"
        : mimeType.startsWith("video/")
          ? "video"
          : mimeType.startsWith("audio/")
            ? "audio"
            : "document";
      mediaPayload = {
        type: mediaType,
        data_url: `data:${media.mimetype};base64,${media.data}`,
        filename: media.filename || "",
        mime_type: mimeType,
        size: Number((msg._data && msg._data.size) || 0),
        caption: msg.body || "",
      };
    }
  } catch (err) {
    logBridgeEvent("error", `${eventPrefix}.media_download_failed`, {
      scope: session.scopeKey,
      message_id: msg && msg.id ? msg.id.id || msg.id._serialized || "" : "",
      error: trimText(err && err.message ? err.message : err, 400),
    });
    console.error(`[${session.scopeKey}] Media download failed: ${err.message}`);
  }
  return mediaPayload;
}

function bridgeMetadata(session, extra = {}) {
  const phoneNumberId = isPlaceholderValue(WHATSAPP_PHONE_NUMBER_ID) ? "" : WHATSAPP_PHONE_NUMBER_ID;
  const metadata = {
    source: "whatsapp_web_bridge",
    bridge_scope: session.scopeKey,
    provider: "whatsapp-web.js",
  };
  if (phoneNumberId) {
    metadata.phone_number_id = phoneNumberId;
  }
  if (session.phone || MY_NUMBER) {
    metadata.display_phone_number = session.phone || MY_NUMBER;
  }
  if (session.companyId || DEFAULT_BRIDGE_COMPANY_ID) {
    metadata.company_id = session.companyId || DEFAULT_BRIDGE_COMPANY_ID;
  }
  if (session.userId) {
    metadata.bridge_user_id = session.userId;
  }
  return { ...metadata, ...extra };
}

function extractPayloadEventKeys(payload) {
  const keys = [];
  for (const entry of (payload && payload.entry) || []) {
    for (const change of (entry && entry.changes) || []) {
      const value = (change && change.value) || {};
      const metadata = value.metadata || {};
      const accountId = String(value.business_account_id || entry.id || metadata.phone_number_id || "").trim();
      for (const status of value.statuses || []) {
        const id = String((status && (status.id || status.message_id || status.recipient_id)) || "").trim();
        if (id) keys.push(`status:${accountId}:${id}:${String(status.status || "")}`);
      }
      for (const msg of value.messages || []) {
        const messageId = String((msg && (msg.id || msg.provider_event_id)) || "").trim();
        const webBridge = (msg && msg.web_bridge && typeof msg.web_bridge === "object") ? msg.web_bridge : {};
        const groupId = String(webBridge.group_id || metadata.group_id || "").trim();
        const eventId = messageId || String(webBridge.msg_id_serialized || webBridge.msg_id_id || "").trim();
        if (msg && msg.type === "reaction") {
          const reaction = msg.reaction || {};
          const target = String(reaction.message_id || reaction.target_message_id || "").trim();
          const actor = String(msg.from || webBridge.provider_sender_id || webBridge.raw_from || "").trim();
          const emoji = String(reaction.emoji || "").trim();
          keys.push(`reaction:${accountId}:${eventId || target}:${actor}:${emoji}:${String(reaction.action || "")}`);
        } else if (eventId) {
          keys.push(`${groupId ? "group-message" : "message"}:${accountId}:${eventId}`);
        }
      }
    }
  }
  if (keys.length === 0) {
    keys.push(`payload:${crypto.createHash("sha256").update(JSON.stringify(payload || {})).digest("hex")}`);
  }
  return [...new Set(keys.filter(Boolean))];
}

function pruneWebhookDedup(session) {
  if (!session || !session.webhookDedup) return;
  const now = Date.now();
  for (const [key, expiresAt] of session.webhookDedup.entries()) {
    if (expiresAt <= now) session.webhookDedup.delete(key);
  }
}

function buildDuplicateWebhookResponse(keys) {
  return {
    status: 200,
    data: {
      processed: true,
      duplicate: true,
      status: "duplicate",
      results: keys.map((key) => ({ duplicate: true, dedup_stage: "bridge_queue", key })),
    },
  };
}

function isRetryableWebhookError(err) {
  const status = err && err.response && Number(err.response.status);
  return status === 429 || status === 408 || status >= 500 || !status;
}

function webhookRetryDelay(attempt) {
  const base = Math.min(
    BRIDGE_WEBHOOK_RETRY_MAX_DELAY_MS,
    BRIDGE_WEBHOOK_RETRY_BASE_DELAY_MS * (2 ** Math.max(0, attempt - 1)),
  );
  return base + Math.floor(Math.random() * Math.min(1000, base));
}

async function postWebhookPayloadDirect(session, payload, options = {}) {
  const rawPayload = JSON.stringify(payload);
  const signature = buildMetaSignature(rawPayload);
  return axios.post(`${PYTHON_BACKEND}${WHATSAPP_WEBHOOK_PATH}`, rawPayload, {
    headers: {
      "Content-Type": "application/json",
      "X-Hub-Signature-256": signature,
      "X-Bridge-Secret": BRIDGE_SECRET,
      "X-Bridge-Company-Id": session.companyId || "",
      "X-Bridge-User-Id": session.userId || "",
      "X-Webhook-Timestamp": String(Math.floor(Date.now() / 1000)),
      ...(options.returnResults ? { "X-Bridge-Return-Results": "true" } : {}),
    },
    timeout: options.timeout || BRIDGE_FORWARD_TIMEOUT_MS,
  });
}

async function drainWebhookQueue(session) {
  if (!session || session.webhookQueueRunning) return;
  session.webhookQueueRunning = true;
  try {
    while (session.webhookQueue && session.webhookQueue.length > 0) {
      const item = session.webhookQueue.shift();
      const waitMs = Math.max(0, BRIDGE_WEBHOOK_MIN_INTERVAL_MS - (Date.now() - (session.webhookLastSentAt || 0)));
      if (waitMs > 0) await sleep(waitMs);

      let lastErr = null;
      for (let attempt = 1; attempt <= BRIDGE_WEBHOOK_RETRY_ATTEMPTS; attempt += 1) {
        try {
          const response = await postWebhookPayloadDirect(session, item.payload, item.options);
          session.webhookLastSentAt = Date.now();
          item.resolve(response);
          lastErr = null;
          break;
        } catch (err) {
          lastErr = err;
          const status = err && err.response && err.response.status;
          if (!isRetryableWebhookError(err) || attempt >= BRIDGE_WEBHOOK_RETRY_ATTEMPTS) break;
          logBridgeEvent(status === 429 ? "warn" : "info", "whatsapp.webhook.rate_limit_avoided", {
            scope: session.scopeKey,
            company_id: session.companyId || "",
            user_id: session.userId || "",
            status: status || "",
            attempt,
            queue_depth: session.webhookQueue.length,
          });
          await sleep(webhookRetryDelay(attempt));
        }
      }
      if (lastErr) {
        for (const key of item.keys || []) {
          session.webhookDedup.delete(key);
        }
        item.reject(lastErr);
      }
    }
  } finally {
    session.webhookQueueRunning = false;
  }
}

async function postWebhookPayload(session, payload, options = {}) {
  if (!session) return postWebhookPayloadDirect(session, payload, options);
  if (!session.webhookQueue) session.webhookQueue = [];
  if (!session.webhookDedup) session.webhookDedup = new Map();
  pruneWebhookDedup(session);
  const keys = extractPayloadEventKeys(payload);
  const freshKeys = keys.filter((key) => !session.webhookDedup.has(key));
  if (freshKeys.length === 0) {
    logBridgeEvent("info", "whatsapp.webhook.duplicate_event_skipped", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      keys: keys.slice(0, 5),
    });
    return buildDuplicateWebhookResponse(keys);
  }
  const expiresAt = Date.now() + BRIDGE_WEBHOOK_DEDUP_TTL_MS;
  for (const key of freshKeys) {
    session.webhookDedup.set(key, expiresAt);
  }
  return new Promise((resolve, reject) => {
    session.webhookQueue.push({ payload, options, keys: freshKeys, resolve, reject });
    drainWebhookQueue(session).catch((err) => {
      for (const key of freshKeys) session.webhookDedup.delete(key);
      reject(err);
    });
  });
}

function responseHasDuplicate(data) {
  const results = Array.isArray(data && data.results) ? data.results : [];
  return Boolean((data && data.duplicate) || results.some((item) => item && item.duplicate));
}

function prunePlatformEchoTrackers(session) {
  const now = Date.now();
  for (const [key, expiresAt] of session.platformSentMessageIds || []) {
    if (expiresAt <= now) session.platformSentMessageIds.delete(key);
  }
  for (const [key, expiresAt] of session.platformSendFingerprints || []) {
    if (expiresAt <= now) session.platformSendFingerprints.delete(key);
  }
  for (const [key, expiresAt] of session.mobileOutboundForwarded || []) {
    if (expiresAt <= now) session.mobileOutboundForwarded.delete(key);
  }
}

function platformEchoFingerprint({ chatId = "", phoneDigits = "", message = "", attachmentCount = 0, ts = 0 }) {
  const timestampMs = Number(ts || Date.now());
  const bucket = Math.floor(timestampMs / Math.max(PLATFORM_OUTBOUND_ECHO_BUCKET_MS, 1));
  return [
    String(chatId || "").trim().toLowerCase(),
    String(phoneDigits || "").replace(/\D/g, ""),
    trimText(message || "", 1000),
    Number(attachmentCount || 0),
    bucket,
  ].join("|");
}

function rememberPlatformOutboundSend(session, data = {}) {
  if (!session) return;
  prunePlatformEchoTrackers(session);
  const expiresAt = Date.now() + PLATFORM_OUTBOUND_ECHO_TTL_MS;
  const fingerprint = platformEchoFingerprint({ ...data, ts: data.ts || Date.now() });
  if (fingerprint) {
    session.platformSendFingerprints.set(fingerprint, expiresAt);
  }
  for (const id of data.messageIds || []) {
    const normalized = String(id || "").trim();
    if (normalized) session.platformSentMessageIds.set(normalized, expiresAt);
  }
}

async function isPlatformOutboundEcho(session, msg, targetIdentity) {
  prunePlatformEchoTrackers(session);
  const messageId = extractMessageId(msg);
  if (messageId && session.platformSentMessageIds.has(messageId)) {
    session.platformSentMessageIds.delete(messageId);
    return true;
  }
  const chatId = stringifyIdentityValue((msg && msg.to) || (msg && msg.id && msg.id.remote) || "");
  const msgTimestampMs = messageTimestampSeconds(msg) ? messageTimestampSeconds(msg) * 1000 : Date.now();
  const fingerprint = platformEchoFingerprint({
    chatId,
    phoneDigits: targetIdentity && targetIdentity.digits,
    message: msg && msg.body,
    attachmentCount: msg && msg.hasMedia ? 1 : 0,
    ts: msgTimestampMs,
  });
  if (fingerprint && session.platformSendFingerprints.has(fingerprint)) {
    session.platformSendFingerprints.delete(fingerprint);
    return true;
  }
  return false;
}

function mobileOutboundCacheKeys(msg) {
  const keys = [];
  const messageId = extractMessageId(msg);
  if (messageId) keys.push(`id:${messageId}`);
  const chatId = stringifyIdentityValue(
    (msg && msg.to) || (msg && msg.from) || (msg && msg.id && msg.id.remote) || "",
  );
  const fingerprint = platformEchoFingerprint({
    chatId,
    message: msg && msg.body,
    attachmentCount: msg && msg.hasMedia ? 1 : 0,
    ts: messageTimestampSeconds(msg) ? messageTimestampSeconds(msg) * 1000 : Date.now(),
  });
  if (fingerprint && !fingerprint.startsWith("|||0|")) {
    keys.push(`fp:${messageTimestampSeconds(msg)}:${fingerprint}`);
  }
  return keys;
}

function hasMobileOutboundForwarded(session, msg) {
  if (!session) return false;
  if (!session.mobileOutboundForwarded) session.mobileOutboundForwarded = new Map();
  prunePlatformEchoTrackers(session);
  return mobileOutboundCacheKeys(msg).some((key) => session.mobileOutboundForwarded.has(key));
}

function rememberMobileOutboundForward(session, msg) {
  if (!session) return;
  if (!session.mobileOutboundForwarded) session.mobileOutboundForwarded = new Map();
  prunePlatformEchoTrackers(session);
  const expiresAt = Date.now() + MOBILE_OUTBOUND_FORWARD_CACHE_TTL_MS;
  for (const key of mobileOutboundCacheKeys(msg)) {
    session.mobileOutboundForwarded.set(key, expiresAt);
  }
}

async function forwardInboundMessage(session, msg, options = {}) {
  if (!msg || msg.fromMe || msg.from === "status@broadcast") return { forwarded: false, skipped: true };
  if (msg.isGroupMsg) return forwardGroupMessage(session, msg, { ...options, direction: "inbound" });

  const senderIdentity = await resolveInboundSenderIdentity(msg);
  const senderPhone = senderIdentity.senderPhoneDigits || senderIdentity.rawSenderDigits;
  const rawFrom = stringifyIdentityValue(msg.from);
  const msgAuthor = stringifyIdentityValue(msg.author);
  const msgIdRemote = stringifyIdentityValue(msg.id && msg.id.remote);
  // Prefer the name the bridge owner saved in their phone; fall back to the
  // contact's own WhatsApp display name; then the WA notify name from the message.
  const senderNameSaved = senderIdentity.rawFields.contact_name_saved || "";
  const senderPushname = senderIdentity.rawFields.contact_pushname || (msg._data && msg._data.notifyName) || "";
  const senderName = senderNameSaved || senderPushname || `WhatsApp ${senderIdentity.senderPhone || senderPhone || "contact"}`;
  const hasMedia = Boolean(msg.hasMedia);
  const inboundMessageId = (msg.id && (msg.id.id || msg.id._serialized)) || "";

  logBridgeEvent("info", "whatsapp.incoming.received", {
    scope: session.scopeKey,
    company_id: session.companyId || "",
    user_id: session.userId || "",
    message_id: msg && msg.id ? msg.id.id || msg.id._serialized || "" : "",
    source: "whatsapp_web_bridge",
    raw_from: rawFrom,
    msg_from: rawFrom,
    msg_author: msgAuthor,
    msg_id_remote: msgIdRemote,
    raw_sender_id: senderIdentity.rawSenderId,
    normalized_sender: senderIdentity.senderPhone,
    selected_identity_source: senderIdentity.selectedSource,
    provider_sender_id: senderIdentity.providerSenderId,
    from: maskPhone(senderIdentity.senderPhone || senderPhone),
    has_media: hasMedia,
    body_length: String(msg.body || "").length,
  });
  if (!senderIdentity.isValid) {
    logBridgeEvent("warn", "whatsapp.incoming.identity_unresolved", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      raw_from: rawFrom,
      msg_author: msgAuthor,
      msg_id_remote: msgIdRemote,
      raw_sender_id: senderIdentity.rawSenderId,
      provider_sender_id: senderIdentity.providerSenderId,
      candidates: senderIdentity.candidates.map((item) => ({ label: item.label, raw: item.raw })),
    });
    return {
      forwarded: false,
      skipped: true,
      reason: "invalid_sender_identity",
      providerSenderId: senderIdentity.providerSenderId,
      rawSenderId: senderIdentity.rawSenderId,
    };
  }

  const mediaPayload = await downloadMediaPayload(session, msg, "whatsapp.incoming");

  try {
    const businessAccountId = isPlaceholderValue(WHATSAPP_BUSINESS_ACCOUNT_ID) ? "" : WHATSAPP_BUSINESS_ACCOUNT_ID;
    const metadata = bridgeMetadata(session, {
      bridge_event_type: options.source === "history_sync" ? "history_sync" : "message",
    });
    if (senderIdentity.rawFields.profile_picture_url) {
      metadata.profile_picture_url = senderIdentity.rawFields.profile_picture_url;
    }

    const messagePayload = {
      from: senderPhone,
      // Explicit phone key — backend must use this as the primary customer identity key
      customer_phone: senderIdentity.senderPhone,
      customer_phone_digits: senderIdentity.senderPhoneDigits,
      type: mediaPayload ? mediaPayload.type : "text",
      text: { body: msg.body || "" },
      timestamp: messageTimestampSeconds(msg),
      id: inboundMessageId,
      provider_event_id: inboundMessageId,
      idempotency_key: inboundMessageId ? `whatsapp:${session.companyId || DEFAULT_BRIDGE_COMPANY_ID || ""}:in:${inboundMessageId}` : "",
      web_bridge: {
        source: "whatsapp_web_bridge",
        raw_from: rawFrom,
        msg_from: rawFrom,
        msg_author: msgAuthor,
        msg_to: stringifyIdentityValue(msg.to),
        msg_id_remote: msgIdRemote,
        msg_id_id: stringifyIdentityValue(msg.id && msg.id.id),
        msg_id_serialized: stringifyIdentityValue(msg.id && msg.id._serialized),
        raw_sender_id: senderIdentity.rawSenderId,
        provider_sender_id: senderIdentity.providerSenderId,
        selected_identity_source: senderIdentity.selectedSource,
        sender_phone: senderIdentity.senderPhone,
        sender_phone_digits: senderIdentity.senderPhoneDigits,
        // Separate saved name vs push/display name so backend can choose correctly
        sender_name_saved: senderNameSaved,
        sender_pushname: senderPushname,
        contact_number: senderIdentity.rawFields.contact_number || "",
        contact_id: senderIdentity.rawFields.contact_id || "",
        chat_id: senderIdentity.rawFields.chat_id || "",
        profile_picture_url: senderIdentity.rawFields.profile_picture_url || "",
      },
    };
    if (mediaPayload) {
      const payloadForType = { ...mediaPayload };
      delete payloadForType.type;
      messagePayload[mediaPayload.type] = payloadForType;
    }

    const payload = {
      entry: [{
        id: businessAccountId || `whatsapp-web:${session.scopeKey}`,
        changes: [{
          value: {
            business_account_id: businessAccountId,
            metadata,
            messages: [messagePayload],
            contacts: [{
              profile: {
                // Use saved name as primary; pushname as fallback — never mix them
                name: senderName,
                name_saved: senderNameSaved,
                name_push: senderPushname,
                picture: senderIdentity.rawFields.profile_picture_url || "",
                profile_picture: senderIdentity.rawFields.profile_picture_url || "",
              },
              wa_id: senderIdentity.senderPhoneDigits || "",
              web_bridge: {
                sender_phone: senderIdentity.senderPhone,
                provider_sender_id: senderIdentity.providerSenderId,
                contact_id: senderIdentity.rawFields.contact_id || "",
                profile_picture_url: senderIdentity.rawFields.profile_picture_url || "",
              },
            }],
          },
        }],
      }],
    };

    const response = await postWebhookPayload(session, payload, {
      returnResults: Boolean(options.returnResults),
    });
    logBridgeEvent("info", "whatsapp.incoming.forwarded", {
      scope: session.scopeKey,
      message_id: messagePayload.id || "",
      source: "whatsapp_web_bridge",
      raw_from: rawFrom,
      raw_sender_id: senderIdentity.rawSenderId,
      normalized_sender: senderIdentity.senderPhone,
      from: maskPhone(senderIdentity.senderPhone || senderPhone),
      type: messagePayload.type,
      backend_status: response.status,
      backend_response_status: response.data && response.data.status ? response.data.status : "",
      backend_message_id: response.data && response.data.message_id ? response.data.message_id : "",
      backend_conversation_id: response.data && response.data.conversation_id ? response.data.conversation_id : "",
      backend_customer_id: response.data && response.data.customer_id ? response.data.customer_id : "",
    });
    console.log(`[${session.scopeKey}] Forwarded inbound message to Python backend`);
    return { forwarded: true, duplicate: responseHasDuplicate(response.data), data: response.data };
  } catch (err) {
    const status = err && err.response && err.response.status;
    if (status === 409) {
      logBridgeEvent("info", "whatsapp.history.duplicate_skipped", {
        scope: session.scopeKey,
        message_id: msg && msg.id ? msg.id.id || msg.id._serialized || "" : "",
        direction: "inbound",
        reason: "backend_replay",
      });
      return { forwarded: false, duplicate: true, skipped: true };
    }
    logBridgeEvent("error", "whatsapp.incoming.forward_failed", {
      scope: session.scopeKey,
      from: maskPhone(senderPhone),
      error: trimText(err && err.message ? err.message : err, 500),
    });
    console.error(`[${session.scopeKey}] Forward failed: ${err.message}`);
    return { forwarded: false, error: err && err.message ? err.message : String(err || "") };
  }
}

async function forwardOutboundMessage(session, msg, options = {}) {
  if (msg && msg.fromMe && msg.isGroupMsg) {
    return forwardGroupMessage(session, msg, { ...options, direction: "outbound" });
  }
  if (!msg || !msg.fromMe || msg.from === "status@broadcast") {
    return { forwarded: false, skipped: true };
  }

  // Use the canonical outbound identity resolver (msg.to → msg.id.remote → chat.id)
  if (!options.historySync && hasMobileOutboundForwarded(session, msg)) {
    return { forwarded: false, duplicate: true, skipped: true };
  }

  const targetIdentity = await resolveOutboundRecipientIdentity(msg);
  const messageId = extractMessageId(msg);
  const rawFrom = stringifyIdentityValue(msg.from);
  const rawTo = stringifyIdentityValue(msg.to);
  const msgIdRemote = stringifyIdentityValue(msg.id && msg.id.remote);
  const targetRaw = stringifyIdentityValue(targetIdentity.rawRecipientId || rawTo || msgIdRemote);
  const targetDigits = targetIdentity.recipientPhoneDigits || targetIdentity.rawRecipientDigits || "";
  let targetIdentityUnresolved = !targetIdentity.recipientPhoneDigits;

  // ── @lid resolution ────────────────────────────────────────────────────────
  // WhatsApp multi-device assigns @lid JIDs (e.g. "182974364528890@lid") to some
  // contacts.  These are internal device identifiers, not phone numbers, so all
  // standard phone-parsing paths return empty.  We attempt a best-effort lookup
  // via the WhatsApp client API before deciding the identity is truly unresolvable.
  if (targetIdentityUnresolved) {
    const rawLidCandidate = [rawTo, msgIdRemote, targetRaw]
      .find((v) => v && v.toLowerCase().includes("@lid")) || "";
    if (rawLidCandidate) {
      const lidResolved = await resolveLidContact(session, rawLidCandidate);
      if (lidResolved) {
        targetIdentity.recipientPhone = lidResolved.e164;
        targetIdentity.recipientPhoneDigits = lidResolved.digits;
        targetIdentityUnresolved = false;
      } else {
        // LID could not be resolved to a phone — skip gracefully.
        // This is expected for contacts whose LID the local WhatsApp session
        // has not yet mapped (e.g. a brand-new contact or a very fresh session).
        // We log at INFO (not WARN) to avoid noisy alerts; the backfill will
        // retry on the next cycle once the contact map has been refreshed.
        logBridgeEvent("info", "whatsapp.outbound.mobile_message_lid_unresolvable", {
          scope: session.scopeKey,
          company_id: session.companyId || "",
          user_id: session.userId || "",
          message_id: messageId,
          raw_to: rawTo,
          msg_id_remote: msgIdRemote,
          raw_lid: rawLidCandidate,
          reason: "lid_not_mapped_to_phone",
        });
        return { forwarded: false, skipped: true, reason: "lid_unresolvable" };
      }
    } else {
      logBridgeEvent("warn", "whatsapp.outbound.mobile_message_forward_failed", {
        scope: session.scopeKey,
        company_id: session.companyId || "",
        user_id: session.userId || "",
        message_id: messageId,
        reason: "target_identity_unresolved",
        raw_to: rawTo,
        msg_id_remote: msgIdRemote,
        target_raw: targetRaw,
      });
      return { forwarded: false, skipped: true, reason: "target_identity_unresolved" };
    }
  }

  if (!options.historySync && await isPlatformOutboundEcho(session, msg, { digits: targetIdentity.recipientPhoneDigits })) {
    rememberMobileOutboundForward(session, msg);
    logBridgeEvent("info", "whatsapp.outbound.mobile_message_duplicate_skipped", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      message_id: messageId,
      target: maskPhone(targetIdentity.recipientPhoneDigits),
      reason: "platform_send_echo",
    });
    return { forwarded: false, duplicate: true, skipped: true };
  }

  const hasMedia = Boolean(msg.hasMedia);
  const mediaPayload = await downloadMediaPayload(session, msg, "whatsapp.outbound");
  const targetName = targetIdentity.recipientName || `WhatsApp ${targetIdentity.recipientPhone || targetDigits || targetRaw || "contact"}`;
  const profilePictureUrl = await fetchProfilePictureUrl(session, targetIdentity.selectedRaw || targetRaw || rawTo);

  logBridgeEvent("info", "whatsapp.outbound.mobile_message_detected", {
    scope: session.scopeKey,
    company_id: session.companyId || "",
    user_id: session.userId || "",
    message_id: messageId,
    target: maskPhone(targetDigits || targetRaw),
    has_media: hasMedia,
    body_length: String(msg.body || "").length,
    source: options.historySync ? "history_sync" : "message_create",
    identity_unresolved: targetIdentityUnresolved,
  });

  try {
    const businessAccountId = isPlaceholderValue(WHATSAPP_BUSINESS_ACCOUNT_ID) ? "" : WHATSAPP_BUSINESS_ACCOUNT_ID;
    const metadata = bridgeMetadata(session, {
      direction: "outbound",
      bridge_event_type: options.historySync ? "history_sync" : "message_create",
      profile_picture_url: profilePictureUrl,
    });

    const messagePayload = {
      from: targetIdentity.recipientPhoneDigits || targetDigits || targetRaw,
      sender_name: session.companyId ? `Agent (${session.companyId})` : 'Agent',
      type: mediaPayload ? mediaPayload.type : "text",
      text: { body: msg.body || "" },
      timestamp: messageTimestampSeconds(msg),
      id: messageId,
      provider_event_id: messageId,
      idempotency_key: messageId ? `whatsapp:${session.companyId || DEFAULT_BRIDGE_COMPANY_ID || ""}:out:${messageId}` : "",
      web_bridge: {
        source: "whatsapp_web_bridge",
        direction: "outbound",
        from_me: true,
        identity_unresolved: false,
        pending_identity: false,
        raw_from: rawFrom,
        msg_from: rawFrom,
        msg_to: rawTo,
        msg_author: stringifyIdentityValue(msg.author),
        msg_id_remote: msgIdRemote,
        msg_id_id: stringifyIdentityValue(msg.id && msg.id.id),
        msg_id_serialized: stringifyIdentityValue(msg.id && msg.id._serialized),
        raw_sender_id: targetIdentity.selectedRaw || targetRaw,
        provider_sender_id: rawFrom || msgIdRemote,
        selected_identity_source: targetIdentity.selectedSource || "msg.to",
        sender_phone: targetIdentity.recipientPhone,
        sender_phone_digits: targetIdentity.recipientPhoneDigits,
        target_phone: targetIdentity.recipientPhone,
        target_phone_digits: targetIdentity.recipientPhoneDigits,
        target_raw_id: targetRaw,
        target_lid_jid: targetRaw.toLowerCase().includes("@lid") ? targetRaw : "",
        target_identity_source: targetIdentity.selectedSource || "raw_target",
        profile_picture_url: profilePictureUrl,
        chat_id: stringifyIdentityValue(targetIdentity.chat && targetIdentity.chat.id),
      },
    };
    if (mediaPayload) {
      const payloadForType = { ...mediaPayload };
      delete payloadForType.type;
      messagePayload[mediaPayload.type] = payloadForType;
    }

    const payload = {
      entry: [{
        id: businessAccountId || `whatsapp-web:${session.scopeKey}`,
        changes: [{
          value: {
            business_account_id: businessAccountId,
            metadata,
            messages: [messagePayload],
            contacts: [{
              profile: { name: targetName },
              wa_id: targetIdentity.recipientPhoneDigits || targetDigits || targetRaw,
              web_bridge: {
                sender_phone: targetIdentity.recipientPhone,
                provider_sender_id: targetIdentity.selectedRaw || targetRaw,
                contact_id: targetIdentity.selectedRaw || targetRaw,
                target_raw_id: targetRaw,
                target_lid_jid: targetRaw.toLowerCase().includes("@lid") ? targetRaw : "",
                profile_picture_url: profilePictureUrl,
              },
            }],
          },
        }],
      }],
    };

    const response = await postWebhookPayload(session, payload, {
      returnResults: Boolean(options.returnResults),
    });
    const duplicate = responseHasDuplicate(response.data);
    rememberMobileOutboundForward(session, msg);
    logBridgeEvent("info", duplicate ? "whatsapp.outbound.mobile_message_duplicate_skipped" : "whatsapp.outbound.mobile_message_forwarded", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      message_id: messageId,
      target: maskPhone(targetDigits || targetRaw),
      backend_status: response.status,
      backend_response_status: response.data && response.data.status ? response.data.status : "",
      source: options.historySync ? "history_sync" : "message_create",
      identity_unresolved: targetIdentityUnresolved,
    });
    return { forwarded: !duplicate, duplicate, data: response.data };
  } catch (err) {
    const status = err && err.response && err.response.status;
    if (status === 409) {
      rememberMobileOutboundForward(session, msg);
      logBridgeEvent("info", "whatsapp.outbound.mobile_message_duplicate_skipped", {
        scope: session.scopeKey,
        company_id: session.companyId || "",
        user_id: session.userId || "",
        message_id: messageId,
      target: maskPhone(targetDigits || targetRaw),
      reason: "backend_replay",
      });
      return { forwarded: false, duplicate: true, skipped: true };
    }
    logBridgeEvent("error", "whatsapp.outbound.mobile_message_forward_failed", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      message_id: messageId,
      target: maskPhone(targetDigits || targetRaw),
      error: trimText(err && err.message ? err.message : err, 500),
    });
    return { forwarded: false, error: err && err.message ? err.message : String(err || "") };
  }
}

async function syncRecentMobileOutboundMessages(session, options = {}) {
  if (!session || !session.isReady || !session.client || session.mobileOutboundBackfillRunning) return;
  session.mobileOutboundBackfillRunning = true;
  const sinceMs = Date.now() - MOBILE_OUTBOUND_BACKFILL_LOOKBACK_MS;
  let checked = 0;
  let forwarded = 0;
  let duplicates = 0;
  let failed = 0;
  try {
    const sourceChats = options.chat
      ? [options.chat]
      : (typeof session.client.getChats === "function" ? await session.client.getChats() : []);
    const chats = (Array.isArray(sourceChats) ? sourceChats : [])
      .filter((chat) => chat && typeof chat.fetchMessages === "function")
      .sort((a, b) => Number(b.timestamp || 0) - Number(a.timestamp || 0))
      .slice(0, MOBILE_OUTBOUND_BACKFILL_CHAT_LIMIT);

    for (const chat of chats) {
      let fetched = [];
      try {
        fetched = await chat.fetchMessages({ limit: MOBILE_OUTBOUND_BACKFILL_MESSAGE_LIMIT });
      } catch {
        failed += 1;
        continue;
      }
      const messages = (Array.isArray(fetched) ? fetched : [])
        .filter((item) => item && item.fromMe && item.from !== "status@broadcast")
        .filter((item) => messageTimestampSeconds(item) * 1000 >= sinceMs)
        .sort((a, b) => messageTimestampSeconds(a) - messageTimestampSeconds(b));
      for (const item of messages) {
        checked += 1;
        const result = await forwardOutboundMessage(session, item, {
          source: options.source || "mobile_backfill",
          returnResults: true,
        });
        if (result && result.forwarded) forwarded += 1;
        else if (result && result.duplicate) duplicates += 1;
        else if (result && result.error) failed += 1;
      }
    }

    if (forwarded || failed) {
      logBridgeEvent(failed ? "warn" : "info", "whatsapp.outbound.mobile_backfill_completed", {
        scope: session.scopeKey,
        company_id: session.companyId || "",
        user_id: session.userId || "",
        source: options.source || "mobile_backfill",
        checked,
        forwarded,
        duplicates,
        failed,
      });
    }
  } catch (err) {
    logBridgeEvent("warn", "whatsapp.outbound.mobile_backfill_failed", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      source: options.source || "mobile_backfill",
      checked,
      forwarded,
      duplicates,
      failed,
      error: trimText(err && err.message ? err.message : err, 500),
    });
  } finally {
    session.mobileOutboundBackfillRunning = false;
  }
}

function scheduleMobileOutboundBackfill(session, source = "event", chat = null) {
  if (!session || !session.isReady) return;
  if (session.mobileOutboundBackfillTimeout) return;
  session.mobileOutboundBackfillTimeout = setTimeout(() => {
    session.mobileOutboundBackfillTimeout = null;
    syncRecentMobileOutboundMessages(session, { source, chat }).catch(() => {});
  }, 1200);
  if (typeof session.mobileOutboundBackfillTimeout.unref === "function") {
    session.mobileOutboundBackfillTimeout.unref();
  }
}

function startMobileOutboundBackfill(session) {
  if (!session || session.mobileOutboundBackfillTimer) return;
  session.mobileOutboundBackfillTimer = setInterval(() => {
    syncRecentMobileOutboundMessages(session, { source: "timer" }).catch(() => {});
  }, MOBILE_OUTBOUND_BACKFILL_INTERVAL_MS);
  if (typeof session.mobileOutboundBackfillTimer.unref === "function") {
    session.mobileOutboundBackfillTimer.unref();
  }
}

function stopMobileOutboundBackfill(session) {
  if (!session) return;
  if (session.mobileOutboundBackfillTimer) clearInterval(session.mobileOutboundBackfillTimer);
  if (session.mobileOutboundBackfillTimeout) clearTimeout(session.mobileOutboundBackfillTimeout);
  session.mobileOutboundBackfillTimer = null;
  session.mobileOutboundBackfillTimeout = null;
}

async function forwardInboundReaction(session, reaction) {
  const rawSender = stringifyIdentityValue(
    reaction && (reaction.senderId || reaction.author || reaction.from || (reaction.id && reaction.id.participant)),
  );
  const targetMessageId = stringifyIdentityValue(
    reaction && (reaction.msgId || reaction.messageId || reaction.parentMsgId || reaction.id),
  );
  const providerReactionId = stringifyIdentityValue(reaction && reaction.id ? reaction.id : "")
    || `${targetMessageId}:${rawSender}:${reaction && reaction.timestamp ? reaction.timestamp : Date.now()}`;
  const emoji = String((reaction && (reaction.reaction || reaction.emoji)) || "").trim();
  const senderPhone = normalizePhoneNumber(rawSender);
  const actorIdentity = senderPhone || rawSender;
  if (!targetMessageId) {
    logBridgeEvent("warn", "whatsapp.incoming.reaction_skipped", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      raw_sender: rawSender,
      target_message_id: targetMessageId,
    });
    return;
  }

  try {
    const phoneNumberId = isPlaceholderValue(WHATSAPP_PHONE_NUMBER_ID) ? "" : WHATSAPP_PHONE_NUMBER_ID;
    const businessAccountId = isPlaceholderValue(WHATSAPP_BUSINESS_ACCOUNT_ID) ? "" : WHATSAPP_BUSINESS_ACCOUNT_ID;
    const metadata = {
      source: "whatsapp_web_bridge",
      bridge_scope: session.scopeKey,
      provider: "whatsapp-web.js",
      company_id: session.companyId || DEFAULT_BRIDGE_COMPANY_ID || "",
      bridge_user_id: session.userId || "",
      display_phone_number: session.phone || MY_NUMBER || "",
      phone_number_id: phoneNumberId,
    };
    const messagePayload = {
      from: actorIdentity,
      type: "reaction",
      timestamp: Math.floor(Date.now() / 1000),
      id: providerReactionId,
      provider_event_id: providerReactionId,
      idempotency_key: providerReactionId ? `whatsapp:${session.companyId || DEFAULT_BRIDGE_COMPANY_ID || ""}:reaction:${providerReactionId}` : "",
      reaction: {
        message_id: targetMessageId,
        emoji,
        action: emoji ? "added" : "removed",
      },
      web_bridge: {
        source: "whatsapp_web_bridge",
        raw_from: rawSender,
        sender_phone: senderPhone ? `+${senderPhone}` : "",
        sender_phone_digits: senderPhone,
        provider_event_id: providerReactionId,
        idempotency_key: providerReactionId ? `whatsapp:${session.companyId || DEFAULT_BRIDGE_COMPANY_ID || ""}:reaction:${providerReactionId}` : "",
        identity_unresolved: !senderPhone,
        provider_sender_id: rawSender,
      },
    };
    const payload = {
      entry: [{
        id: businessAccountId || `whatsapp-web:${session.scopeKey}`,
        changes: [{
          value: {
            business_account_id: businessAccountId,
            metadata,
            messages: [messagePayload],
            contacts: [{
              profile: { name: `WhatsApp ${senderPhone || rawSender}` },
              wa_id: actorIdentity,
              web_bridge: {
                sender_phone: senderPhone ? `+${senderPhone}` : "",
                provider_sender_id: rawSender,
              },
            }],
          },
        }],
      }],
    };
    const response = await postWebhookPayload(session, payload, { returnResults: true });
    logBridgeEvent("info", "whatsapp.incoming.reaction_forwarded", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      message_id: providerReactionId,
      target_message_id: targetMessageId,
      backend_status: response.status,
    });
  } catch (err) {
    logBridgeEvent("error", "whatsapp.incoming.reaction_forward_failed", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      target_message_id: targetMessageId,
      error: trimText(err && err.message ? err.message : err, 500),
    });
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
    session.lastInitError = "";
    session.retrying = false;
    setSessionState(session, SESSION_STATES.QR_REQUIRED);
    logBridgeEvent("info", "whatsapp.session.qr_required", {
      ...sessionLogContext(session),
    });
    console.log(`\n[${session.scopeKey}] Scan this QR code with WhatsApp:`);
    qrcode.generate(qr, { small: true });
  });

  client.on("authenticated", () => {
    session.lastQrString = "";
    session.isAuthenticated = true;
    session.lastInitError = "";
    session.retrying = false;
    setSessionState(session, SESSION_STATES.QR_SCANNED);
    logBridgeEvent("info", "whatsapp.session.qr_scanned", {
      ...sessionLogContext(session),
    });
    setSessionState(session, SESSION_STATES.AUTHENTICATED);
    logBridgeEvent("info", "whatsapp.session.authenticated", {
      ...sessionLogContext(session),
    });
    setSessionState(session, SESSION_STATES.INITIALIZING);
    logBridgeEvent("info", "whatsapp.session.waiting_for_ready", {
      ...sessionLogContext(session),
      timeout_ms: WWEBJS_READY_AFTER_INIT_TIMEOUT_MS,
    });
    console.log(`[${session.scopeKey}] WhatsApp authenticated`);
  });

  client.on("auth_failure", (message) => {
    session.isReady = false;
    session.isAuthenticated = false;
    session.lastQrString = "";
    session.lastInitError = String(message || "Authentication failed");
    session.retrying = false;
    setSessionState(session, SESSION_STATES.FAILED, { lastError: session.lastInitError });
    logBridgeEvent("error", "whatsapp.session.auth_failed", {
      ...sessionLogContext(session),
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
    session.retrying = false;
    session.phone = String(
      (client.info && client.info.wid && client.info.wid.user) || MY_NUMBER || "",
    ).trim();
    setSessionState(session, SESSION_STATES.READY);
    // #region agent log
    _dbgLog("H4", "bridge.js:ready", "client ready", { scopeKey: session.scopeKey });
    // #endregion
    logBridgeEvent("info", "whatsapp.session.ready", {
      ...sessionLogContext(session),
      phone: maskPhone(session.phone),
    });
    console.log(
      `[${session.scopeKey}] READY company=${session.companyId || "-"} user=${session.userId || "-"} phone=${session.phone || "-"}`,
    );
    startMobileOutboundBackfill(session);
    scheduleMobileOutboundBackfill(session, "ready");
  });

  client.on("disconnected", (reason) => {
    stopMobileOutboundBackfill(session);
    session.isReady = false;
    session.isAuthenticated = false;
    session.lastQrString = "";
    session.phone = "";
    session.retrying = false;
    setSessionState(session, SESSION_STATES.DISCONNECTED);
    logBridgeEvent("warn", "whatsapp.session.disconnected", {
      ...sessionLogContext(session),
      reason: trimText(reason, 300),
    });
    console.log(`[${session.scopeKey}] Disconnected: ${reason}`);
  });

  client.on("message", async (msg) => {
    if (msg && msg.fromMe) return;
    await forwardInboundMessage(session, msg);
  });

  client.on("message_create", async (msg) => {
    if (!msg || !msg.fromMe) return;
    await forwardOutboundMessage(session, msg);
  });

  client.on("message_ack", (msg) => {
    if (msg && msg.fromMe) scheduleMobileOutboundBackfill(session, "message_ack");
  });

  client.on("chat_update", (chat) => {
    scheduleMobileOutboundBackfill(
      session,
      "chat_update",
      chat && typeof chat.fetchMessages === "function" ? chat : null,
    );
  });

  client.on("message_reaction", async (reaction) => {
    await forwardInboundReaction(session, reaction);
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
    phone: String(scopeInput.phone || "").trim(),
    isReady: false,
    isAuthenticated: false,
    state: scopeInput.state === SESSION_STATES.READY ? SESSION_STATES.INITIALIZING : (scopeInput.state || SESSION_STATES.IDLE),
    stateStartedAt: Date.now(),
    updatedAt: Date.now(),
    retrying: false,
    initAttempt: 0,
    clientStarted: false,
    traceId: "",
    initPromise: null,
    lastInitError: String(scopeInput.lastError || "").trim(),
    lastReadyAt: 0,
    lastQrString: "",
    client: null,
    platformSentMessageIds: new Map(),
    platformSendFingerprints: new Map(),
    mobileOutboundForwarded: new Map(),
    webhookQueue: [],
    webhookQueueRunning: false,
    webhookLastSentAt: 0,
    webhookDedup: new Map(),
    mobileOutboundBackfillTimer: null,
    mobileOutboundBackfillTimeout: null,
    mobileOutboundBackfillRunning: false,
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
  if (
    session.clientStarted &&
    [
      SESSION_STATES.QR_REQUIRED,
      SESSION_STATES.QR_SCANNED,
      SESSION_STATES.AUTHENTICATED,
      SESSION_STATES.INITIALIZING,
      SESSION_STATES.RECONNECTING,
    ].includes(session.state)
  ) {
    return;
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
  session.clientStarted = false;
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
  session.retrying = false;
  setSessionState(session, SESSION_STATES.INITIALIZING);
  logBridgeEvent("info", "whatsapp.session.init_start", {
    ...sessionLogContext(session),
    max_attempts: WWEBJS_INIT_MAX_ATTEMPTS,
    ready_timeout_ms: WWEBJS_READY_AFTER_INIT_TIMEOUT_MS,
  });

  const maxAttempts = Math.max(1, WWEBJS_INIT_MAX_ATTEMPTS);
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    session.initAttempt = attempt;
    session.lastInitError = "";
    try {
      session.clientStarted = true;
      await withTimeout(
        session.client.initialize(),
        WWEBJS_AUTH_TIMEOUT_MS,
        `WhatsApp client initialize for ${session.scopeKey}`,
      );
      await configurePuppeteerPage(session);

      const readyState = await waitForReadyOrQr(session, WWEBJS_READY_AFTER_INIT_TIMEOUT_MS);
      if (readyState === "ready") {
        logBridgeEvent("info", "whatsapp.session.init_ready", {
          ...sessionLogContext(session, { attempt }),
        });
        return;
      }
      if (readyState === "qr") {
        logBridgeEvent("info", "whatsapp.session.init_waiting_for_qr", {
          ...sessionLogContext(session, { attempt }),
        });
        return;
      }
      if (readyState === "timeout" && session.isAuthenticated) {
        const delayMs = initRetryDelayMs(attempt);
        logBridgeEvent("warn", "whatsapp.session.ready_timeout_transient", {
          ...sessionLogContext(session, { attempt }),
          timeout_ms: WWEBJS_READY_AFTER_INIT_TIMEOUT_MS,
          retry_available: attempt < maxAttempts,
        });
        if (attempt < maxAttempts) {
          session.retrying = true;
          setSessionState(session, SESSION_STATES.RECONNECTING, { retrying: true });
          logBridgeEvent("warn", "whatsapp.session.retry_scheduled", {
            ...sessionLogContext(session, { attempt }),
            delay_ms: delayMs,
            next_attempt: attempt + 1,
          });
          const becameReady = await waitForReady(session, delayMs);
          if (becameReady) return;
          try {
            await session.client.destroy();
          } catch {
            /* ignore cleanup failure */
          }
          removeChromiumSingletonLocks(sessionAuthDir(session.scopeKey));
          rebuildSessionClient(session);
          session.lastInitError = "";
          session.retrying = false;
          setSessionState(session, SESSION_STATES.INITIALIZING);
          continue;
        }
      }
      throw new Error(`WhatsApp session did not reach ready state (${readyState})`);
    } catch (err) {
      const formatted = formatBridgeError(err);
      session.isReady = false;
      const transient = isTransientPuppeteerError(err);
      const canRetry = attempt < maxAttempts && transient;
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

      logBridgeEvent(canRetry ? "warn" : "error", canRetry ? "whatsapp.session.init_timeout_transient" : "whatsapp.session.init_failed", {
        ...sessionLogContext(session, { attempt }),
        transient,
        retry_available: canRetry,
        error: formatted.clientMessage,
      });
      console.error(`[${session.scopeKey}] Initialize failed: ${err.message || err}`);

      if (!canRetry) {
        session.retrying = false;
        setSessionState(session, SESSION_STATES.FAILED, { lastError: formatted.clientMessage });
        throw err;
      }

      session.retrying = true;
      setSessionState(session, SESSION_STATES.RECONNECTING, { retrying: true });
      const delayMs = initRetryDelayMs(attempt);
      logBridgeEvent("warn", "whatsapp.session.retry_scheduled", {
        ...sessionLogContext(session, { attempt }),
        delay_ms: delayMs,
        next_attempt: attempt + 1,
      });
      try {
        await session.client.destroy();
      } catch {
        /* ignore cleanup failure */
      }
      removeChromiumSingletonLocks(sessionAuthDir(session.scopeKey));
      rebuildSessionClient(session);
      session.lastInitError = "";
      await sleep(delayMs);
      session.retrying = false;
      setSessionState(session, SESSION_STATES.INITIALIZING);
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

function parseHistoryLimit(value) {
  const parsed = Number.parseInt(String(value || WHATSAPP_HISTORY_DEFAULT_LIMIT), 10);
  const safe = Number.isFinite(parsed) && parsed > 0 ? parsed : WHATSAPP_HISTORY_DEFAULT_LIMIT;
  return Math.min(Math.max(1, safe), WHATSAPP_HISTORY_MAX_LIMIT);
}

function parseSinceTimestamp(value) {
  if (!value) return 0;
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 10_000_000_000 ? Math.floor(value / 1000) : Math.floor(value);
  }
  const text = String(value || "").trim();
  if (!text) return 0;
  if (/^\d+$/.test(text)) {
    const parsed = Number.parseInt(text, 10);
    return parsed > 10_000_000_000 ? Math.floor(parsed / 1000) : parsed;
  }
  const parsedDate = Date.parse(text);
  return Number.isFinite(parsedDate) ? Math.floor(parsedDate / 1000) : 0;
}

async function resolveHistoryChat(session, body = {}) {
  const chatId = String(body.chat_id || body.chatId || "").trim();
  if (chatId) {
    const chat = typeof session.client.getChatById === "function"
      ? await session.client.getChatById(chatId)
      : null;
    if (chat) return { chat, chatId };
  }
  const phone = String(body.phone || body.to || "").trim();
  if (phone) {
    const target = await resolveChatTarget(session.client, phone);
    if (!target.ok) {
      return { error: target.error || "WhatsApp chat could not be resolved", statusCode: target.statusCode || 400 };
    }
    const chat = typeof session.client.getChatById === "function"
      ? await session.client.getChatById(target.chatId)
      : null;
    if (chat) return { chat, chatId: target.chatId, phoneDigits: target.phoneDigits };
  }
  return { error: "Provide phone or chat_id for history sync", statusCode: 400 };
}

async function syncChatHistory(session, body = {}) {
  const limit = parseHistoryLimit(body.limit);
  const sinceTs = parseSinceTimestamp(body.since || body.since_timestamp || body.sinceTimestamp);
  try {
    const target = await resolveHistoryChat(session, body);
    if (target.error) {
      return { ok: false, statusCode: target.statusCode || 400, error: target.error };
    }
    const chatId = target.chatId || stringifyIdentityValue(target.chat && target.chat.id);
    logBridgeEvent("info", "whatsapp.history.sync_started", {
      scope: session.scopeKey,
      company_id: session.companyId || "",
      user_id: session.userId || "",
      chat_id: chatId,
      target: maskPhone(target.phoneDigits || chatId),
      limit,
      since_timestamp: sinceTs || "",
    });
    const fetched = await target.chat.fetchMessages({ limit });
    const messages = (Array.isArray(fetched) ? fetched : [])
      .filter((item) => item && item.from !== "status@broadcast")
      .filter((item) => !sinceTs || messageTimestampSeconds(item) >= sinceTs)
      .sort((a, b) => messageTimestampSeconds(a) - messageTimestampSeconds(b));
    let forwarded = 0;
    let duplicates = 0;
    let failed = 0;
    for (const historyMessage of messages) {
      const result = historyMessage.fromMe
        ? await forwardOutboundMessage(session, historyMessage, { historySync: true, returnResults: true })
        : await forwardInboundMessage(session, historyMessage, { source: "history_sync", returnResults: true });
      const messageId = extractMessageId(historyMessage);
      if (result && result.duplicate) {
        duplicates += 1;
        logBridgeEvent("info", "whatsapp.history.duplicate_skipped", {
          scope: session.scopeKey,
          message_id: messageId,
          chat_id: chatId,
        });
      } else if (result && result.forwarded) {
        forwarded += 1;
        logBridgeEvent("info", "whatsapp.history.message_forwarded", {
          scope: session.scopeKey,
          message_id: messageId,
          chat_id: chatId,
          direction: historyMessage.fromMe ? "outbound" : "inbound",
        });
      } else {
        failed += 1;
      }
    }
    logBridgeEvent("info", "whatsapp.history.sync_completed", {
      scope: session.scopeKey,
      chat_id: chatId,
      fetched: messages.length,
      forwarded,
      duplicates,
      failed,
    });
    return { ok: true, chat_id: chatId, fetched: messages.length, forwarded, duplicates, failed };
  } catch (err) {
    logBridgeEvent("error", "whatsapp.history.sync_failed", {
      scope: session.scopeKey,
      error: trimText(err && err.message ? err.message : err, 500),
    });
    return { ok: false, statusCode: 500, error: err && err.message ? err.message : "History sync failed" };
  }
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
      ...sessionSnapshot(session),
      company_id: session.companyId || "",
      user_id: session.userId || "",
    })),
  });
});

app.get("/session", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  session.traceId = bridgeTraceId(req);
  ensureSessionInitialized(session).catch(() => {});
  res.json(sessionSnapshot(session));
});

app.get("/qr", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  session.traceId = bridgeTraceId(req);
  ensureSessionInitialized(session).catch(() => {});
  const snapshot = sessionSnapshot(session);

  if (session.isReady) {
    return res.json({ ...snapshot, qr_data_url: "", qr_png_base64: "" });
  }
  if (!session.lastQrString) {
    return res.json({ ...snapshot, qr_data_url: "", qr_png_base64: "" });
  }

  QRImage.toDataURL(session.lastQrString, { errorCorrectionLevel: "M", width: 280 }, (err, dataUrl) => {
    if (err) {
      console.error(`[${session.scopeKey}] QR PNG error: ${err.message}`);
      return res.status(500).json({ error: err.message });
    }
    res.json({ ...sessionSnapshot(session), qr_data_url: dataUrl, qr_png_base64: "" });
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
    session.isAuthenticated = false;
    session.lastQrString = "";
    session.phone = "";
    session.retrying = false;
    setSessionState(session, SESSION_STATES.DISCONNECTED);
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
  let mediaItems = [];
  try {
    mediaItems = Array.isArray(attachments)
      ? (await Promise.all(attachments.map(attachmentToMedia))).filter(Boolean)
      : [];
  } catch (err) {
    logBridgeEvent("error", "whatsapp.outbound.attachment_conversion_failed", {
      error: trimText(err && err.message ? err.message : err, 400),
      attachment_count: Array.isArray(attachments) ? attachments.length : 0,
    });
    return res.status(400).json({
      success: false,
      error: err && err.message ? err.message : "Failed to convert attachment for WhatsApp",
    });
  }
  if (!to || (!String(message || "").trim() && mediaItems.length === 0)) {
    return res.status(400).json({ error: "Missing 'to' or content" });
  }

  const session = createSession(sessionFromRequest(req));
  session.traceId = bridgeTraceId(req);
  ensureSessionInitialized(session).catch(() => {});
  logBridgeEvent("info", "whatsapp.outgoing.request", {
    ...sessionLogContext(session),
    target: maskPhone(to),
    body_length: String(message || "").length,
    attachment_count: mediaItems.length,
    status: sessionStatus(session),
  });
  if (!isSessionReady(session)) {
    const snapshot = sessionSnapshot(session);
    logBridgeEvent("warn", "whatsapp.outgoing.send_blocked_not_ready", {
      ...sessionLogContext(session),
      target: maskPhone(to),
      status: snapshot.state,
      last_error: session.lastInitError || "",
    });
    return res.status(503).json({
      success: false,
      code: "WHATSAPP_SESSION_NOT_READY",
      error: session.lastQrString
        ? "WhatsApp session is not connected for this account. Please scan the QR code first."
        : "WhatsApp session is still starting. Please retry in a few seconds.",
      ...snapshot,
      scope: session.scopeKey,
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
    const echoAttachmentCount = mediaItems.length > 0 ? 1 : 0;
    rememberPlatformOutboundSend(session, {
      chatId,
      phoneDigits: target.phoneDigits,
      message,
      attachmentCount: echoAttachmentCount,
    });
    if (mediaItems.length > 1) {
      rememberPlatformOutboundSend(session, {
        chatId,
        phoneDigits: target.phoneDigits,
        message: "",
        attachmentCount: 1,
      });
    }

    if (mediaItems.length > 0) {
      for (let index = 0; index < mediaItems.length; index += 1) {
        const media = mediaItems[index];
        const options = index === 0 && String(message || "").trim() ? { caption: message } : {};
        const result = await sendMessageWithRetry(session, chatId, media, options, {
          phoneDigits: target.phoneDigits,
          messageType: mediaKindForMime(media.mimetype || media.mimeType || ""),
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
    rememberPlatformOutboundSend(session, {
      chatId,
      phoneDigits: target.phoneDigits,
      message,
      attachmentCount: echoAttachmentCount,
      messageIds: sentIds.filter(Boolean),
    });

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
      phone: session.phone || "",
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

app.post("/sync-history", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  session.traceId = bridgeTraceId(req);
  ensureSessionInitialized(session).catch(() => {});
  if (!isSessionReady(session)) {
    const snapshot = sessionSnapshot(session);
    logBridgeEvent("warn", "whatsapp.history.sync_failed", {
      ...sessionLogContext(session),
      reason: "session_not_ready",
      status: snapshot.state,
    });
    return res.status(503).json({
      success: false,
      code: "WHATSAPP_SESSION_NOT_READY",
      error: "WhatsApp session is not ready for history sync.",
      ...snapshot,
      scope: session.scopeKey,
    });
  }

  const result = await syncChatHistory(session, req.body || {});
  if (!result.ok) {
    return res.status(result.statusCode || 500).json({ success: false, ...result, scope: session.scopeKey });
  }
  return res.json({ success: true, ...result, scope: session.scopeKey });
});

app.get("/check/:phone", async (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  const session = createSession(sessionFromRequest(req));
  session.traceId = bridgeTraceId(req);
  ensureSessionInitialized(session).catch(() => {});
  if (!isSessionReady(session)) {
    const snapshot = sessionSnapshot(session);
    logBridgeEvent("warn", "whatsapp.check.not_ready", {
      ...sessionLogContext(session),
      target: maskPhone(req.params.phone || ""),
      status: snapshot.state,
      last_error: session.lastInitError || "",
    });
    return res.status(503).json({
      code: "WHATSAPP_SESSION_NOT_READY",
      error: "WhatsApp session is not ready for this account.",
      ...snapshot,
      scope: session.scopeKey,
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
