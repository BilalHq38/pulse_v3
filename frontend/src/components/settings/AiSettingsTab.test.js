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
  put: jest.fn(),
  delete: jest.fn(),
};

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: mockApi,
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

const AiSettingsTab = require('./AiSettingsTab').default;

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

function changeSelect(select, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  setter.call(select, value);
  select.dispatchEvent(new Event('input', { bubbles: true }));
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

function renderTab(overrides = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  const selected = {
    id: 'llm-default',
    company_id: '',
    provider: 'gemini',
    model_name: 'gemini-2.5-flash',
    temperature: 0.7,
    max_tokens: 2048,
    is_selected: true,
    provider_ready: true,
    supports_text: true,
    supports_vision: true,
    supports_audio: true,
    capabilities: ['Text', 'Vision', 'Audio'],
  };
  const props = {
    company: { ai_enabled: true },
    setCompany: jest.fn(),
    saveCompany: jest.fn(),
    saving: '',
    setSaving: jest.fn(),
    llmEngines: [selected],
    selectedLlmEngine: selected,
    refreshAiConfig: jest.fn(),
    aiConfigError: '',
    aiAgents: [],
    setAiAgents: jest.fn(),
    editingLlmId: null,
    setEditingLlmId: jest.fn(),
    llmDraft: {},
    setLlmDraft: jest.fn(),
    showAddLlmForm: false,
    setShowAddLlmForm: jest.fn(),
    addLlmForm: { provider: 'gemini', model_category: 'text_generation', model_name: 'gemini-2.5-flash', temperature: 0.7, max_tokens: 2048 },
    setAddLlmForm: jest.fn(),
    editingAgentId: null,
    setEditingAgentId: jest.fn(),
    agentDraft: {},
    setAgentDraft: jest.fn(),
    showAddAgentForm: false,
    setShowAddAgentForm: jest.fn(),
    addAgentForm: {},
    setAddAgentForm: jest.fn(),
    isAdmin: true,
    ...overrides,
  };
  act(() => {
    root.render(<AiSettingsTab {...props} />);
  });
  return { container, root, props };
}

beforeEach(() => {
  mockApi.get.mockImplementation((url) => {
    if (url.startsWith('/orchestrator/executions')) return Promise.resolve({ data: { executions: [] } });
    if (url.startsWith('/ai/sessions')) return Promise.resolve({ data: [] });
    if (url === '/mcp/servers') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
});

afterEach(() => {
  jest.clearAllMocks();
  document.body.innerHTML = '';
});

test('LLM Engines renders a single default Gemini 2.5 Flash engine with capability badges', async () => {
  const { container, root } = renderTab();

  await tick();
  await waitForSelector('[data-testid="llm-engine-card"]', container);
  expect(container.querySelectorAll('[data-testid="llm-engine-card"]')).toHaveLength(1);
  expect(container.textContent).toContain('gemini-2.5-flash');
  expect(container.textContent).toContain('Text');
  expect(container.textContent).toContain('Vision');
  expect(container.textContent).toContain('Audio');

  await act(async () => root.unmount());
});

test('Add Engine dropdown exposes Vertex AI and supported Gemini models', async () => {
  const StateHarness = () => {
    const [showAdd, setShowAdd] = React.useState(false);
    const [form, setForm] = React.useState({
      provider: 'gemini',
      model_category: 'text_generation',
      model_name: 'gemini-2.5-flash',
      temperature: 0.7,
      max_tokens: 2048,
    });
    return (
      <AiSettingsTab
        company={{ ai_enabled: true }}
        setCompany={jest.fn()}
        saveCompany={jest.fn()}
        saving=""
        setSaving={jest.fn()}
        llmEngines={[]}
        selectedLlmEngine={null}
        refreshAiConfig={jest.fn()}
        aiAgents={[]}
        setAiAgents={jest.fn()}
        editingLlmId={null}
        setEditingLlmId={jest.fn()}
        llmDraft={{}}
        setLlmDraft={jest.fn()}
        showAddLlmForm={showAdd}
        setShowAddLlmForm={setShowAdd}
        addLlmForm={form}
        setAddLlmForm={setForm}
        editingAgentId={null}
        setEditingAgentId={jest.fn()}
        agentDraft={{}}
        setAgentDraft={jest.fn()}
        showAddAgentForm={false}
        setShowAddAgentForm={jest.fn()}
        addAgentForm={{}}
        setAddAgentForm={jest.fn()}
        isAdmin
      />
    );
  };

  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<StateHarness />);
  });
  await tick();

  const addButton = await waitForSelector('[data-testid="add-llm-engine-btn"]', container);
  await act(async () => {
    addButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

  const modelSelect = await waitForSelector('[data-testid="llm-add-model"]', container);
  const providerSelect = await waitForSelector('[data-testid="llm-add-provider"]', container);
  expect(providerSelect.textContent).toContain('Vertex AI Gemini');
  expect(providerSelect.textContent).toContain('Gemini Developer API');
  expect(modelSelect.textContent).toContain('Vertex AI Gemini 2.5 Flash Lite (Default)');
  expect(modelSelect.textContent).toContain('Vertex AI Gemini 2.5 Flash');
  expect(modelSelect.textContent).toContain('Vertex AI Gemini 2.5 Pro (Recommended)');
  expect(modelSelect.textContent).not.toContain('Gemini 3');
  expect(modelSelect.textContent).not.toContain('Gemma');
  expect(modelSelect.textContent).not.toContain('Gemini 1.5');
  expect(modelSelect.textContent).not.toContain('Gemini 1.0');

  await act(async () => root.unmount());
});
