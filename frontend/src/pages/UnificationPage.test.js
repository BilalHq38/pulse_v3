const { TextDecoder, TextEncoder } = require('util');

global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;
global.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;

const mockApi = {
  get: jest.fn(),
  post: jest.fn(),
};
const mockAuthUser = {
  id: 'user-1',
  role: 'admin',
  company_id: 'company-1',
};

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: mockApi,
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockAuthUser,
  }),
}));

jest.mock('@/lib/useSocket', () => ({
  useSocket: jest.fn(),
}));

jest.mock('@/hooks/use-toast', () => ({
  showToast: jest.fn(),
}));

jest.mock('@/hooks/use-confirm-dialog', () => ({
  useConfirmDialog: () => ({
    confirmDialog: null,
  }),
}));

const UnificationPage = require('./UnificationPage').default;
const { useIdentityUnificationStore } = require('../stores/useIdentityUnificationStore');

const PROFILE_A = {
  id: 'profile-a',
  customer_id: 'profile-a',
  display_name: 'Amina Khan',
  primary_email: 'amina@example.test',
  primary_phone: '+923001234567',
  avatar_url: 'https://cdn.example.test/amina.jpg',
  platforms_used: 'whatsapp',
  source_channels: ['whatsapp'],
  member_count: 1,
  confidence_score: 0.8,
};

const PROFILE_B = {
  id: 'profile-b',
  customer_id: 'profile-b',
  display_name: 'Amina K.',
  primary_email: 'amina@example.test',
  primary_phone: '03001234567',
  avatar_url: 'https://cdn.example.test/amina.jpg',
  platforms_used: 'instagram',
  source_channels: ['instagram'],
  member_count: 1,
  confidence_score: 0.78,
};

const SUGGESTION = {
  id: 'suggestion-1',
  suggestion_id: 'suggestion-1',
  resolution_id: 'suggestion-1',
  source: 'auto_detect',
  match_score: 0.91,
  confidence: 0.91,
  match_reasons: 'phone, email, name, profile_picture',
  matched_fields: ['phone', 'email', 'name', 'profile_picture'],
  source_channels: ['whatsapp', 'instagram'],
  candidate_a: {
    customer_id: 'profile-a',
    display_name: 'Amina Khan',
    email: 'amina@example.test',
    phone: '+923001234567',
    avatar_url: 'https://cdn.example.test/amina.jpg',
    source_channels: ['whatsapp'],
  },
  candidate_b: {
    customer_id: 'profile-b',
    display_name: 'Amina K.',
    email: 'amina@example.test',
    phone: '03001234567',
    avatar_url: 'https://cdn.example.test/amina.jpg',
    source_channels: ['instagram'],
  },
  status: 'pending',
};

function resetStore() {
  useIdentityUnificationStore.setState({
    tenantContext: {
      tenantId: '',
      userRole: 'company_agent',
    },
    profiles: [],
    profileDetails: {},
    profileMeta: {},
    reviewQueue: [],
    selectedProfileIds: [],
    lastOperation: null,
    errorMessage: '',
    loadingProfiles: false,
    loadingDetails: false,
    loadingReviewQueue: false,
    actionInFlight: false,
  });
}

function installApiMocks() {
  mockApi.get.mockImplementation((url) => {
    if (url === '/identity/profiles') {
      return Promise.resolve({ data: [PROFILE_A, PROFILE_B] });
    }
    if (url === '/identity/suggestions') {
      return Promise.resolve({ data: [SUGGESTION] });
    }
    return Promise.resolve({ data: {} });
  });
  mockApi.post.mockImplementation((url) => {
    if (url === '/identity/auto-detect') {
      return Promise.resolve({ data: { status: 'ok', new_suggestions: 1, processed_customers: 2 } });
    }
    if (url === '/identity/suggestions/suggestion-1/resolve') {
      return Promise.resolve({ data: { status: 'ok', suggestion_id: 'suggestion-1' } });
    }
    return Promise.resolve({ data: {} });
  });
}

async function tick() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForText(container, text) {
  for (let i = 0; i < 40; i += 1) {
    if (container.textContent.includes(text)) return;
    await tick();
  }
  throw new Error(`Expected text ${text}`);
}

async function renderPage() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<UnificationPage />);
  });
  await waitForText(container, 'Amina Khan');
  await waitForText(container, '91% match');
  return { container, root };
}

beforeEach(() => {
  resetStore();
  installApiMocks();
});

afterEach(() => {
  jest.clearAllMocks();
  document.body.innerHTML = '';
});

test('Unification page focuses on identity resolution without backend auth request details', async () => {
  const { container, root } = await renderPage();

  expect(container.textContent).not.toContain('Authenticated Request Scope');
  expect(container.textContent).not.toContain('Tenant ID');
  expect(container.textContent).not.toContain('Auth Path');
  expect(container.textContent).not.toContain('backend identity contract');

  const refresh = Array.from(container.querySelectorAll('button')).find((button) => button.textContent.includes('Refresh Data'));
  const autoDetect = Array.from(container.querySelectorAll('button')).find((button) => button.textContent.includes('Auto Detect Matches'));

  expect(refresh.className).toContain('px-5');
  expect(refresh.className).toContain('text-sm');
  expect(autoDetect.className).toContain('px-5');
  expect(autoDetect.className).toContain('text-sm');

  await act(async () => root.unmount());
});

test('Auto Detect calls backend matching endpoint and renders side-by-side candidates with signals', async () => {
  const { container, root } = await renderPage();

  const autoDetect = Array.from(container.querySelectorAll('button')).find((button) => button.textContent.includes('Auto Detect Matches'));
  await act(async () => {
    autoDetect.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  expect(mockApi.post).toHaveBeenCalledWith('/identity/auto-detect', {});

  const candidate = container.querySelector('[data-testid="merge-candidate-suggestion-1"]');
  expect(candidate).not.toBeNull();
  expect(candidate.textContent).toContain('Profile A');
  expect(candidate.textContent).toContain('Profile B');
  expect(candidate.textContent).toContain('91% match');
  expect(candidate.textContent).toContain('Phone');
  expect(candidate.textContent).toContain('Email');
  expect(candidate.textContent).toContain('Avatar');
  expect(candidate.textContent).toContain('Whatsapp');
  expect(candidate.textContent).toContain('Instagram');
  expect(
    Array.from(candidate.querySelectorAll('div')).some((node) =>
      String(node.className || '').includes('md:grid-cols-[minmax(0,1fr),auto,minmax(0,1fr)]'),
    ),
  ).toBe(true);

  await act(async () => root.unmount());
});

test('Merge and Skip actions resolve the selected suggestion', async () => {
  const { container, root } = await renderPage();
  const buttons = Array.from(container.querySelectorAll('button'));
  const merge = buttons.find((button) => button.textContent.trim() === 'Merge');

  await act(async () => {
    merge.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  expect(mockApi.post).toHaveBeenCalledWith('/identity/suggestions/suggestion-1/resolve', {
    suggestion_id: 'suggestion-1',
    resolution_id: 'suggestion-1',
    action: 'accept',
    notes: null,
  });

  await tick();
  const refreshedSkip = Array.from(container.querySelectorAll('button')).find((button) => button.textContent.trim() === 'Skip');
  await act(async () => {
    refreshedSkip.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  expect(mockApi.post).toHaveBeenCalledWith('/identity/suggestions/suggestion-1/resolve', {
    suggestion_id: 'suggestion-1',
    resolution_id: 'suggestion-1',
    action: 'reject',
    notes: null,
  });

  await act(async () => root.unmount());
});
