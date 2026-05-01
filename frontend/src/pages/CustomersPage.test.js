const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'CustomersPage.js'), 'utf8');

test('Customer detail close clears the customer query parameter', () => {
  expect(source).toContain("nextParams.delete('customer')");
  expect(source).toContain('setSearchParams(nextParams, { replace: true })');
});

test('Customer list and detail render provider avatars when present', () => {
  expect(source).toContain('function AvatarBubble');
  expect(source).toContain('avatar={cust.avatar}');
  expect(source).toContain('avatar={selected.avatar}');
});
