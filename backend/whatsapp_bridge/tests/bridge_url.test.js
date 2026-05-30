const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bridgeSource = fs.readFileSync(path.join(__dirname, "../bridge.js"), "utf8");

function extractFunction(name) {
  const start = bridgeSource.indexOf(`function ${name}`);
  if (start < 0) return "";
  
  // Find the matching curly brace
  let openBraces = 0;
  let firstBrace = bridgeSource.indexOf("{", start);
  if (firstBrace < 0) return "";
  
  for (let i = firstBrace; i < bridgeSource.length; i++) {
    if (bridgeSource[i] === "{") openBraces++;
    if (bridgeSource[i] === "}") {
      openBraces--;
      if (openBraces === 0) {
        return bridgeSource.slice(start, i + 1);
      }
    }
  }
  return "";
}

test("resolveInternalBackendUrl and resolvePublicBackendUrl logic", () => {
  const internalFuncStr = extractFunction("resolveInternalBackendUrl");
  const publicFuncStr = extractFunction("resolvePublicBackendUrl");
  assert.ok(internalFuncStr.length > 0, "resolveInternalBackendUrl function not found");
  assert.ok(publicFuncStr.length > 0, "resolvePublicBackendUrl function not found");
  
  // Set up required environment mock variables
  process.env.INTERNAL_BACKEND_URL = "http://gateway:8000";
  process.env.PUBLIC_BACKEND_URL = "https://public-domain.com";
  process.env.PYTHON_BACKEND = "http://localhost:8000";
  
  // Define helper variables used in the function body
  const INTERNAL_BACKEND_URL = "http://gateway:8000";
  const PUBLIC_BACKEND_URL = "https://public-domain.com";
  const logBridgeEvent = () => {}; // mock
  
  // Eval the function definitions
  let resolveInternalBackendUrl;
  eval(internalFuncStr + "\nresolveInternalBackendUrl = resolveInternalBackendUrl;");

  let resolvePublicBackendUrl;
  eval(publicFuncStr + "\nresolvePublicBackendUrl = resolvePublicBackendUrl;");
  
  // Test INTERNAL resolver (gateway:8000)
  assert.equal(
    resolveInternalBackendUrl("/api/webhook/meta/whatsapp"),
    "http://gateway:8000/api/webhook/meta/whatsapp"
  );
  assert.equal(
    resolveInternalBackendUrl("http://localhost:3000/api/media/a.png"),
    "http://gateway:8000/api/media/a.png"
  );
  assert.equal(
    resolveInternalBackendUrl("https://gateway:8000/api/media/a.png"),
    "http://gateway:8000/api/media/a.png"
  );
  assert.equal(
    resolveInternalBackendUrl("https://external.com/img.jpg"),
    "https://external.com/img.jpg"
  );
  
  // Test PUBLIC resolver (public-domain.com)
  assert.equal(
    resolvePublicBackendUrl("/api/webhook/meta/whatsapp"),
    "https://public-domain.com/api/webhook/meta/whatsapp"
  );
  assert.equal(
    resolvePublicBackendUrl("http://localhost:3000/api/media/a.png"),
    "https://public-domain.com/api/media/a.png"
  );
  assert.equal(
    resolvePublicBackendUrl("https://external.com/img.jpg"),
    "https://external.com/img.jpg"
  );
});

