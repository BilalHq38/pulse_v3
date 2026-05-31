const { TextDecoder, TextEncoder } = require('util');

global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;
global.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;
const { MemoryRouter, useLocation } = require('react-router-dom');

const mockApi = {
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  delete: jest.fn(),
};
const mockAuthUser = { role: 'company_admin', company_id: 'co-1' };

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: mockApi,
  getAccessToken: jest.fn(() => ''),
}));

jest.mock('@/hooks/use-toast', () => ({
  getErrorMessage: jest.fn((error, fallback) => error?.response?.data?.detail || fallback),
  showToast: jest.fn(),
}));

jest.mock('@/hooks/use-confirm-dialog', () => ({
  useConfirmDialog: () => ({
    requestConfirmation: jest.fn(),
    confirmDialog: null,
  }),
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockAuthUser,
    loading: false,
  }),
}));

jest.mock('@/components/BulkUploadModal', () => () => null);
jest.mock('@/lib/backend-url', () => ({ resolveMediaUrl: (url) => url || '' }));

const CustomersPage = require('./CustomersPage').default;
const { buildCustomerMethods } = require('../lib/channelUtils');

let setIntervalSpy;
let clearIntervalSpy;

const BASE_CUSTOMER = {
  id: 'cust-1',
  name: 'Avery Stone',
  email: 'avery@example.com',
  phone: '+15550101',
  company: 'BrightWorks',
  segment: 'growth',
  lifetime_value: 1200,
  avg_sentiment: 0.4,
  total_conversations: 3,
  channels: ['email', 'whatsapp', 'whatsapp'],
  tags: [],
};

function LocationProbe() {
  const location = useLocation();
  return (
    <div
      data-testid="location-probe"
      data-pathname={location.pathname}
      data-search={location.search}
    />
  );
}

function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function installApiMocks({ customer = BASE_CUSTOMER, delayedProfile } = {}) {
  mockApi.get.mockImplementation((url) => {
    if (url === '/customers') return Promise.resolve({ data: [customer] });
    if (url === `/customers/${customer.id}`) return Promise.resolve({ data: customer });
    if (url === `/customers/${customer.id}/profile`) {
      return delayedProfile?.promise || Promise.resolve({ data: { engagement_level: 'high' } });
    }
    if (url === '/purchases') return Promise.resolve({ data: [] });
    if (url === '/journey/tracking') return Promise.resolve({ data: [] });
    if (url === `/identity/customer/${customer.id}`) return Promise.resolve({ data: { unified: false } });
    return Promise.resolve({ data: [] });
  });
  mockApi.post.mockResolvedValue({ data: { status: 'sent' } });
  mockApi.put.mockResolvedValue({ data: customer });
  mockApi.delete.mockResolvedValue({ data: {} });
}

async function tick() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForSelector(selector, root = document) {
  for (let i = 0; i < 30; i += 1) {
    const node = root.querySelector(selector);
    if (node) return node;
    await tick();
  }
  throw new Error(`Expected selector ${selector}`);
}

async function waitForNoSelector(selector, root = document) {
  for (let i = 0; i < 30; i += 1) {
    if (!root.querySelector(selector)) return;
    await tick();
  }
  throw new Error(`Expected selector ${selector} to be removed`);
}

async function renderPage(initialEntry = '/customers') {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe />
        <CustomersPage />
      </MemoryRouter>,
    );
  });

  await waitForSelector('[data-testid="customer-row-cust-1"]', container);
  return { container, root };
}

