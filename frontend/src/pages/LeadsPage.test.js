const { TextDecoder, TextEncoder } = require('util');

global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;
global.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;
const { MemoryRouter } = require('react-router-dom');

const mockApi = {
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  delete: jest.fn(),
};

const mockToast = jest.fn();

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: mockApi,
}));

jest.mock('@/hooks/use-toast', () => ({
  getErrorMessage: jest.fn((error, fallback) => error?.response?.data?.detail || fallback),
  showToast: (...args) => mockToast(...args),
}));

jest.mock('@/hooks/use-confirm-dialog', () => ({
  useConfirmDialog: () => ({
    requestConfirmation: jest.fn(),
    confirmDialog: null,
  }),
}));

jest.mock('@/components/BulkUploadModal', () => () => null);
jest.mock('@/lib/backend-url', () => ({ resolveMediaUrl: (url) => url || '' }));

const LeadsPage = require('./LeadsPage').default;

const BASE_LEAD = {
  id: 'lead-1',
  name: 'Avery Lead',
  email: 'avery@example.com',
  phone: '+15550100',
  company: 'Northstar',
  source: 'whatsapp',
  status: 'new',
  grade: 'warm',
  score: 72,
  notes: 'Asked for onboarding details.',
  tags: [],
  channels: ['whatsapp', 'email'],
  nurture_messages: [
    {
      id: 'nm-1',
      message: 'Original nurture message one',
      phase: 'awareness',
      sent: false,
      created_at: '2026-05-01T10:00:00Z',
    },
    {
      id: 'nm-2',
      message: 'Second nurture message',
      phase: 'consideration',
      sent: false,
      created_at: '2026-05-01T11:00:00Z',
    },
  ],
};

function cloneLead(overrides = {}) {
  return {
    ...BASE_LEAD,
    nurture_messages: BASE_LEAD.nurture_messages.map((item) => ({ ...item })),
    ...overrides,
  };
}

async function tick() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForSelector(selector, root = document) {
  for (let i = 0; i < 40; i += 1) {
    const node = root.querySelector(selector);
    if (node) return node;
    await tick();
  }
  throw new Error(`Expected selector ${selector}`);
}

async function waitForNoSelector(selector, root = document) {
  for (let i = 0; i < 40; i += 1) {
    if (!root.querySelector(selector)) return;
    await tick();
  }
  throw new Error(`Expected selector ${selector} to be removed`);
}

function changeValue(element, value) {
  const prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  setter.call(element, value);
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

function installApiMocks(lead = cloneLead()) {
  mockApi.get.mockImplementation((url) => {
    if (url === '/leads') return Promise.resolve({ data: [lead] });
    if (url === '/settings/channels') {
      return Promise.resolve({
        data: [
          {
            channel: 'email',
            enabled: true,
            email_send_enabled: true,
            email_provider: 'smtp_imap',
            smtp_host: 'smtp.example.com',
            email_address: 'sales@example.com',
          },
        ],
      });
    }
    if (url === '/reference-data') {
      return Promise.resolve({
        data: {
          lead_statuses: [{ status_name: 'new' }, { status_name: 'contacted' }, { status_name: 'won' }],
          sources: [{ source_name: 'whatsapp' }, { source_name: 'email' }],
        },
      });
    }
    if (url === `/leads/${lead.id}`) return Promise.resolve({ data: lead });
    return Promise.resolve({ data: [] });
  });
  mockApi.post.mockResolvedValue({ data: { status: 'ok' } });
  mockApi.put.mockResolvedValue({ data: lead });
  mockApi.delete.mockResolvedValue({ data: {} });
}

async function renderPage(initialEntry = '/leads') {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <LeadsPage />
      </MemoryRouter>,
    );
  });

  await waitForSelector('[data-testid="lead-card-lead-1"]', container);
  return { container, root };
}

