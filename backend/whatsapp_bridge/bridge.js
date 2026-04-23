/**
 * whatsapp_bridge/bridge.js
 * ═══════════════════════════════════════════════════════════════════
 * WhatsApp Web bridge — no Meta account, no Business API needed.
 * Uses whatsapp-web.js to run a real WhatsApp Web session.
 *
 * What it does:
 *   - Shows a QR code in terminal → scan with your WhatsApp
 *   - Exposes HTTP server on port 3001
 *   - Python backend calls POST /send  to send a message
 *   - Incoming WhatsApp messages are forwarded to Python backend
 *
 * Install:   cd whatsapp_bridge && npm install
 * Run:       node bridge.js
 * Then scan QR: WhatsApp → Settings → Linked Devices → Link a Device
 */

const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const qrcode  = require("qrcode-terminal");
const QRImage = require("qrcode");
const express = require("express");
const axios   = require("axios");
const crypto  = require("crypto");
const fs      = require("fs");
const path    = require("path");

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
      if (!key) continue;
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      if (!process.env[key]) process.env[key] = value;
    }
  } catch (err) {
    console.warn(`⚠️  Failed to load env file ${filePath}: ${err.message}`);
  }
}

// Load env in precedence order: shell env vars > repo root .env > cwd/.env
for (const candidate of [
  path.resolve(__dirname, "../../.env"),
  path.resolve(process.cwd(), ".env"),
]) {
  loadEnvFile(candidate);
}

// ── Config ─────────────────────────────────────────────────────────
const BRIDGE_PORT    = process.env.BRIDGE_PORT    || 3001;
const PYTHON_BACKEND = process.env.PYTHON_BACKEND || "http://localhost:8000";
const WHATSAPP_WEBHOOK_PATH = process.env.WHATSAPP_WEBHOOK_PATH || "/api/webhook/meta/whatsapp";
const BRIDGE_SECRET  = process.env.BRIDGE_SECRET  || process.env.WHATSAPP_BRIDGE_SECRET || "";
const BRIDGE_COMPANY_ID = (process.env.BRIDGE_COMPANY_ID || "").trim();
const WHATSAPP_PHONE_NUMBER_ID = (process.env.WHATSAPP_PHONE_NUMBER_ID || process.env.PHONE_NUMBER_ID || "").trim();
const WHATSAPP_BUSINESS_ACCOUNT_ID = (process.env.WHATSAPP_BUSINESS_ACCOUNT_ID || "").trim();
const WEBHOOK_SIGNING_SECRET =
  process.env.WHATSAPP_WEBHOOK_SECRET ||
  process.env.META_WEBHOOK_SECRET ||
  process.env.BRIDGE_WEBHOOK_SECRET ||
  "";
const MY_NUMBER      = process.env.MY_WHATSAPP_NUMBER || "";

function buildMetaSignature(rawPayload) {
  if (!WEBHOOK_SIGNING_SECRET) throw new Error("Webhook signing secret is not configured");
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

// ── WhatsApp client ────────────────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({ 
    dataPath: "./.wwebjs_auth",
    clientId: "pulse_bridge"
  }),
  puppeteer: {
    headless: true,
    executablePath: process.env.CHROME_BIN || process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu"],
  },
});

let isClientReady = false;
let clientInitPromise = null;
/** Latest raw QR string from whatsapp-web.js (for HTTP /qr). Cleared after auth. */
let lastQrString = "";

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

// ── QR code display ────────────────────────────────────────────────
client.on("qr", (qr) => {
  lastQrString = String(qr || "");
  console.log("\n════════════════════════════════════════════════════");
  console.log("  SCAN THIS QR CODE WITH YOUR WHATSAPP:");
  console.log("  WhatsApp → Settings → Linked Devices → Link a Device");
  console.log("════════════════════════════════════════════════════\n");
  qrcode.generate(qr, { small: true });
  console.log("\n  Waiting for scan...\n");
});

