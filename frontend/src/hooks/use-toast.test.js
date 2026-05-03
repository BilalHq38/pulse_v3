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
