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

test("bridge forwards webhooks and relative media through the configured backend", () => {
  assert.match(bridgeSource, /axios\.post\(`\$\{PYTHON_BACKEND\}\$\{WHATSAPP_WEBHOOK_PATH\}`/);
  assert.match(bridgeSource, /sourceUrl\.startsWith\("\/"\) \? `\$\{PYTHON_BACKEND\}\$\{sourceUrl\}`/);
});

test("webhook forwarding carries bridge authentication headers", () => {
  assert.match(bridgeSource, /"X-Bridge-Secret": BRIDGE_SECRET/);
  assert.match(bridgeSource, /"X-Bridge-Company-Id": session\.companyId \|\| ""/);
  assert.match(bridgeSource, /"X-Bridge-User-Id": session\.userId \|\| ""/);
  assert.match(bridgeSource, /"X-Hub-Signature-256": signature/);
});

test("attachmentToMedia passes X-Bridge-Secret on fetch", () => {
  assert.match(bridgeSource, /headers: \{ "X-Bridge-Secret": BRIDGE_SECRET \}/);
  assert.match(bridgeSource, /responseType: "arraybuffer"/);
});
