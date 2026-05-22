const test = require("node:test");
const assert = require("node:assert/strict");

const {
  envBoolean,
  guardMessageReplay,
  markSessionReadyBaseline,
} = require("../replay_guard");

test("old message before ready baseline is ignored", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);

  const result = guardMessageReplay(session, {}, {
    messageId: "old-1",
    timestampSeconds: 1_700_000_000,
    nowMs: 1_700_000_011_000,
  });

  assert.equal(result.ignored, true);
  assert.equal(result.reason, "before_ready_baseline");
});

test("new message after ready baseline is accepted", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);

  const result = guardMessageReplay(session, {}, {
    messageId: "new-1",
    timestampSeconds: 1_700_000_011,
    nowMs: 1_700_000_011_000,
  });

  assert.equal(result.ignored, false);
});

test("duplicate message id is ignored", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);

  const first = guardMessageReplay(session, {}, {
    messageId: "dup-1",
    timestampSeconds: 1_700_000_011,
    nowMs: 1_700_000_011_000,
  });
  const second = guardMessageReplay(session, {}, {
    messageId: "dup-1",
    timestampSeconds: 1_700_000_012,
    nowMs: 1_700_000_012_000,
  });

  assert.equal(first.ignored, false);
  assert.equal(second.ignored, true);
  assert.equal(second.reason, "duplicate_message_id");
});

test("reconnect sets a new baseline without blocking live messages", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);
  markSessionReadyBaseline(session, 1_700_000_020_000);

  const oldResult = guardMessageReplay(session, {}, {
    messageId: "reconnect-old",
    timestampSeconds: 1_700_000_019,
    nowMs: 1_700_000_021_000,
  });
  const liveResult = guardMessageReplay(session, {}, {
    messageId: "reconnect-live",
    timestampSeconds: 1_700_000_020,
    nowMs: 1_700_000_021_000,
  });

  assert.equal(oldResult.ignored, true);
  assert.equal(liveResult.ignored, false);
});

test("old reaction before ready baseline is ignored", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);

  const result = guardMessageReplay(session, {}, {
    messageId: "reaction:r1:target1:sender1:like:added",
    timestampSeconds: 1_700_000_009,
    nowMs: 1_700_000_011_000,
  });

  assert.equal(result.ignored, true);
  assert.equal(result.reason, "before_ready_baseline");
});

test("duplicate reaction is ignored", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);

  const first = guardMessageReplay(session, {}, {
    messageId: "reaction:r2:target1:sender1:like:added",
    timestampSeconds: 1_700_000_011,
    nowMs: 1_700_000_011_000,
  });
  const second = guardMessageReplay(session, {}, {
    messageId: "reaction:r2:target1:sender1:like:added",
    timestampSeconds: 1_700_000_012,
    nowMs: 1_700_000_012_000,
  });

  assert.equal(first.ignored, false);
  assert.equal(second.ignored, true);
  assert.equal(second.reason, "duplicate_message_id");
});

test("new reaction after ready baseline is accepted", () => {
  const session = {};
  markSessionReadyBaseline(session, 1_700_000_010_000);

  const result = guardMessageReplay(session, {}, {
    messageId: "reaction:r3:target1:sender1:like:added",
    timestampSeconds: 1_700_000_010,
    nowMs: 1_700_000_011_000,
  });

  assert.equal(result.ignored, false);
});

test("mobile outbound backfill flag is disabled by default", () => {
  assert.equal(envBoolean(undefined, false), false);
  assert.equal(envBoolean("", false), false);
});

test("mobile outbound backfill flag can be enabled explicitly", () => {
  assert.equal(envBoolean("true", false), true);
  assert.equal(envBoolean("1", false), true);
  assert.equal(envBoolean("false", true), false);
});
