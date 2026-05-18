const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'TicketsPage.js'), 'utf8');

test('ticket detail close suppresses stale ticket query auto-reopen', () => {
  expect(source).toContain('closingTicketIdRef');
  expect(source).toContain("nextParams.delete('ticket')");
  expect(source).toContain('closingTicketIdRef.current === requestedTicketId');
});
