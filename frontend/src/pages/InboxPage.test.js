const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'InboxPage.js'), 'utf8');

test('Inbox no longer contains the manual cross-platform consent banner', () => {
  expect(source).not.toContain('Consent required for cross-platform identity linking');
  expect(source).not.toContain('Grant & Verify');
  expect(source).not.toContain('identity-aware replies');
});

test('Inbox does not call consent status from the chat screen', () => {
  expect(source).not.toContain('/consent/status');
  expect(source).not.toContain('consent/status');
});

test('Inbox socket new_message events upsert by message id', () => {
  expect(source).toContain('mergeMessageUpdate(m, nextMessage)');
  expect(source).toContain('prev.some(m => m.id === nextMessage.id)');
});

test('Inbox image preview has open, close, and broken-image fallback behavior', () => {
  expect(source).toContain('setImagePreview(att)');
  expect(source).toContain('setImagePreviewFailed(true)');
  expect(source).toContain('Image unavailable');
});
