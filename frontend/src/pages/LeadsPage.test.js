const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'LeadsPage.js'), 'utf8');

test('Lead detail supports editing, deleting, and sending nurture messages', () => {
  expect(source).toContain('AI Nurture Messages');
  expect(source).toContain('edit-nurture-');
  expect(source).toContain('save-nurture-edit-');
  expect(source).toContain('cancel-nurture-edit-');
  expect(source).toContain('delete-nurture-');
  expect(source).toContain('/nurture-messages/${nurtureMessage.id}');
  expect(source).toContain('/nurture-messages/${nurtureMessage.id}/send');
});

test('Lead detail shows conversion separately from destructive delete', () => {
  expect(source).toContain('Convert to Customer');
  expect(source).toContain('convert-lead-btn');
  expect(source).toContain('delete-lead-detail-btn');
  expect(source).toContain('/convert-to-customer');
});

test('Manual stage changes use the stage endpoint', () => {
  expect(source).toContain('/stage');
  expect(source).toContain('lead-stage-reason');
  expect(source).toContain('won');
});

test('Lead cards and detail render provider avatars when available', () => {
  expect(source).toContain('function LeadAvatar');
  expect(source).toContain('<LeadAvatar lead={lead} />');
  expect(source).toContain('lead?.avatar');
});
