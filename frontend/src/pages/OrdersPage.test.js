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
  patch: jest.fn(),
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

const OrdersPage = require('./OrdersPage').default;

const ORDER = {
  id: 'order-1',
  conversation_id: 'convo-1',
  customer_name: 'Avery Stone',
  customer_phone: '+15550123',
  product_name: 'Black Shirt',
  quantity: 2,
  status: 'admin_review',
  source_channel: 'whatsapp',
  created_at: '2026-05-21T10:00:00Z',
  delivery_address: 'House 12, Islamabad',
  assigned_name: 'Agent One',
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

function installApiMocks({ listError = null, patchError = null } = {}) {
  mockApi.get.mockImplementation((url) => {
    if (listError && url === '/orders') return Promise.reject(listError);
    if (url === '/orders') return Promise.resolve({ data: { items: [ORDER] } });
    if (url === '/orders/order-1') return Promise.resolve({ data: ORDER });
    return Promise.resolve({ data: {} });
  });
  mockApi.patch.mockImplementation((_url, payload) => {
    if (patchError) return Promise.reject(patchError);
    return Promise.resolve({ data: { ...ORDER, status: payload.status } });
  });
}

async function renderPage(initialEntry = '/orders') {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe />
        <OrdersPage />
      </MemoryRouter>,
    );
  });

  await waitForSelector('[data-testid="order-row-order-1"]', container);
  return { container, root };
}

afterEach(() => {
  jest.clearAllMocks();
  document.body.innerHTML = '';
});

test('orders page renders loaded orders', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  expect(container.textContent).toContain('Black Shirt');
  expect(container.textContent).toContain('Avery Stone');

  await act(async () => root.unmount());
});

test('order detail opens and can navigate to conversation', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  const viewButton = await waitForSelector('button[aria-label="View order"]', container);
  await act(async () => {
    viewButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  await waitForSelector('[data-testid="order-detail-modal"]', container);
  expect(container.textContent).toContain('House 12, Islamabad');

  const conversationButton = Array.from(container.querySelectorAll('button')).find((button) => (
    button.textContent.includes('Open conversation')
  ));
  await act(async () => {
    conversationButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const probe = container.querySelector('[data-testid="location-probe"]');
  expect(probe.dataset.pathname).toBe('/inbox');
  expect(probe.dataset.search).toContain('conversation=convo-1');

  await act(async () => root.unmount());
});

test('status update action works', async () => {
  installApiMocks();
  const { container, root } = await renderPage();

  const confirmButton = await waitForSelector('button[aria-label="Mark confirmed"]', container);
  await act(async () => {
    confirmButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  expect(mockApi.patch).toHaveBeenCalledWith('/orders/order-1/status', { status: 'confirmed' });
  expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ type: 'success' }));

  await act(async () => root.unmount());
});

test('error toast shown on list and status failure', async () => {
  installApiMocks({ listError: { response: { data: { detail: 'List failed' } } } });

  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/orders']}>
        <OrdersPage />
      </MemoryRouter>,
    );
  });
  await tick();

  expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ type: 'error', title: 'Orders unavailable' }));
  await act(async () => root.unmount());

  jest.clearAllMocks();
  installApiMocks({ patchError: { response: { data: { detail: 'Patch failed' } } } });
  const rendered = await renderPage();
  const confirmButton = await waitForSelector('button[aria-label="Mark confirmed"]', rendered.container);
  await act(async () => {
    confirmButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ type: 'error', title: 'Update failed' }));
  await act(async () => rendered.root.unmount());
});
