const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'DashboardPage.js'), 'utf8');

test('dashboard metric cards link to inbox filters', () => {
  expect(source).toContain("openInboxFilter('incoming_messages')");
  expect(source).toContain("openInboxFilter('active_conversations')");
  expect(source).toContain("openInboxFilter('pending_replies')");
  expect(source).toContain("openInboxFilter('ai_chats')");
  expect(source).toContain("openInboxFilter('human_chats')");
  expect(source).toContain("openInboxFilter('unread')");
});
