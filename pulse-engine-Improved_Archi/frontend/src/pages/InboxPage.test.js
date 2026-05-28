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

test('Inbox renders video attachments with an openable preview and fallback', () => {
  expect(source).toContain('function ChatVideoThumb');
  expect(source).toContain('setVideoPreview(att)');
  expect(source).toContain('Video unavailable');
});

test('Inbox does not select background conversations on realtime updates', () => {
  expect(source).toContain("if (!activeConvoId || data.conversation_id !== activeConvoId)");
  expect(source).toContain('loadConversations();');
  expect(source).not.toContain('loadConversations(data.conversation_id)');
  expect(source).not.toContain("loadConversations(data?.conversation_id || '')");
});

test('Inbox composer supports generic image and video media attachments', () => {
  expect(source).toContain("const CHAT_VIDEO_TYPES = ['video/mp4', 'video/webm', 'video/quicktime']");
  expect(source).toContain('const CHAT_MEDIA_TYPES = [...CHAT_IMAGE_TYPES, ...CHAT_VIDEO_TYPES]');
  expect(source).toContain("title=\"Attach media\"");
  expect(source).toContain("attachment.type === 'video'");
});

test('Inbox customer sidebar is closed by default on desktop and opens on demand', () => {
  expect(source).toContain('{showCustomerSidebar && (');
  expect(source).toContain('Contact Info');
  expect(source).toContain('customer-sidebar-loading');
  expect(source).not.toContain('lg:translate-x-0');
});

test('Inbox handles realtime message reaction updates and renders reactions on bubbles', () => {
  expect(source).toContain("eventName === 'message_reaction_updated'");
  expect(source).toContain('normalizeReactions');
  expect(source).toContain("data-testid={`msg-${msg.id}-reactions`}");
});

test('Inbox renders AI paused warning state returned by backend', () => {
  expect(source).toContain('isConversationAiDisabled(selectedConvo)');
  expect(source).toContain('data-testid="ai-paused-warning"');
  expect(source).toContain('AI auto-response is paused because the AI provider is unavailable. Please respond manually.');
});

test('Inbox visually marks AI-disabled conversation cards', () => {
  expect(source).toContain('data-testid={`convo-${convo.id}-ai-disabled`}');
  expect(source).toContain('AI paused');
  expect(source).toContain("selectedAiDisabled ? 'AI Paused'");
});

test('Inbox renders WhatsApp group names in list, header, and message rows', () => {
  expect(source).toContain('function isWhatsappGroupConversation');
  expect(source).toContain('function messageGroupName');
  expect(source).toContain('data-testid={`msg-${msg.id}-group-name`}');
  expect(source).toContain('selectedConvoIsGroup');
});

test('Inbox applies dashboard filter query params to conversation loading', () => {
  expect(source).toContain('const INBOX_FILTERS = {');
  expect(source).toContain("searchParams.get('inbox_filter')");
  expect(source).toContain('inbox_filter: effectiveFilter');
  expect(source).toContain("api.get('/conversations', params ? { params } : undefined)");
});

test('Inbox avatar failure state resets when profile picture URL changes', () => {
  expect(source).toContain('setFailed(false);');
  expect(source).toContain('}, [url]);');
});
