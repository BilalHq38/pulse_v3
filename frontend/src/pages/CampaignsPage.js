import { useCallback, useEffect, useMemo, useState } from 'react';
import api from '@/lib/api';
import {
  AlertCircle,
  CheckCircle2,
  Mail,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  X,
} from 'lucide-react';

const STATUS_STYLES = {
  draft:      { label: 'Draft',     cls: 'bg-slate-50 text-slate-500 border-slate-200' },
  queued:     { label: 'Queued',    cls: 'bg-amber-50 text-amber-600 border-amber-200' },
  sending:    { label: 'Sending',   cls: 'bg-blue-50 text-blue-600 border-blue-200' },
  completed:  { label: 'Completed', cls: 'bg-emerald-50 text-emerald-600 border-emerald-200' },
  failed:     { label: 'Failed',    cls: 'bg-red-50 text-red-500 border-red-200' },
  cancelled:  { label: 'Cancelled', cls: 'bg-slate-50 text-slate-400 border-slate-200' },
};

const DEFAULT_FILTERS = {
  audience: 'both',
  lifecycle_stage: '',
  tags: '',
  source: '',
  channel: '',
};

function toFiltersPayload(f) {
  const out = {};
  if (f.audience && f.audience !== 'both') out.audience = f.audience;
  if (f.lifecycle_stage) out.lifecycle_stage = f.lifecycle_stage.split(',').map((s) => s.trim()).filter(Boolean);
  if (f.tags) out.tags = f.tags.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (f.source) out.source = f.source.split(',').map((s) => s.trim()).filter(Boolean);
  if (f.channel) out.channel = f.channel.split(',').map((s) => s.trim()).filter(Boolean);
  return out;
}

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', subject: '', body: '', html_body: '' });
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sendNow, setSendNow] = useState(true);
  const [notice, setNotice] = useState('');

  const loadCampaigns = useCallback(async () => {
    setLoadError('');
    try {
      const res = await api.get('/campaigns');
      setCampaigns(Array.isArray(res.data) ? res.data : []);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setLoadError(detail || 'Failed to load campaigns');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadCampaigns(); }, [loadCampaigns]);
  useEffect(() => {
    // Light polling so sending/queued campaigns show progress.
    const t = setInterval(() => {
      if (document.hidden) return;
      loadCampaigns();
    }, 8000);
    return () => clearInterval(t);
  }, [loadCampaigns]);

  const runPreview = useCallback(async () => {
    setPreviewLoading(true);
    try {
      const res = await api.post('/campaigns/preview', { filters: toFiltersPayload(filters) });
      setPreview(res.data);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setPreview({ error: detail || 'Preview failed' });
    } finally {
      setPreviewLoading(false);
    }
  }, [filters]);

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!form.subject.trim() || !form.body.trim()) {
      setNotice('Subject and body are required');
      return;
    }
    setSubmitting(true);
    setNotice('');
    try {
      const payload = {
        name: form.name || form.subject,
        subject: form.subject,
        body: form.body,
        html_body: form.html_body,
        filters: toFiltersPayload(filters),
        send_now: sendNow,
      };
      await api.post('/campaigns', payload);
      setShowForm(false);
      setForm({ name: '', subject: '', body: '', html_body: '' });
      setFilters(DEFAULT_FILTERS);
      setPreview(null);
      setNotice('Campaign created' + (sendNow ? ' and queued for delivery.' : '.'));
      loadCampaigns();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setNotice(detail || 'Failed to create campaign');
    } finally {
      setSubmitting(false);
    }
  };

  const sendCampaign = async (id) => {
    try {
      await api.post(`/campaigns/${id}/send`);
      loadCampaigns();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      alert(detail || 'Failed to queue campaign');
    }
  };

  const deleteCampaign = async (id) => {
    if (!window.confirm('Delete this campaign and its recipient list?')) return;
    try {
      await api.delete(`/campaigns/${id}`);
      loadCampaigns();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      alert(detail || 'Failed to delete campaign');
    }
  };

  const totals = useMemo(() => {
    const sent = campaigns.reduce((a, c) => a + (c.sent_count || 0), 0);
    const failed = campaigns.reduce((a, c) => a + (c.failed_count || 0), 0);
    const recipients = campaigns.reduce((a, c) => a + (c.total_recipients || 0), 0);
    return { sent, failed, recipients };
  }, [campaigns]);

  return (
    <div className="min-h-screen bg-slate-50/60">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-10">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
              <Mail size={22} className="text-sky-500" /> Email Campaigns
            </h1>
            <p className="text-sm text-slate-500 mt-1">
              Send bulk email to filtered leads and customers. Reuses your existing Brevo/SMTP configuration.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={loadCampaigns}
              className="p-2 rounded-lg border border-slate-200 text-slate-500 hover:text-slate-700 hover:bg-slate-50"
              aria-label="Refresh"
              data-testid="campaigns-refresh"
            >
              <RefreshCw size={14} />
            </button>
            <button
              onClick={() => { setShowForm(true); setNotice(''); }}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              data-testid="new-campaign-btn"
            >
              <Plus size={14} /> New campaign
            </button>
          </div>
        </div>

        {notice && (
          <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700 px-3 py-2 text-sm inline-flex items-center gap-2">
            <CheckCircle2 size={14} /> {notice}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
          <StatCard label="Campaigns" value={campaigns.length} />
          <StatCard label="Recipients queued" value={totals.recipients} />
          <StatCard label="Sent / Failed" value={`${totals.sent} / ${totals.failed}`} />
        </div>

        {loadError && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 text-red-600 px-3 py-2 text-sm inline-flex items-center gap-2">
            <AlertCircle size={14} /> {loadError}
          </div>
        )}

        <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden">
          <div className="grid grid-cols-12 gap-2 px-4 py-2 bg-slate-50/80 text-[11px] uppercase tracking-wide text-slate-400 font-medium">
            <div className="col-span-5">Campaign</div>
            <div className="col-span-2">Status</div>
            <div className="col-span-1 text-right">Recipients</div>
            <div className="col-span-1 text-right">Sent</div>
            <div className="col-span-1 text-right">Failed</div>
            <div className="col-span-2 text-right">Actions</div>
          </div>

          {loading ? (
            <div className="px-4 py-10 text-center text-sm text-slate-400">Loading…</div>
          ) : campaigns.length === 0 ? (
            <div className="px-4 py-14 text-center">
              <Mail size={28} className="mx-auto text-slate-300 mb-2" />
              <p className="text-sm text-slate-500">No campaigns yet.</p>
              <p className="text-xs text-slate-400 mt-1">Click <span className="font-medium">New campaign</span> to compose your first email blast.</p>
            </div>
          ) : (
            campaigns.map((c) => {
              const s = STATUS_STYLES[c.status] || STATUS_STYLES.draft;
              return (
                <div
                  key={c.id}
                  className="grid grid-cols-12 gap-2 px-4 py-3 border-t border-slate-100 items-center hover:bg-slate-50/60"
                  data-testid={`campaign-row-${c.id}`}
                >
                  <div className="col-span-5 min-w-0">
                    <div className="text-sm font-medium text-slate-800 truncate">{c.name || c.subject}</div>
                    <div className="text-[11px] text-slate-400 truncate">{c.subject}</div>
                  </div>
                  <div className="col-span-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${s.cls}`}>{s.label}</span>
                  </div>
                  <div className="col-span-1 text-right text-sm text-slate-600">{c.total_recipients || 0}</div>
                  <div className="col-span-1 text-right text-sm text-emerald-600">{c.sent_count || 0}</div>
                  <div className="col-span-1 text-right text-sm text-red-500">{c.failed_count || 0}</div>
                  <div className="col-span-2 flex items-center justify-end gap-1">
                    {(c.status === 'draft' || c.status === 'failed') && (c.total_recipients || 0) > 0 && (
                      <button
                        onClick={() => sendCampaign(c.id)}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-blue-600 text-white text-xs hover:bg-blue-700"
                        data-testid={`send-campaign-${c.id}`}
                      >
                        <Send size={11} /> Send
                      </button>
                    )}
                    <button
                      onClick={() => deleteCampaign(c.id)}
                      className="p-1.5 rounded-md border border-slate-200 text-slate-400 hover:text-red-500 hover:bg-red-50"
                      aria-label="Delete campaign"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {showForm && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-start sm:items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-2xl my-6">
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
              <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                <Mail size={16} className="text-sky-500" /> New email campaign
              </h2>
              <button
                onClick={() => setShowForm(false)}
                className="text-slate-400 hover:text-slate-600"
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <form onSubmit={submit} className="p-5 space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="Internal name (optional)">
                  <input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Q2 reactivation"
                    className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm"
                    data-testid="campaign-name"
                  />
                </Field>
                <Field label="Subject *">
                  <input
                    value={form.subject}
                    onChange={(e) => setForm({ ...form, subject: e.target.value })}
                    placeholder="A quick update from us"
                    className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm"
                    required
                    data-testid="campaign-subject"
                  />
                </Field>
              </div>

              <Field label="Plain-text body *">
                <textarea
                  value={form.body}
                  onChange={(e) => setForm({ ...form, body: e.target.value })}
                  placeholder="Write your message…"
                  rows={6}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm resize-y"
                  required
                  data-testid="campaign-body"
                />
              </Field>

              <details className="rounded-lg border border-slate-200 bg-slate-50/60">
                <summary className="px-3 py-2 text-xs font-medium text-slate-600 cursor-pointer">
                  HTML body (optional)
                </summary>
                <div className="px-3 pb-3">
                  <textarea
                    value={form.html_body}
                    onChange={(e) => setForm({ ...form, html_body: e.target.value })}
                    placeholder="<p>Rich HTML content…</p>"
                    rows={4}
                    className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-xs font-mono resize-y"
                  />
                </div>
              </details>

              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                <div className="text-xs font-semibold text-slate-600 mb-3">Audience filters</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Audience">
                    <select
                      value={filters.audience}
                      onChange={(e) => setFilters({ ...filters, audience: e.target.value })}
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="filter-audience"
                    >
                      <option value="both">Leads + customers</option>
                      <option value="leads">Leads only</option>
                      <option value="customers">Customers only</option>
                    </select>
                  </Field>
                  <Field label="Lifecycle stage (comma-sep)">
                    <input
                      value={filters.lifecycle_stage}
                      onChange={(e) => setFilters({ ...filters, lifecycle_stage: e.target.value })}
                      placeholder="lead, prospect, customer"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="filter-lifecycle"
                    />
                  </Field>
                  <Field label="Tags (comma-sep)">
                    <input
                      value={filters.tags}
                      onChange={(e) => setFilters({ ...filters, tags: e.target.value })}
                      placeholder="interested, social_lead, hot_lead"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="filter-tags"
                    />
                  </Field>
                  <Field label="Source (comma-sep)">
                    <input
                      value={filters.source}
                      onChange={(e) => setFilters({ ...filters, source: e.target.value })}
                      placeholder="facebook, instagram, web_chat"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="filter-source"
                    />
                  </Field>
                  <Field label="Channel (comma-sep)">
                    <input
                      value={filters.channel}
                      onChange={(e) => setFilters({ ...filters, channel: e.target.value })}
                      placeholder="whatsapp, email"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="filter-channel"
                    />
                  </Field>
                </div>

                <div className="mt-3 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={runPreview}
                    disabled={previewLoading}
                    className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                    data-testid="campaign-preview-btn"
                  >
                    {previewLoading ? 'Previewing…' : 'Preview recipients'}
                  </button>
                  {preview && !preview.error && (
                    <span className="text-xs text-emerald-600 font-medium" data-testid="campaign-preview-count">
                      {preview.count} matching recipient{preview.count === 1 ? '' : 's'}
                    </span>
                  )}
                  {preview?.error && (
                    <span className="text-xs text-red-500 font-medium">{preview.error}</span>
                  )}
                </div>
              </div>

              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={sendNow}
                  onChange={(e) => setSendNow(e.target.checked)}
                />
                Start sending immediately after save
              </label>

              {notice && !showForm && (
                <div className="text-xs text-red-500">{notice}</div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  className="px-3 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                  data-testid="campaign-submit"
                >
                  {submitting ? 'Saving…' : (
                    <>
                      <Send size={14} />
                      {sendNow ? 'Save & send' : 'Save draft'}
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block text-xs text-slate-500">
      <span className="block mb-1 font-medium">{label}</span>
      {children}
    </label>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="bg-white border border-slate-100 rounded-xl px-4 py-3">
      <p className="text-[11px] text-slate-400 uppercase tracking-wide">{label}</p>
      <p className="text-xl font-bold text-slate-900 mt-0.5">{value}</p>
    </div>
  );
}
