const { parsePhoneNumberFromString } = require("libphonenumber-js");

function getFallbackRegion() {
  const r = String(process.env.WHATSAPP_DEFAULT_COUNTRY || "")
    .trim()
    .toUpperCase();
  return r.length === 2 && /^[A-Z]{2}$/.test(r) ? r : undefined;
}

function stringifyIdentityValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number") return String(value).trim();
  if (typeof value !== "object") return String(value || "").trim();
  if (value._serialized) return String(value._serialized || "").trim();
  if (value.user && value.server) return `${value.user}@${value.server}`.trim();
  if (value.user) return String(value.user || "").trim();
  if (value.id) return stringifyIdentityValue(value.id);
  return "";
}

function rawDigits(value) {
  return String(value || "").replace(/\D/g, "");
}

function isProviderIdentifier(rawValue) {
  const raw = String(rawValue || "").trim().toLowerCase();
  if (!raw) return false;
  if (raw.startsWith("wamid.")) return true;
  if (raw.includes("@lid")) return true;
  if (raw.includes("@g.us")) return true;
  if (raw === "status@broadcast") return true;
  return raw.includes("@") && !raw.endsWith("@c.us") && !raw.endsWith("@s.whatsapp.net");
}

function normalizeInboundPhone(value, fallbackCountry) {
  const raw = stringifyIdentityValue(value);
  if (!raw || isProviderIdentifier(raw)) return null;
  const compact = raw
    .replace(/@c\.us$/i, "")
    .replace(/@s\.whatsapp\.net$/i, "")
    .trim();
  const digits = rawDigits(compact);
  if (digits.length < 8 || digits.length > 15) return null;

  const fallback = fallbackCountry !== undefined ? fallbackCountry : getFallbackRegion();
  const candidates = [];
  if (compact.startsWith("+")) {
    candidates.push([compact, undefined]);
  } else {
    candidates.push([`+${digits}`, undefined]);
    if (fallback) candidates.push([compact, fallback]);
  }

  for (const [candidate, region] of candidates) {
    try {
      const parsed = parsePhoneNumberFromString(candidate, region);
      if (parsed && parsed.isValid && parsed.isValid()) {
        const e164 = parsed.format("E.164");
        return {
          raw,
          e164,
          digits: e164.replace(/^\+/, ""),
        };
      }
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

function pushCandidate(candidates, label, value, priority) {
  const raw = stringifyIdentityValue(value);
  if (!raw) return;
  candidates.push({ label, raw, priority });
}

function rawMessageIdentityFields(msg = {}) {
  const id = msg.id || {};
  const data = msg._data || {};
  return {
    msg_from: stringifyIdentityValue(msg.from),
    msg_author: stringifyIdentityValue(msg.author),
    msg_to: stringifyIdentityValue(msg.to),
    msg_id_remote: stringifyIdentityValue(id.remote),
    msg_id_id: stringifyIdentityValue(id.id),
    msg_id_serialized: stringifyIdentityValue(id._serialized),
    data_from: stringifyIdentityValue(data.from),
    data_author: stringifyIdentityValue(data.author),
    data_to: stringifyIdentityValue(data.to),
  };
}

async function resolveInboundSenderIdentity(msg = {}, options = {}) {
  const fallbackCountry = options.fallbackCountry !== undefined ? options.fallbackCountry : getFallbackRegion();
  const rawFields = rawMessageIdentityFields(msg);
  const candidates = [];

  let contact = null;
  let chat = null;

  if (typeof msg.getContact === "function") {
    try {
      contact = await msg.getContact();
    } catch (err) {
      rawFields.contact_error = String((err && err.message) || err || "").slice(0, 300);
    }
  }
  if (typeof msg.getChat === "function") {
    try {
      chat = await msg.getChat();
    } catch (err) {
      rawFields.chat_error = String((err && err.message) || err || "").slice(0, 300);
    }
  }

  if (contact) {
    rawFields.contact_number = stringifyIdentityValue(contact.number);
    rawFields.contact_id = stringifyIdentityValue(contact.id);
    rawFields.contact_name = stringifyIdentityValue(contact.name || contact.pushname || contact.shortName);
    if (typeof contact.getProfilePicUrl === "function") {
      try {
        rawFields.profile_picture_url = stringifyIdentityValue(await contact.getProfilePicUrl());
      } catch (err) {
        rawFields.profile_picture_error = String((err && err.message) || err || "").slice(0, 300);
      }
    }
    pushCandidate(candidates, "contact.number", contact.number, 10);
    pushCandidate(candidates, "contact.id", contact.id, 20);
  }

  pushCandidate(candidates, "msg.from", msg.from, 30);
  pushCandidate(candidates, "msg.author", msg.author, 40);
  pushCandidate(candidates, "msg._data.from", (msg._data || {}).from, 50);
  pushCandidate(candidates, "msg._data.author", (msg._data || {}).author, 60);

  if (chat) {
    rawFields.chat_id = stringifyIdentityValue(chat.id);
    rawFields.chat_name = stringifyIdentityValue(chat.name);
    pushCandidate(candidates, "chat.id", chat.id, 70);
  }

  pushCandidate(candidates, "msg.id.remote", (msg.id || {}).remote, 80);

  let selected = null;
  for (const candidate of candidates.sort((a, b) => a.priority - b.priority)) {
    const normalized = normalizeInboundPhone(candidate.raw, fallbackCountry);
    if (normalized) {
      selected = { ...normalized, source: candidate.label };
      break;
    }
  }

  const rawFrom = rawFields.msg_from || rawFields.data_from || rawFields.msg_id_remote || "";
  const providerSenderId = [rawFrom, rawFields.msg_author, rawFields.msg_id_remote, rawFields.contact_id]
    .find((value) => value && (!selected || value !== selected.raw))
    || "";

  return {
    isValid: Boolean(selected),
    senderPhone: selected ? selected.e164 : "",
    senderPhoneDigits: selected ? selected.digits : "",
    selectedRaw: selected ? selected.raw : "",
    selectedSource: selected ? selected.source : "",
    rawSenderId: selected ? selected.raw : rawFrom,
    rawSenderDigits: rawDigits(rawFrom),
    providerSenderId,
    rawFields,
    candidates,
  };
}

module.exports = {
  isProviderIdentifier,
  normalizeInboundPhone,
  rawMessageIdentityFields,
  resolveInboundSenderIdentity,
  stringifyIdentityValue,
};
