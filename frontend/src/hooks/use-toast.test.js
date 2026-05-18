const { getErrorMessage } = require('./use-toast');

test('getErrorMessage maps invalid product upload format to a spreadsheet message', () => {
  expect(
    getErrorMessage(
      { response: { data: { detail: 'request body must be valid JSON' } } },
      'fallback',
    ),
  ).toBe('Upload the spreadsheet as an Excel or CSV file.');
});

test('getErrorMessage maps unreadable spreadsheets to a user-safe message', () => {
  expect(
    getErrorMessage(
      { response: { data: { detail: 'Could not read spreadsheet file' } } },
      'fallback',
    ),
  ).toBe('The selected file could not be read.');
});

test('getErrorMessage maps unconfigured email delivery to a clean message', () => {
  expect(
    getErrorMessage(
      { response: { data: { detail: 'Email delivery is not configured.' } } },
      'fallback',
    ),
  ).toBe('Email channel is not configured.');
});

test('getErrorMessage maps workspace seat limits to a team limit message', () => {
  expect(
    getErrorMessage(
      { response: { data: { detail: 'Workspace user limit reached (3 seat(s) on your plan). Upgrade or remove users to add more.' } } },
      'fallback',
    ),
  ).toBe('Team member limit reached (3 seat(s) on your plan). Upgrade your plan or remove pending invitations/users before adding another teammate.');
});

test('getErrorMessage maps protected prebuilt article deletion accurately', () => {
  expect(
    getErrorMessage(
      { response: { data: { detail: 'Prebuilt knowledge base articles cannot be deleted. Edit the article or disable AI context instead.' } } },
      'fallback',
    ),
  ).toBe('Prebuilt knowledge base articles cannot be deleted. Edit the article or disable AI context instead.');
});
