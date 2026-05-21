const DEFAULT_SEEN_TTL_MS = 6 * 60 * 60 * 1000;

function ensureSeenCache(session) {
  if (!session.seenMessageIds) session.seenMessageIds = new Map();
  return session.seenMessageIds;
}

function pruneSeenMessageIds(session, nowMs = Date.now()) {
  const seen = ensureSeenCache(session);
  for (const [messageId, expiresAt] of seen.entries()) {
    if (Number(expiresAt || 0) <= nowMs) seen.delete(messageId);
  }
}

function markSessionReadyBaseline(session, nowMs = Date.now()) {
  if (!session) return 0;
  session.readyBaselineMs = Number(nowMs || Date.now());
  session.readyBaselineSeconds = Math.floor(session.readyBaselineMs / 1000);
  session.seenMessageIds = new Map();
  return session.readyBaselineSeconds;
}

function guardMessageReplay(session, message, options = {}) {
  if (!session) return { ignored: false, reason: "" };
  const nowMs = Number(options.nowMs || Date.now());
  pruneSeenMessageIds(session, nowMs);

  const messageId = String(options.messageId || (message && message.id) || "").trim();
  const messageTimestamp = Number(options.timestampSeconds || (message && message.timestamp) || 0);
  const readyBaseline = Number(options.readyBaselineSeconds || session.readyBaselineSeconds || 0);

  if (readyBaseline && messageTimestamp && messageTimestamp < readyBaseline) {
    return {
      ignored: true,
      reason: "before_ready_baseline",
      messageId,
      messageTimestamp,
      readyBaseline,
    };
  }

  const seen = ensureSeenCache(session);
  if (messageId && seen.has(messageId)) {
    return {
      ignored: true,
      reason: "duplicate_message_id",
      messageId,
      messageTimestamp,
      readyBaseline,
    };
  }

  if (messageId) {
    seen.set(messageId, nowMs + Number(options.ttlMs || session.seenMessageTtlMs || DEFAULT_SEEN_TTL_MS));
  }
  return {
    ignored: false,
    reason: "",
    messageId,
    messageTimestamp,
    readyBaseline,
  };
}

module.exports = {
  DEFAULT_SEEN_TTL_MS,
  guardMessageReplay,
  markSessionReadyBaseline,
  pruneSeenMessageIds,
};
