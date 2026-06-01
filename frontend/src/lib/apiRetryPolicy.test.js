const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'api.js'), 'utf8');

test('settings and notification bootstrap endpoints do not use guarded GET retries', () => {
  expect(source).toContain('NO_GUARDED_RETRY_PREFIXES');
  expect(source).toContain("'/settings/company'");
  expect(source).toContain("'/settings/personal'");
  expect(source).toContain("'/notifications'");
  expect(source).toContain('requestUrl.startsWith(prefix)');
});
