import { useMemo, useState } from 'react';
import ChatWidget from '@/components/ChatWidget/ChatWidget';

function useDefaultTenant() {
  const envTenant =
    (typeof process !== 'undefined' && process.env && process.env.REACT_APP_DEFAULT_TENANT_ID) || '';
  const queryTenant = useMemo(() => {
    try {
      return new URLSearchParams(window.location.search).get('company_id') || '';
    } catch {
      return '';
    }
  }, []);
  return (queryTenant || envTenant || '').trim();
}

export default function WidgetDemoPage() {
  const defaultTenant = useDefaultTenant();
  const [companyId, setCompanyId] = useState(defaultTenant);
  const [confirmed, setConfirmed] = useState(Boolean(defaultTenant));

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="max-w-4xl mx-auto px-6 py-16">
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-medium mb-4">
            Live Demo
          </div>
          <h1 className="text-4xl font-bold text-slate-900 mb-3">Pulse Engine Chat Widget</h1>
          <p className="text-lg text-slate-600 max-w-2xl mx-auto">
            A floating chat widget wired to the Pulse Engine agent orchestrator. Messages flow through
            Capture &rarr; Qualification &rarr; Support with long-term memory and identity unification.
          </p>
        </div>

        {!confirmed ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (companyId.trim()) setConfirmed(true);
            }}
            className="max-w-md mx-auto bg-white rounded-2xl shadow-sm border border-slate-200 p-6"
          >
            <label className="block text-sm font-medium text-slate-700 mb-2">
              Tenant / Company ID
            </label>
            <input
              type="text"
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              placeholder="e.g. demo-tenant"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
            <p className="text-xs text-slate-500 mt-2">
              This is passed to <code>/api/webhooks/web-chat</code> as <code>company_id</code>. Set{' '}
              <code>REACT_APP_DEFAULT_TENANT_ID</code> to skip this step.
            </p>
            <button
              type="submit"
              className="mt-4 w-full bg-blue-600 text-white font-medium py-2 rounded-lg hover:bg-blue-700"
            >
              Launch widget
            </button>
          </form>
        ) : (
          <div className="grid md:grid-cols-3 gap-4">
            <FeatureCard
              title="Capture"
              body="New conversations auto-create customers + leads with phone/email normalization."
            />
            <FeatureCard
              title="Adaptive Qualify"
              body="Agent asks budget / timeline / need / role incrementally until scoring is ready."
            />
            <FeatureCard
              title="Memory Support"
              body="Replies reuse long-term memory and knowledge base; escalates below confidence threshold."
            />
          </div>
        )}

        {confirmed && (
          <div className="mt-10 text-center text-sm text-slate-500">
            Click the blue bubble in the bottom-right to start chatting.
          </div>
        )}
      </div>

      {confirmed && <ChatWidget companyId={companyId} defaultOpen />}
    </div>
  );
}

function FeatureCard({ title, body }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <div className="text-sm font-semibold text-blue-600 uppercase tracking-wide mb-2">{title}</div>
      <div className="text-slate-700 text-sm leading-relaxed">{body}</div>
    </div>
  );
}