async function openCustomerDetail(container) {
  const row = await waitForSelector('[data-testid="customer-row-cust-1"]', container);
  await act(async () => {
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  return waitForSelector('[data-testid="customer-detail-modal"]', container);
}

afterEach(() => {
  setIntervalSpy?.mockRestore();
  clearIntervalSpy?.mockRestore();
  jest.clearAllMocks();
  document.body.innerHTML = '';
});

beforeEach(() => {
  setIntervalSpy = jest.spyOn(global, 'setInterval').mockImplementation(() => 0);
  clearIntervalSpy = jest.spyOn(global, 'clearInterval').mockImplementation(() => {});
});

test('customer detail closes with X and removes the customer URL parameter', async () => {
  installApiMocks();
  const { container, root } = await renderPage();
  await openCustomerDetail(container);
  expect(container.querySelector('[data-testid="location-probe"]').dataset.search).toContain('customer=cust-1');

  const panel = await waitForSelector('[data-testid="customer-detail-panel"]', container);
  await act(async () => {
    panel.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  expect(container.querySelector('[data-testid="customer-detail-modal"]')).not.toBeNull();

  const closeButton = await waitForSelector('[data-testid="customer-detail-close-btn"]', container);
  await act(async () => {
    closeButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForNoSelector('[data-testid="customer-detail-modal"]', container);
  expect(container.querySelector('[data-testid="location-probe"]').dataset.search).not.toContain('customer=');

  await act(async () => root.unmount());
});

test('customer detail closes with backdrop and Escape', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  const modal = await openCustomerDetail(container);
  await act(async () => {
    modal.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await waitForNoSelector('[data-testid="customer-detail-modal"]', container);

  await openCustomerDetail(container);
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  });
  await waitForNoSelector('[data-testid="customer-detail-modal"]', container);

  await act(async () => root.unmount());
});

test('stale customer detail requests are ignored after close', async () => {
  const delayedProfile = createDeferred();
  installApiMocks({ delayedProfile });
  const { container, root } = await renderPage();

  await openCustomerDetail(container);
  const closeButton = await waitForSelector('[data-testid="customer-detail-close-btn"]', container);
  await act(async () => {
    closeButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await waitForNoSelector('[data-testid="customer-detail-modal"]', container);

  await act(async () => {
    delayedProfile.resolve({ data: { engagement_level: 'stale' } });
  });
  await tick();

  expect(container.querySelector('[data-testid="customer-detail-modal"]')).toBeNull();
  expect(container.textContent).not.toContain('stale');

  await act(async () => root.unmount());
});

test('message methods dedupe channels and keep Email out of the chat picker', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  expect(buildCustomerMethods(BASE_CUSTOMER).map((method) => method.channel)).toEqual(['whatsapp']);

  const messageButton = await waitForSelector('[data-testid="message-customer-cust-1"]', container);
  await act(async () => {
    messageButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const methodButtons = Array.from(container.querySelectorAll('[data-testid^="customer-method-"]'));
  expect(methodButtons).toHaveLength(1);
  expect(methodButtons[0].dataset.testid).toContain('whatsapp');
  expect(container.querySelector('[data-testid^="customer-method-email"]')).toBeNull();

  await act(async () => root.unmount());
});

test('Email button opens the email composer and sends through the communications endpoint', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  const emailButton = await waitForSelector('[data-testid="email-customer-cust-1"]', container);
  await act(async () => {
    emailButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await waitForSelector('[data-testid="customer-email-composer-modal"]', container);

  const sendButton = Array.from(container.querySelectorAll('button'))
    .find((button) => button.textContent.includes('Send As Is'));
  await act(async () => {
    sendButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  expect(mockApi.post).toHaveBeenCalledWith('/communications/email/send', {
    to_email: 'avery@example.com',
    subject: expect.any(String),
    body: expect.any(String),
  }, expect.objectContaining({ timeout: expect.any(Number) }));

  await act(async () => root.unmount());
});

test('Email button prompts for an address when the customer has no email', async () => {
  installApiMocks({
    customer: {
      ...BASE_CUSTOMER,
      email: '',
      channels: ['whatsapp'],
    },
  });
  const { container, root } = await renderPage();

  const emailButton = await waitForSelector('[data-testid="email-customer-cust-1"]', container);
  await act(async () => {
    emailButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const prompt = await waitForSelector('[data-testid="customer-contact-prompt-modal"]', container);
  expect(prompt.textContent).toContain('Email is required before sending an email.');
  expect(container.querySelector('[data-testid="location-probe"]').dataset.pathname).toBe('/customers');

  await act(async () => root.unmount());
});
