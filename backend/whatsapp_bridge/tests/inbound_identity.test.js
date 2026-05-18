const test = require("node:test");
const assert = require("node:assert/strict");

const {
  normalizeInboundPhone,
  resolveInboundSenderIdentity,
  resolveOutboundRecipientIdentity,
} = require("../inbound_identity");

test("WhatsApp Web msg.from c.us normalizes to E.164 sender", async () => {
  const identity = await resolveInboundSenderIdentity({
    from: "923445563662@c.us",
    id: { remote: "923445563662@c.us", id: "AC123" },
  });

  assert.equal(identity.isValid, true);
  assert.equal(identity.senderPhone, "+923445563662");
  assert.equal(identity.senderPhoneDigits, "923445563662");
  assert.equal(identity.selectedSource, "msg.from");
});

test("contact number wins when msg.from is a WhatsApp LID provider id", async () => {
  const identity = await resolveInboundSenderIdentity({
    from: "222436977021015@lid",
    id: { remote: "222436977021015@lid", id: "AC123" },
    async getContact() {
      return {
        number: "923445563662",
        id: { _serialized: "222436977021015@lid", user: "222436977021015", server: "lid" },
        pushname: "Bilal",
      };
    },
  });

  assert.equal(identity.isValid, true);
  assert.equal(identity.senderPhone, "+923445563662");
  assert.equal(identity.rawSenderId, "923445563662");
  assert.equal(identity.providerSenderId, "222436977021015@lid");
  assert.equal(identity.selectedSource, "contact.number");
});

test("contact profile picture URL is captured when available", async () => {
  const identity = await resolveInboundSenderIdentity({
    from: "923445563662@c.us",
    async getContact() {
      return {
        number: "923445563662",
        async getProfilePicUrl() {
          return "https://mmg.whatsapp.net/profile.jpg";
        },
      };
    },
  });

  assert.equal(identity.rawFields.profile_picture_url, "https://mmg.whatsapp.net/profile.jpg");
});

test("provider ids are not treated as phone numbers", () => {
  assert.equal(normalizeInboundPhone("222436977021015@lid"), null);
  assert.equal(normalizeInboundPhone("wamid.HBgM..."), null);
  assert.equal(normalizeInboundPhone("222436977021015"), null);
});

test("invalid sender remains invalid when no real phone candidate exists", async () => {
  const identity = await resolveInboundSenderIdentity({
    from: "222436977021015@lid",
    id: { remote: "222436977021015@lid", id: "AC123" },
    async getContact() {
      return {
        number: "",
        id: { _serialized: "222436977021015@lid" },
      };
    },
  });

  assert.equal(identity.isValid, false);
  assert.equal(identity.senderPhone, "");
  assert.equal(identity.providerSenderId, "222436977021015@lid");
});

test("outbound resolver falls back to raw data remote for mobile sent messages", async () => {
  const identity = await resolveOutboundRecipientIdentity({
    fromMe: true,
    _data: { id: { remote: "923445563662@c.us" } },
  });

  assert.equal(identity.isValid, true);
  assert.equal(identity.recipientPhone, "+923445563662");
  assert.equal(identity.recipientPhoneDigits, "923445563662");
  assert.equal(identity.selectedSource, "msg._data.id.remote");
});

test("outbound resolver does not turn group chats into personal recipients", async () => {
  const identity = await resolveOutboundRecipientIdentity({
    fromMe: true,
    to: "120363111222333444@g.us",
    id: { remote: "120363111222333444@g.us" },
  });

  assert.equal(identity.isValid, false);
  assert.equal(identity.recipientPhoneDigits, "");
});

test("outbound resolver preserves raw LID target for pending identity storage", async () => {
  const identity = await resolveOutboundRecipientIdentity({
    fromMe: true,
    to: "222436977021015@lid",
    id: { remote: "222436977021015@lid" },
  });

  assert.equal(identity.isValid, false);
  assert.equal(identity.recipientPhoneDigits, "");
  assert.equal(identity.rawRecipientId, "222436977021015@lid");
});
