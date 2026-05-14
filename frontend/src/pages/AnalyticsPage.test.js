const { TextDecoder, TextEncoder } = require('util');

global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;
global.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;

const mockApi = {
  get: jest.fn(),
};

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: mockApi,
}));

jest.mock('recharts', () => {
  const React = require('react');
  const MockChart = ({ children }) => React.createElement('div', null, children);
  const MockLeaf = () => React.createElement('div', null);
  return {
    BarChart: MockChart,
    Bar: MockChart,
    LineChart: MockChart,
    Line: MockLeaf,
    PieChart: MockChart,
    Pie: MockChart,
    Cell: MockLeaf,
    ResponsiveContainer: MockChart,
    XAxis: MockLeaf,
    YAxis: MockLeaf,
    Tooltip: MockLeaf,
    CartesianGrid: MockLeaf,
    Legend: MockLeaf,
  };
});

const AnalyticsPage = require('./AnalyticsPage').default;

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

function findSection(container, title) {
  const heading = Array.from(container.querySelectorAll('h4')).find((node) => node.textContent === title);
  if (!heading) throw new Error(`Missing section ${title}`);
  return heading.parentElement;
}

function installApiMocks() {
  mockApi.get.mockImplementation((url) => {
    const dataByUrl = {
      '/analytics/overview': {
        total_conversations: 0,
        ai_resolution_rate: 0,
        csat_score: 0,
        nps_score: 0,
        total_leads: 1,
        total_customers: 1,
        total_products: 0,
      },
      '/analytics/conversations': [],
      '/analytics/leads': { by_source: {}, by_grade: {} },
      '/analytics/sentiment': [],
      '/analytics/agents': [],
      '/analytics/customer-summaries': [
        {
          id: 'customer-1',
          entity_type: 'customer',
          source_type: 'customer_interaction_summary',
          type: 'Customer',
          name: 'Customer Only',
          date: '2026-05-14',
          sentiment_label: 'positive',
          resolution_status: 'resolved',
          topics: ['AI handled'],
        },
        {
          id: 'lead-1',
          entity_type: 'lead',
          source_type: 'lead_activity',
          type: 'Lead',
          name: 'Lead Only',
          date: '2026-05-14',
          sentiment_label: 'neutral',
          resolution_status: 'new',
          topics: ['nurture'],
        },
      ],
      '/analytics/daily-summaries': [],
      '/ai/sessions': [],
      '/analytics/reports': [],
    };
    return Promise.resolve({ data: dataByUrl[url] || [] });
  });
}

afterEach(() => {
  jest.clearAllMocks();
  document.body.innerHTML = '';
});

test('interaction summaries render customer and lead records in separate sections by entity type', async () => {
  installApiMocks();
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(<AnalyticsPage />);
  });

  await waitForSelector('[data-testid="customer-interaction-log"]', container);
  const customerSection = findSection(container, 'Customer Interaction Summary');
  const leadSection = findSection(container, 'Lead Interaction Summary');

  expect(customerSection.textContent).toContain('Customer Only');
  expect(customerSection.textContent).not.toContain('Lead Only');
  expect(leadSection.textContent).toContain('Lead Only');
  expect(leadSection.textContent).not.toContain('Customer Only');

  await act(async () => root.unmount());
});