async function openLeadDetail(container) {
  const card = await waitForSelector('[data-testid="lead-card-lead-1"]', container);
  await act(async () => {
    card.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  return waitForSelector('[data-testid="lead-detail-modal"]', container);
}

afterEach(() => {
  jest.clearAllMocks();
  document.body.innerHTML = '';
});

test('lead Email opens an editable composer and sends edited content', async () => {
  const lead = cloneLead();
  installApiMocks(lead);
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const emailButton = await waitForSelector('[data-testid="email-lead-btn"]', container);
  await act(async () => {
    emailButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="lead-email-composer-modal"]', container);
  const subject = await waitForSelector('[data-testid="lead-email-subject"]', container);
  const body = await waitForSelector('[data-testid="lead-email-body"]', container);

  await act(async () => {
    changeValue(subject, 'Edited lead subject');
    changeValue(body, 'Edited lead body that the sender reviewed.');
  });

  const send = await waitForSelector('[data-testid="send-lead-email"]', container);
  await act(async () => {
    send.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockApi.post).toHaveBeenCalledWith('/communications/email/send', {
    to_email: 'avery@example.com',
    subject: 'Edited lead subject',
    body: 'Edited lead body that the sender reviewed.',
  }, expect.objectContaining({ timeout: expect.any(Number) }));
  await waitForNoSelector('[data-testid="lead-email-composer-modal"]', container);

  await act(async () => root.unmount());
});

test('lead Message method modal includes Email and opens the email composer', async () => {
  const lead = cloneLead();
  installApiMocks(lead);
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const messageButton = await waitForSelector('[data-testid="message-lead-btn"]', container);
  await act(async () => {
    messageButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="lead-message-methods-modal"]', container);
  const emailMethod = await waitForSelector('[data-testid="lead-method-email"]', container);
  expect(emailMethod.textContent).toContain('Email');

  await act(async () => {
    emailMethod.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="lead-email-composer-modal"]', container);
  expect(container.textContent).toContain('To avery@example.com');

  await act(async () => root.unmount());
});

test('lead Message Email warns when the lead has no email address', async () => {
  installApiMocks(cloneLead({ email: '', channels: ['whatsapp'] }));
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const messageButton = await waitForSelector('[data-testid="message-lead-btn"]', container);
  await act(async () => {
    messageButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const emailMethod = await waitForSelector('[data-testid="lead-method-email"]', container);
  await act(async () => {
    emailMethod.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({
    message: 'No email address found for this lead.',
  }));
  expect(container.querySelector('[data-testid="lead-email-composer-modal"]')).toBeNull();

  await act(async () => root.unmount());
});

test('lead Email prompts for contact details when the lead has no email', async () => {
  installApiMocks(cloneLead({ email: '' }));
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const emailButton = await waitForSelector('[data-testid="email-lead-btn"]', container);
  await act(async () => {
    emailButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="lead-contact-prompt-modal"]', container);
  expect(container.textContent).toContain('Email is required before sending an email.');

  await act(async () => root.unmount());
});

test('lead nurture message edit saves only the selected message', async () => {
  const lead = cloneLead();
  const updatedLead = cloneLead({
    nurture_messages: [
      { ...lead.nurture_messages[0], message: 'Edited nurture message one' },
      { ...lead.nurture_messages[1] },
    ],
  });
  installApiMocks(lead);
  mockApi.put.mockResolvedValueOnce({ data: { lead: updatedLead } });
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const editButton = await waitForSelector('[data-testid="edit-nurture-nm-1"]', container);
  await act(async () => {
    editButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const textarea = await waitForSelector('[data-testid="edit-nurture-text-nm-1"]', container);
  await act(async () => {
    changeValue(textarea, 'Edited nurture message one');
  });

  const saveButton = await waitForSelector('[data-testid="save-nurture-edit-nm-1"]', container);
  await act(async () => {
    saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockApi.put).toHaveBeenCalledWith('/leads/lead-1/nurture-messages/nm-1', {
    message: 'Edited nurture message one',
    phase: 'awareness',
  });
  expect(container.textContent).toContain('Edited nurture message one');
  expect(container.textContent).toContain('Second nurture message');

  await act(async () => root.unmount());
});

test('lead nurture edit can be canceled without changing text', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const editButton = await waitForSelector('[data-testid="edit-nurture-nm-1"]', container);
  await act(async () => {
    editButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const textarea = await waitForSelector('[data-testid="edit-nurture-text-nm-1"]', container);
  await act(async () => {
    changeValue(textarea, 'Unsaved nurture text');
  });

  const cancelButton = await waitForSelector('[data-testid="cancel-nurture-edit-nm-1"]', container);
  await act(async () => {
    cancelButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(container.textContent).toContain('Original nurture message one');
  expect(container.textContent).not.toContain('Unsaved nurture text');

  await act(async () => root.unmount());
});

test('lead nurture delete removes only the selected message', async () => {
  const lead = cloneLead();
  const updatedLead = cloneLead({
    nurture_messages: [{ ...lead.nurture_messages[1] }],
  });
  installApiMocks(lead);
  mockApi.delete.mockResolvedValueOnce({ data: { lead: updatedLead } });
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const deleteButton = await waitForSelector('[data-testid="delete-nurture-nm-1"]', container);
  await act(async () => {
    deleteButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockApi.delete).toHaveBeenCalledWith('/leads/lead-1/nurture-messages/nm-1');
  expect(container.textContent).not.toContain('Original nurture message one');
  expect(container.textContent).toContain('Second nurture message');

  await act(async () => root.unmount());
});

test('lead nurture send uses the saved draft endpoint once', async () => {
  const lead = cloneLead();
  installApiMocks(lead);
  mockApi.post.mockResolvedValueOnce({ data: { lead, conversation_id: '' } });
  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const sendButton = await waitForSelector('[data-testid="send-nurture-detail-nm-1"]', container);
  await act(async () => {
    sendButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="lead-nurture-send-modal"]', container);
  const confirmSend = await waitForSelector('[data-testid="submit-nurture-send"]', container);
  await act(async () => {
    confirmSend.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockApi.post).toHaveBeenCalledWith('/leads/lead-1/nurture-messages/nm-1/send', {
    channel: 'whatsapp',
  });

  await act(async () => root.unmount());
});

test('opening a lead detail does not auto score or auto nurture', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  await openLeadDetail(container);

  expect(mockApi.post).not.toHaveBeenCalledWith('/leads/lead-1/score');
  expect(mockApi.post).not.toHaveBeenCalledWith('/leads/lead-1/nurture');

  await act(async () => root.unmount());
});

test('lead detail modal keeps header and actions stable while body scrolls', async () => {
  installApiMocks(cloneLead({
    notes: 'Long lead notes. '.repeat(80),
    scoring_reason: 'Detailed scoring explanation. '.repeat(60),
    nurture_messages: [
      ...BASE_LEAD.nurture_messages,
      {
        id: 'nm-3',
        message: 'Additional long nurture draft. '.repeat(30),
        phase: 'decision',
        sent: false,
        created_at: '2026-05-01T12:00:00Z',
      },
    ],
  }));
  const { container, root } = await renderPage();

  const modal = await openLeadDetail(container);
  const panel = modal.firstElementChild;
  const header = panel.firstElementChild;
  const scrollBody = await waitForSelector('[data-testid="lead-detail-scroll-body"]', container);
  const actions = (await waitForSelector('[data-testid="score-lead-btn"]', container)).closest('.flex-none');
  const messageList = await waitForSelector('[data-testid="lead-nurture-message-list"]', container);

  expect(panel.className).toContain('max-h-[calc(100vh-2rem)]');
  expect(panel.className).toContain('overflow-hidden');
  expect(header.className).toContain('flex-none');
  expect(scrollBody.className).toContain('overflow-y-auto');
  expect(actions.className).toContain('border-t');
  expect(messageList.className).toContain('overflow-y-auto');

  await act(async () => root.unmount());
});

test('manual AI Nurture creates one draft from the lead card action', async () => {
  const lead = cloneLead({ nurture_messages: [] });
  const updatedLead = cloneLead({
    nurture_messages: [
      {
        id: 'nm-new',
        message: 'One generated nurture draft',
        phase: 'awareness',
        sent: false,
        created_at: '2026-05-02T10:00:00Z',
      },
    ],
  });
  let detailLead = lead;
  mockApi.get.mockImplementation((url) => {
    if (url === '/leads') return Promise.resolve({ data: [detailLead] });
    if (url === '/reference-data') {
      return Promise.resolve({
        data: {
          lead_statuses: [{ status_name: 'new' }, { status_name: 'contacted' }, { status_name: 'won' }],
          sources: [{ source_name: 'whatsapp' }, { source_name: 'email' }],
        },
      });
    }
    if (url === `/leads/${lead.id}`) return Promise.resolve({ data: detailLead });
    return Promise.resolve({ data: [] });
  });
  mockApi.post.mockImplementation((url) => {
    if (url === '/leads/lead-1/nurture') {
      detailLead = updatedLead;
      return Promise.resolve({ data: { message: 'One generated nurture draft' } });
    }
    return Promise.resolve({ data: { status: 'ok' } });
  });

  const { container, root } = await renderPage();

  await openLeadDetail(container);
  const nurtureButton = await waitForSelector('[data-testid="nurture-lead-btn"]', container);
  await act(async () => {
    nurtureButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="send-nurture-detail-nm-new"]', container);
  expect(container.textContent).toContain('One generated nurture draft');
  expect(mockApi.post.mock.calls.filter(([url]) => url === '/leads/lead-1/nurture')).toHaveLength(1);
  expect(mockApi.post.mock.calls.filter(([url]) => url === '/leads/lead-1/score')).toHaveLength(0);

  await act(async () => root.unmount());
});

test('AI Auto Nurture All generates drafts before Send Message to All sends them', async () => {
  const lead = cloneLead({ nurture_messages: [] });
  const generatedLead = cloneLead({
    nurture_messages: [
      {
        id: 'nm-batch',
        message: 'Batch generated draft',
        phase: 'awareness',
        sent: false,
        created_at: '2026-05-02T11:00:00Z',
      },
    ],
  });
  const sentLead = cloneLead({
    nurture_messages: [
      {
        id: 'nm-batch',
        message: 'Batch generated draft',
        phase: 'awareness',
        sent: true,
        created_at: '2026-05-02T11:00:00Z',
      },
    ],
  });
  let currentLead = lead;
  mockApi.get.mockImplementation((url) => {
    if (url === '/leads') return Promise.resolve({ data: [currentLead] });
    if (url === '/reference-data') {
      return Promise.resolve({
        data: {
          lead_statuses: [{ status_name: 'new' }, { status_name: 'contacted' }, { status_name: 'won' }],
          sources: [{ source_name: 'whatsapp' }, { source_name: 'email' }],
        },
      });
    }
    if (url === `/leads/${lead.id}`) return Promise.resolve({ data: currentLead });
    return Promise.resolve({ data: [] });
  });
  mockApi.post.mockImplementation((url) => {
    if (url === '/leads/auto-nurture-all') {
      currentLead = generatedLead;
      return Promise.resolve({ data: { total_processed: 1, results: [{ lead_id: 'lead-1', status: 'nurtured' }] } });
    }
    if (url === '/leads/lead-1/nurture-messages/nm-batch/send') {
      currentLead = sentLead;
      return Promise.resolve({ data: { lead: sentLead, conversation_id: '' } });
    }
    return Promise.resolve({ data: { status: 'ok' } });
  });

  const { container, root } = await renderPage();

  const autoButton = await waitForSelector('[data-testid="auto-nurture-all-btn"]', container);
  await act(async () => {
    autoButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const sendAllButton = await waitForSelector('[data-testid="send-nurture-all-btn"]', container);
  expect(mockApi.post).toHaveBeenCalledWith('/leads/auto-nurture-all', {}, { timeout: 120000 });
  expect(mockApi.post.mock.calls.filter(([url]) => url.includes('/send'))).toHaveLength(0);

  await act(async () => {
    sendAllButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockApi.post).toHaveBeenCalledWith('/leads/lead-1/nurture-messages/nm-batch/send', {
    channel: 'whatsapp',
  });
  await waitForNoSelector('[data-testid="send-nurture-all-btn"]', container);

  await act(async () => root.unmount());
});