client.on("authenticated", () => {
  lastQrString = "";
  console.log("✅  WhatsApp authenticated!");
});
client.on("auth_failure", (msg) => {
  console.error("❌  Auth failed:", msg);
  isClientReady = false;
});
client.on("ready", () => {
  console.log("✅  WhatsApp client READY");
  console.log(`📱  Your number: +${MY_NUMBER}`);
  console.log(`🌐  Bridge on: http://localhost:${BRIDGE_PORT}`);
  if (!BRIDGE_COMPANY_ID) {
    console.warn("⚠️  BRIDGE_COMPANY_ID is empty. Inbound messages may not map to the tenant visible in frontend.");
  } else {
    console.log(`🏢  Bridge company_id: ${BRIDGE_COMPANY_ID}`);
  }
  console.log(`🔗  Python backend: ${PYTHON_BACKEND}\n`);
  isClientReady = true;
});
client.on("disconnected", (r) => {
  console.log("⚠️  Disconnected:", r);
  isClientReady = false;
  console.log("   Delete .wwebjs_auth folder and restart to re-scan QR.");
});

// ── Incoming message → forward to Python backend ───────────────────
client.on("message", async (msg) => {
  if (msg.isGroupMsg || msg.from === "status@broadcast") return;

  const senderPhone = msg.from.replace("@c.us", "").replace(/\D/g, "");
  const senderName  = msg._data?.notifyName || `WhatsApp ${senderPhone}`;
  const isImageMessage = Boolean(msg.hasMedia);
  let imagePayload = null;

  console.log(`📨  [${senderPhone}] ${senderName}: ${msg.body}`);

  if (isImageMessage) {
    try {
      const media = await msg.downloadMedia();
      if (media && typeof media.mimetype === "string" && media.mimetype.startsWith("image/")) {
        imagePayload = {
          data_url: `data:${media.mimetype};base64,${media.data}`,
          filename: media.filename || "",
          mime_type: media.mimetype,
          size: Number(msg._data?.size || 0),
          caption: msg.body || "",
        };
      }
    } catch (err) {
      console.error(`  ✗  Media download failed:`, err.message);
    }
  }

  try {
    // Build payload matching what backend webhook routes expect.
    const payload = {
      entry: [{
        id: WHATSAPP_BUSINESS_ACCOUNT_ID || undefined,
        changes: [{
          value: {
            business_account_id: WHATSAPP_BUSINESS_ACCOUNT_ID || undefined,
            metadata: {
              phone_number_id: WHATSAPP_PHONE_NUMBER_ID,
              display_phone_number: MY_NUMBER,
              company_id: BRIDGE_COMPANY_ID,
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
          }
        }]
      }]
    };

    const rawPayload = JSON.stringify(payload);
    const signature = buildMetaSignature(rawPayload);

    await axios.post(`${PYTHON_BACKEND}${WHATSAPP_WEBHOOK_PATH}`, rawPayload, {
      headers: {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature,
        "X-Bridge-Secret": BRIDGE_SECRET,
      },
      timeout: 10000,
    });
    console.log(`  ↗  Forwarded to Python backend`);
  } catch (err) {
    console.error(`  ✗  Forward failed:`, err.message);
  }
});

// ── HTTP server — Python calls this to SEND messages ──────────────
const app = express();
app.use(express.json());

// Health check
app.get("/health", (_req, res) => {
  res.json({
    status: client.info ? "ready" : "connecting",
    phone: MY_NUMBER,
    bridgeSecretConfigured: Boolean(BRIDGE_SECRET),
    webhookSecretConfigured: Boolean(WEBHOOK_SIGNING_SECRET),
  });
});

// Session + QR for Settings UI (requires X-Bridge-Secret — call only from backend proxy)
app.get("/session", (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  let status = "initializing";
  if (isClientReady) status = "ready";
  else if (lastQrString) status = "need_qr";
  res.json({
    status,
    phone: MY_NUMBER || "",
    bridgeSecretConfigured: Boolean(BRIDGE_SECRET),
  });
});

app.get("/qr", (req, res) => {
  if (!requireBridgeSecret(req, res)) return;
  if (isClientReady) {
    return res.json({ qr_data_url: "", bridge_status: "ready" });
  }
  if (!lastQrString) {
    return res.json({ qr_data_url: "", bridge_status: "waiting_for_qr" });
  }
  QRImage.toDataURL(lastQrString, { errorCorrectionLevel: "M", width: 280 }, (err, dataUrl) => {
    if (err) {
      console.error("QR PNG error:", err.message);
      return res.status(500).json({ error: err.message });
    }
    res.json({ qr_data_url: dataUrl, bridge_status: "need_qr" });
  });
});

// Send message — called by Python messaging_service.py
app.post("/send", async (req, res) => {
  if (!BRIDGE_SECRET)
    return res.status(500).json({ error: "BRIDGE_SECRET is not configured" });
  if (req.headers["x-bridge-secret"] !== BRIDGE_SECRET)
    return res.status(401).json({ error: "Invalid secret" });

  const { to, message, attachments } = req.body;
  const mediaItems = Array.isArray(attachments) ? attachments.map(attachmentToMedia).filter(Boolean) : [];
  if (!to || (!String(message || "").trim() && mediaItems.length === 0))
    return res.status(400).json({ error: "Missing 'to' or content" });

  const chatId = `${String(to).replace(/\D/g,"")}@c.us`;
  
  console.log(`📤 Attempting to send to ${to} (chatId: ${chatId}), client ready: ${isClientReady}`);
  
  // Wait for client to be ready
  if (!isClientReady) {
    console.log("⚠️  Client not ready, waiting...");
    if (clientInitPromise) {
      await clientInitPromise;
    } else {
      return res.status(503).json({ error: "WhatsApp client is not ready. Please wait or restart the bridge." });
    }
  }
  
  try {
    const sentIds = [];
    let captionUsed = false;

    if (mediaItems.length > 0) {
      for (let index = 0; index < mediaItems.length; index += 1) {
        const media = mediaItems[index];
        const options = index === 0 && String(message || "").trim()
          ? { caption: message }
          : {};
        const result = await client.sendMessage(chatId, media, options);
        sentIds.push(result.id.id);
        if (index === 0 && options.caption) captionUsed = true;
      }
    }

    if (String(message || "").trim() && !captionUsed) {
      const textResult = await client.sendMessage(chatId, message);
      sentIds.push(textResult.id.id);
    }

    console.log(`📤  Sent to ${to}: ${String(message || "").substring(0, 50) || "[media]"}`);
    res.json({ success: true, messageId: sentIds[0] || "", messageIds: sentIds });
  } catch (err) {
    console.error(`  ✗  Send failed:`, err.message);
    res.status(500).json({ success: false, error: err.message });
  }
});

// Check if number has WhatsApp
app.get("/check/:phone", async (req, res) => {
  try {
    const isReg = await client.isRegisteredUser(`${req.params.phone.replace(/\D/g,"")}@c.us`);
    res.json({ phone: req.params.phone, has_whatsapp: isReg });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const bridgePortNumber = Number.parseInt(String(BRIDGE_PORT), 10);
const bridgePort = Number.isFinite(bridgePortNumber) ? bridgePortNumber : 3001;

const bridgeServer = app.listen(bridgePort, () => {
  console.log(`🔌  Bridge HTTP on port ${bridgePort}`);
});

bridgeServer.on("error", (err) => {
  if (err && err.code === "EADDRINUSE") {
    console.error(`❌  Bridge port ${bridgePort} is already in use.`);
    console.error("   Stop the existing process or run bridge on another port.");
    console.error(
      "   Example (PowerShell): $env:BRIDGE_PORT='3101'; $env:WHATSAPP_BRIDGE_URL='http://localhost:3101'; node bridge.js",
    );
    process.exit(1);
    return;
  }

  console.error("❌  Bridge failed to start:", err.message || err);
  process.exit(1);
});

// ── Start ──────────────────────────────────────────────────────────
console.log("\n🚀  Starting WhatsApp bridge (first run takes 10-30 seconds)...\n");
clientInitPromise = client.initialize();