test("Axios integration mocking tests", async () => {
  // Mock active environment variables
  process.env.INTERNAL_BACKEND_URL = "http://gateway:8000";
  process.env.PUBLIC_BACKEND_URL = "https://public-domain.com";
  process.env.PYTHON_BACKEND = "http://localhost:8000";
  process.env.WHATSAPP_WEBHOOK_PATH = "/api/webhook/meta/whatsapp";
  process.env.WHATSAPP_BRIDGE_SECRET = "secret123";
  process.env.BRIDGE_FORWARD_TIMEOUT_MS = "5000";

  // Extracts resolved webhook post function body to run a mock integration test
  const webhookFuncStr = extractFunction("postWebhookPayloadDirect");
  assert.ok(webhookFuncStr.length > 0, "postWebhookPayloadDirect function not found");

  // Mock global/scope variables that postWebhookPayloadDirect calls
  const INTERNAL_BACKEND_URL = "http://gateway:8000";
  const PUBLIC_BACKEND_URL = "https://public-domain.com";
  const WHATSAPP_WEBHOOK_PATH = "/api/webhook/meta/whatsapp";
  const BRIDGE_SECRET = "secret123";
  const BRIDGE_FORWARD_TIMEOUT_MS = 5000;
  
  const logBridgeEvent = () => {};
  const buildMetaSignature = () => "sha-sig";
  
  // Mock resolvers within the scope
  const internalFuncStr = extractFunction("resolveInternalBackendUrl");
  let resolveInternalBackendUrl;
  eval(internalFuncStr + "\nresolveInternalBackendUrl = resolveInternalBackendUrl;");

  const publicFuncStr = extractFunction("resolvePublicBackendUrl");
  let resolvePublicBackendUrl;
  eval(publicFuncStr + "\nresolvePublicBackendUrl = resolvePublicBackendUrl;");

  // Track URLs captured by axios mock
  let capturedPostUrl = null;
  let capturedPostHeaders = null;
  const axios = {
    post: async (url, payload, config) => {
      capturedPostUrl = url;
      capturedPostHeaders = config.headers;
      return { status: 200, data: { status: "success" } };
    }
  };

  // Eval and execute the postWebhookPayloadDirect function
  let postWebhookPayloadDirect;
  eval(webhookFuncStr + "\npostWebhookPayloadDirect = postWebhookPayloadDirect;");

  await postWebhookPayloadDirect(
    { companyId: "comp-1", userId: "user-2" },
    { entry: [] }
  );

  // Assert it calls gateway:8000 (Internal) and carries auth headers
  assert.equal(capturedPostUrl, "http://gateway:8000/api/webhook/meta/whatsapp");
  assert.equal(capturedPostHeaders["X-Bridge-Secret"], "secret123");
  assert.equal(capturedPostHeaders["X-Bridge-Company-Id"], "comp-1");
  assert.equal(capturedPostHeaders["X-Bridge-User-Id"], "user-2");
});

test("attachmentToMedia passes X-Bridge-Secret on fetch", async () => {
  const attachmentToMediaStr = extractFunction("attachmentToMedia");
  assert.ok(attachmentToMediaStr.length > 0, "attachmentToMedia function not found");

  // Mock all the required helper functions and globals
  const OUTBOUND_MEDIA_MIME_TYPES = new Set(["image/png", "image/jpeg", "video/mp4"]);
  const inferAttachmentMimeType = (att, ct) => ct || att.mime_type || "image/png";
  const mediaKindForMime = () => "image";
  const parseDataUrl = () => null;
  const logBridgeEvent = () => {};
  const BRIDGE_FORWARD_TIMEOUT_MS = 5000;
  const BRIDGE_SECRET = "secret123";

  // Mock resolveInternalBackendUrl within the scope
  const internalFuncStr = extractFunction("resolveInternalBackendUrl");
  let resolveInternalBackendUrl;
  eval(internalFuncStr + "\nresolveInternalBackendUrl = resolveInternalBackendUrl;");

  let capturedGetUrl = null;
  let capturedGetConfig = null;
  const axios = {
    get: async (url, config) => {
      capturedGetUrl = url;
      capturedGetConfig = config;
      return {
        status: 200,
        headers: { "content-type": "image/png" },
        data: Buffer.from("mock-binary-data")
      };
    }
  };

  // Eval attachmentToMedia
  let attachmentToMedia;
  eval(attachmentToMediaStr + "\nattachmentToMedia = attachmentToMedia;");

  const result = await attachmentToMedia({
    url: "/api/conversations/attachments/media/comp-1/file.png",
    mime_type: "image/png"
  });

  assert.equal(capturedGetUrl, "http://gateway:8000/api/conversations/attachments/media/comp-1/file.png");
  assert.ok(capturedGetConfig);
  assert.equal(capturedGetConfig.headers["X-Bridge-Secret"], "secret123");
  assert.equal(capturedGetConfig.responseType, "arraybuffer");
  assert.equal(result.mimeType, "image/png");
  assert.equal(result.data, Buffer.from("mock-binary-data").toString("base64"));
});
