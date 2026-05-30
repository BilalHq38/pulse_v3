import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api from '@/lib/api';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import {
  AlertCircle,
  Pencil,
  Mail,
  Package,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Trash2,
  Users,
  Wand2,
  X,
} from 'lucide-react';

const STATUS_STYLES = {
  draft:      { label: 'Draft',     cls: 'bg-slate-50 text-slate-500 border-slate-200' },
  queued:     { label: 'Queued',    cls: 'bg-amber-50 text-amber-600 border-amber-200' },
  sending:    { label: 'Sending',   cls: 'bg-blue-50 text-blue-600 border-blue-200' },
  paused:     { label: 'Paused',    cls: 'bg-orange-50 text-orange-600 border-orange-200' },
  completed:  { label: 'Completed', cls: 'bg-emerald-50 text-emerald-600 border-emerald-200' },
  failed:     { label: 'Failed',    cls: 'bg-red-50 text-red-500 border-red-200' },
  cancelled:  { label: 'Cancelled', cls: 'bg-slate-50 text-slate-400 border-slate-200' },
};

const DEFAULT_FILTERS = {
  audience: 'both',
  channel: '',
};
const LONG_REQUEST_TIMEOUT_MS = 120000;

function toFiltersPayload(
  f,
  selectedLeadIds = [],
  selectedCustomerIds = [],
  productId = '',
  aiDetails = null,
  htmlAiPrompt = '',
) {
  const out = {};
  if (f.audience && f.audience !== 'both') out.audience = f.audience;
  if (f.channel) out.channel = f.channel.split(',').map((s) => s.trim()).filter(Boolean);
  if (selectedLeadIds.length) out.selected_lead_ids = selectedLeadIds;
  if (selectedCustomerIds.length) out.selected_customer_ids = selectedCustomerIds;
  if (productId) out.product_id = productId;
  if (aiDetails && typeof aiDetails === 'object') {
    const savedAiDetails = Object.fromEntries(
      Object.entries(aiDetails)
        .map(([key, value]) => [key, String(value || '').trim()])
        .filter(([, value]) => value),
    );
    if (Object.keys(savedAiDetails).length) out.ai_details = savedAiDetails;
  }
  const savedHtmlPrompt = String(htmlAiPrompt || '').trim();
  if (savedHtmlPrompt) out.html_ai_prompt = savedHtmlPrompt;
  return out;
}

const EMPTY_AI_DETAILS = {
  campaign_goal: '',
  audience_description: '',
  tone: 'friendly',
  call_to_action: '',
  offer_details: '',
  extra_context: '',
};

const AI_EXHAUSTION_STORAGE_KEY = 'pulse:campaign-ai-exhausted';
const AI_EXHAUSTION_WAIT_MS = 30 * 60 * 1000;
const AI_EXHAUSTION_WARNING = 'AI/API quota has been exhausted. Please wait 30 minutes. AI responses are stopped and this campaign must be handled manually.';

const getListPayload = (data) => {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  return [];
};

const parseJsonObject = (value) => {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

const isAiExhaustionError = (err) => err?.response?.data?.detail?.ai_enabled === false;

const aiExhaustionActive = () => {
  try {
    const exhaustedAt = Number(localStorage.getItem(AI_EXHAUSTION_STORAGE_KEY) || 0);
    if (!exhaustedAt) return false;
    if (Date.now() - exhaustedAt < AI_EXHAUSTION_WAIT_MS) return true;
    localStorage.removeItem(AI_EXHAUSTION_STORAGE_KEY);
    return false;
  } catch {
    return false;
  }
};

export default function CampaignsPage() {
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editingCampaignId, setEditingCampaignId] = useState('');
  const [form, setForm] = useState({ name: '', subject: '', body: '', html_body: '', product_id: '' });
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [products, setProducts] = useState([]);
  const [leads, setLeads] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [selectedLeadIds, setSelectedLeadIds] = useState([]);
  const [selectedCustomerIds, setSelectedCustomerIds] = useState([]);
  const [aiDetails, setAiDetails] = useState(EMPTY_AI_DETAILS);
  const [generatingCopy, setGeneratingCopy] = useState(false);
  const [htmlAiPrompt, setHtmlAiPrompt] = useState('');
  const [generatingHtmlBody, setGeneratingHtmlBody] = useState(false);
  const [aiGenerationError, setAiGenerationError] = useState('');
  const [aiDisabled, setAiDisabled] = useState(() => aiExhaustionActive());
  const [resourceError, setResourceError] = useState('');
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sendNow, setSendNow] = useState(true);
  const [campaignImages, setCampaignImages] = useState([]);
  const campaignImageInputRef = useRef(null);

  const resetComposer = useCallback(() => {
    setEditingCampaignId('');
    setShowForm(false);
    setForm({ name: '', subject: '', body: '', html_body: '', product_id: '' });
    setFilters(DEFAULT_FILTERS);
    setSelectedLeadIds([]);
    setSelectedCustomerIds([]);
    setAiDetails(EMPTY_AI_DETAILS);
    setHtmlAiPrompt('');
    setAiGenerationError('');
    setPreview(null);
    setSendNow(true);
    setCampaignImages([]);
  }, []);

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

  const loadCampaignResources = useCallback(async () => {
    setResourceError('');
    const [productRes, leadRes, customerRes] = await Promise.allSettled([
      api.get('/company-data/products', { params: { page_size: 500 } }),
      api.get('/leads', { params: { limit: 100 } }),
      api.get('/customers'),
    ]);
    if (productRes.status === 'fulfilled') {
      setProducts(getListPayload(productRes.value.data));
    } else {
      setProducts([]);
      setResourceError('Products could not be loaded. Check product access and refresh data.');
    }
    setLeads(leadRes.status === 'fulfilled' ? getListPayload(leadRes.value.data).filter((lead) => lead.email) : []);
    setCustomers(customerRes.status === 'fulfilled' ? getListPayload(customerRes.value.data).filter((customer) => customer.email) : []);
  }, []);

  useEffect(() => { loadCampaignResources(); }, [loadCampaignResources]);

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
      const res = await api.post('/campaigns/preview', {
        filters: toFiltersPayload(filters, selectedLeadIds, selectedCustomerIds, form.product_id),
      });
      setPreview(res.data);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setPreview({ error: detail || 'Preview failed' });
    } finally {
      setPreviewLoading(false);
    }
  }, [filters, form.product_id, selectedCustomerIds, selectedLeadIds]);

  const toggleLead = (id) => {
    setSelectedLeadIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const toggleCustomer = (id) => {
    setSelectedCustomerIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const toggleAllLeads = (checked) => {
    setSelectedLeadIds(checked ? leads.slice(0, 80).map((lead) => lead.id).filter(Boolean) : []);
  };

  const toggleAllCustomers = (checked) => {
    setSelectedCustomerIds(checked ? customers.slice(0, 80).map((customer) => customer.id).filter(Boolean) : []);
  };

  const handleCampaignImageAdd = (e) => {
    const files = Array.from(e.target.files || []);
    const imageFiles = files.filter(f => f.type.startsWith('image/'));
    imageFiles.forEach(file => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        setCampaignImages(prev => [...prev, { name: file.name, dataUrl: ev.target.result, size: file.size }]);
      };
      reader.readAsDataURL(file);
    });
    e.target.value = '';
  };

  const aiGenerationReady = Boolean(
    form.product_id
    && aiDetails.campaign_goal.trim()
    && aiDetails.audience_description.trim()
    && aiDetails.tone.trim()
    && aiDetails.call_to_action.trim(),
  );

  const stopAiForExhaustion = useCallback((message = AI_EXHAUSTION_WARNING) => {
    let alreadyWarned = false;
    try {
      alreadyWarned = aiExhaustionActive();
      localStorage.setItem(AI_EXHAUSTION_STORAGE_KEY, String(Date.now()));
    } catch {
      alreadyWarned = aiDisabled;
    }
    setAiDisabled(true);
    setAiGenerationError(message);
    if (!alreadyWarned) {
      showToast({
        type: 'warning',
        title: 'AI quota exhausted',
        message,
        dedupeKey: 'campaign-ai-quota-exhausted',
      });
    }
  }, [aiDisabled]);

  const generateCampaignCopy = async () => {
    if (aiDisabled) {
      if (!aiExhaustionActive()) {
        setAiDisabled(false);
      } else {
        setAiGenerationError(AI_EXHAUSTION_WARNING);
        return;
      }
    }
    if (!aiGenerationReady) {
      showToast({
        type: 'error',
        title: 'Details Missing',
        message: 'Select a product and complete the required campaign details first.',
      });
      return;
    }
    setGeneratingCopy(true);
    setAiGenerationError('');
    try {
      const res = await api.post('/campaigns/generate', {
        product_id: form.product_id,
        ...aiDetails,
      }, { timeout: LONG_REQUEST_TIMEOUT_MS });
      setForm((prev) => ({
        ...prev,
        subject: res.data?.subject || prev.subject,
        body: res.data?.body || prev.body,
        html_body: res.data?.html_body || prev.html_body,
      }));
      showToast({
        type: 'success',
        title: 'Copy Ready',
        message: `AI created draft copy for ${form.name || form.subject || 'this campaign'}.`,
      });
    } catch (err) {
      const message = getErrorMessage(err, 'AI/API issue: campaign copy could not be generated. Please write or paste the response manually.');
      if (isAiExhaustionError(err)) {
        stopAiForExhaustion(message || AI_EXHAUSTION_WARNING);
        return;
      }
      setAiGenerationError(message);
      showToast({
        type: 'error',
        title: 'Copy Failed',
        message,
      });
    } finally {
      setGeneratingCopy(false);
    }
  };

  const generateHtmlBody = async () => {
    if (aiDisabled) {
      if (!aiExhaustionActive()) {
        setAiDisabled(false);
      } else {
        setAiGenerationError(AI_EXHAUSTION_WARNING);
        return;
      }
    }
    const description = htmlAiPrompt.trim();
    if (!description) {
      setAiGenerationError('Describe what the AI should create for the HTML email body.');
      return;
    }
    setGeneratingHtmlBody(true);
    setAiGenerationError('');
    try {
      const res = await api.post('/campaigns/generate-html-body', {
        product_id: form.product_id,
        description,
        current_body: form.body,
      }, { timeout: LONG_REQUEST_TIMEOUT_MS });
      setForm((prev) => ({
        ...prev,
        html_body: res.data?.html_body || prev.html_body,
      }));
      showToast({
        type: 'success',
        title: 'HTML Body Ready',
        message: 'AI inserted the generated HTML body.',
      });
    } catch (err) {
      const message = getErrorMessage(err, 'AI/API issue: HTML email body could not be generated. Please write or paste the response manually.');
      if (isAiExhaustionError(err)) {
        stopAiForExhaustion(message || AI_EXHAUSTION_WARNING);
        return;
      }
      setAiGenerationError(message);
      showToast({
        type: 'error',
        title: 'AI Unavailable',
        message,
      });
    } finally {
      setGeneratingHtmlBody(false);
    }
  };

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!form.subject.trim() || !(form.body.trim() || form.html_body.trim())) {
      showToast({
        type: 'error',
        title: 'Content Missing',
        message: 'Add a subject and either plain text or HTML before saving the campaign.',
      });
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        name: form.name || form.subject,
        subject: form.subject,
        body: form.body,
        html_body: form.html_body,
        filters: toFiltersPayload(filters, selectedLeadIds, selectedCustomerIds, form.product_id, aiDetails, htmlAiPrompt),
        send_now: sendNow,
        attachments: campaignImages.map(img => ({ type: 'image', name: img.name, data_url: img.dataUrl })),
      };
      const res = editingCampaignId
        ? await api.put(`/campaigns/${editingCampaignId}`, payload)
        : await api.post('/campaigns', payload);
      if (sendNow && (res.data?.id || editingCampaignId)) {
        await api.post(`/campaigns/${res.data?.id || editingCampaignId}/send`, {}, { timeout: LONG_REQUEST_TIMEOUT_MS });
      }
      resetComposer();
      const recipientCount = preview?.count ?? 0;
      const campaignName = form.name || form.subject || 'Campaign';
      showToast({
        type: 'success',
        title: sendNow ? 'Campaign Queued' : (editingCampaignId ? 'Draft Updated' : 'Draft Saved'),
        message: sendNow
          ? `${campaignName} is queued for ${recipientCount} recipient${recipientCount === 1 ? '' : 's'}.`
          : `${campaignName} was saved${recipientCount ? ` with ${recipientCount} planned recipient${recipientCount === 1 ? '' : 's'}` : ''}.`,
      });
      await loadCampaigns();
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save that campaign.'),
      });
    } finally {
      setSubmitting(false);
    }
  };

  const sendCampaign = async (id) => {
    const campaign = campaigns.find((item) => item.id === id);
    try {
      await api.post(`/campaigns/${id}/send`, {}, { timeout: LONG_REQUEST_TIMEOUT_MS });
      loadCampaigns();
      const recipientCount = campaign?.total_recipients || 0;
      showToast({
        type: 'success',
        title: 'Campaign Queued',
        message: `${campaign?.name || campaign?.subject || 'Campaign'} is queued for ${recipientCount} recipient${recipientCount === 1 ? '' : 's'}.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Send Failed',
        message: getErrorMessage(err, 'We could not queue that campaign.'),
      });
    }
  };

  const pauseCampaign = async (id) => {
    try {
      await api.post(`/campaigns/${id}/pause`);
      loadCampaigns();
      showToast({ type: 'success', title: 'Campaign Paused', message: 'The campaign has been paused.' });
    } catch (err) {
      showToast({ type: 'error', title: 'Pause Failed', message: getErrorMessage(err, 'We could not pause that campaign.') });
    }
  };

  const resumeCampaign = async (id) => {
    try {
      await api.post(`/campaigns/${id}/resume`);
      loadCampaigns();
      showToast({ type: 'success', title: 'Campaign Resumed', message: 'The campaign has been resumed.' });
    } catch (err) {
      showToast({ type: 'error', title: 'Resume Failed', message: getErrorMessage(err, 'We could not resume that campaign.') });
    }
  };

  const restartCampaign = async (id) => {
    try {
      await api.post(`/campaigns/${id}/restart`, {}, { timeout: LONG_REQUEST_TIMEOUT_MS });
      loadCampaigns();
      showToast({ type: 'success', title: 'Campaign Restarted', message: 'The campaign has been restarted from the beginning.' });
    } catch (err) {
      showToast({ type: 'error', title: 'Restart Failed', message: getErrorMessage(err, 'We could not restart that campaign.') });
    }
  };

  const openCampaignEditor = (campaign) => {
    const campaignFilters = parseJsonObject(campaign?.filters);
    setEditingCampaignId(campaign?.id || '');
    setForm({
      name: campaign?.name || '',
      subject: campaign?.subject || '',
      body: campaign?.body || '',
      html_body: campaign?.html_body || '',
      product_id: campaignFilters.product_id || '',
    });
    setFilters({
      audience: campaignFilters.audience || 'both',
      channel: Array.isArray(campaignFilters.channel) ? campaignFilters.channel.join(', ') : '',
    });
    setSelectedLeadIds(Array.isArray(campaignFilters.selected_lead_ids) ? campaignFilters.selected_lead_ids : []);
    setSelectedCustomerIds(
      Array.isArray(campaignFilters.selected_customer_ids) ? campaignFilters.selected_customer_ids : [],
    );
    setAiDetails({
      ...EMPTY_AI_DETAILS,
      ...(campaignFilters.ai_details && typeof campaignFilters.ai_details === 'object'
        ? campaignFilters.ai_details
        : {}),
    });
    setHtmlAiPrompt(String(campaignFilters.html_ai_prompt || ''));
    setPreview(null);
    setSendNow(false);
    setShowForm(true);
  };

  const deleteCampaign = async (id) => {
    const campaign = campaigns.find((item) => item.id === id);
    requestConfirmation({
      title: 'Delete Campaign',
      description: `Delete ${campaign?.name || campaign?.subject || 'this campaign'} and its recipient list. This cannot be undone.`,
      confirmLabel: 'Delete campaign',
      onConfirm: async () => {
        try {
          await api.delete(`/campaigns/${id}`);
          loadCampaigns();
          showToast({
            type: 'success',
            title: 'Campaign Deleted',
            message: `${campaign?.name || campaign?.subject || 'The campaign'} was removed.`,
          });
        } catch (err) {
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that campaign.'),
          });
        }
      },
    });
  };

  const totals = useMemo(() => {
    const sent = campaigns.reduce((a, c) => a + (c.sent_count || 0), 0);
    const failed = campaigns.reduce((a, c) => a + (c.failed_count || 0), 0);
    const recipients = campaigns.reduce((a, c) => a + (c.total_recipients || 0), 0);
    return { sent, failed, recipients };
  }, [campaigns]);

  return (
    <>
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
              onClick={() => { resetComposer(); setShowForm(true); }}
              className="inline-flex items-center gap-1.5 rounded-xl border border-blue-200 bg-blue-50 px-3.5 py-2 text-sm font-semibold text-blue-700 transition-all hover:bg-blue-100"
              data-testid="new-campaign-btn"
            >
              <Plus size={14} /> New campaign
            </button>
          </div>
        </div>

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
          <div className="hidden grid-cols-12 gap-2 px-4 py-2 bg-slate-50/80 text-[11px] uppercase tracking-wide text-slate-400 font-medium md:grid">
            <div className="col-span-4">Campaign</div>
            <div className="col-span-2">Status</div>
            <div className="col-span-1 text-right">Recipients</div>
            <div className="col-span-1 text-right">Sent</div>
            <div className="col-span-1 text-right">Failed</div>
            <div className="col-span-3 text-right">Actions</div>
          </div>

          {loading ? (
            <div className="px-4 py-10 text-center text-sm text-slate-400">Loading…</div>
          ) : campaigns.length === 0 ? (
            <div className="px-4 py-14 text-center">
              <Mail size={28} className="mx-auto text-slate-300 mb-2" />
              <p className="text-sm text-slate-500">No campaign is currently running.</p>
              <p className="text-xs text-slate-400 mt-1">Click <span className="font-medium">New campaign</span> to compose your first email blast.</p>
            </div>
          ) : (
            campaigns.map((c) => {
              const s = STATUS_STYLES[c.status] || STATUS_STYLES.draft;
              return (
                <div
                  key={c.id}
                  className="grid grid-cols-1 gap-3 px-4 py-3 border-t border-slate-100 hover:bg-slate-50/60 md:grid-cols-12 md:gap-2 md:items-center"
                  data-testid={`campaign-row-${c.id}`}
                >
                  <div className="min-w-0 md:col-span-4">
                    <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800 truncate">
                      {c.name || c.subject}
                      {Array.isArray(c.attachments) && c.attachments.length > 0 && (
                        <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-500 font-normal flex-shrink-0" title={`${c.attachments.length} image${c.attachments.length === 1 ? '' : 's'} attached`}>
                          <Package size={10} /> {c.attachments.length}
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-slate-400 truncate">{c.subject}</div>
                  </div>
                  <div className="flex items-center justify-between gap-3 md:col-span-2 md:block">
                    <span className="text-[11px] uppercase tracking-wide text-slate-400 md:hidden">Status</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${s.cls}`}>{s.label}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 text-sm text-slate-600 md:col-span-1 md:block md:text-right">
                    <span className="text-[11px] uppercase tracking-wide text-slate-400 md:hidden">Recipients</span>
                    <span>{c.total_recipients || 0}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 text-sm text-emerald-600 md:col-span-1 md:block md:text-right">
                    <span className="text-[11px] uppercase tracking-wide text-slate-400 md:hidden">Sent</span>
                    <span>{c.sent_count || 0}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 text-sm text-red-500 md:col-span-1 md:block md:text-right">
                    <span className="text-[11px] uppercase tracking-wide text-slate-400 md:hidden">Failed</span>
                    <span>{c.failed_count || 0}</span>
                  </div>
                  <div className="min-w-0 md:col-span-3">
                    <div className="flex flex-wrap items-center justify-start gap-1.5 md:justify-end">
                      {!['sending', 'queued'].includes(c.status) && (
                        <button
                          onClick={() => openCampaignEditor(c)}
                          className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-slate-200 px-2.5 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-1 focus:ring-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                          data-testid={`edit-campaign-${c.id}`}
                        >
                          <Pencil size={13} /> Edit
                        </button>
                      )}
                      {c.status === 'draft' ? (
                        <button
                          onClick={() => sendCampaign(c.id)}
                          disabled={(c.total_recipients || 0) <= 0}
                          className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-1 focus:ring-blue-300 disabled:cursor-not-allowed disabled:opacity-50"
                          data-testid={`send-campaign-${c.id}`}
                        >
                          <Send size={13} /> Start Campaign
                        </button>
                      ) : (c.status === 'queued' || c.status === 'sending') ? (
                        <button
                          onClick={() => pauseCampaign(c.id)}
                          className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-amber-200 bg-amber-50 px-2.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-100 focus:outline-none focus:ring-1 focus:ring-amber-300"
                          data-testid={`pause-campaign-${c.id}`}
                        >
                          <Pause size={13} /> Pause
                        </button>
                      ) : c.status === 'paused' ? (
                        <>
                          <button
                            onClick={() => resumeCampaign(c.id)}
                            className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-1 focus:ring-blue-300"
                            data-testid={`resume-campaign-${c.id}`}
                          >
                            <Play size={13} /> Resume
                          </button>
                          <button
                            onClick={() => restartCampaign(c.id)}
                            className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-slate-200 px-2.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-1 focus:ring-slate-200"
                            data-testid={`restart-campaign-${c.id}`}
                          >
                            <RotateCcw size={13} /> Restart
                          </button>
                        </>
                      ) : c.status === 'failed' ? (
                        <>
                          {(c.total_recipients || 0) > 0 && (
                            <button
                              onClick={() => sendCampaign(c.id)}
                              className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-1 focus:ring-blue-300"
                              data-testid={`send-campaign-${c.id}`}
                            >
                              <Send size={13} /> Send
                            </button>
                          )}
                          <button
                            onClick={() => restartCampaign(c.id)}
                            className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-slate-200 px-2.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-1 focus:ring-slate-200"
                            data-testid={`restart-campaign-${c.id}`}
                          >
                            <RotateCcw size={13} /> Restart
                          </button>
                        </>
                      ) : c.status === 'completed' ? (
                        <button
                          onClick={() => restartCampaign(c.id)}
                          className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-slate-200 px-2.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-1 focus:ring-slate-200"
                          data-testid={`restart-campaign-${c.id}`}
                        >
                          <RotateCcw size={13} /> Restart
                        </button>
                      ) : null}
                      <button
                        onClick={() => deleteCampaign(c.id)}
                        className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-md border border-slate-200 text-slate-400 transition-colors hover:bg-red-50 hover:text-red-500 focus:outline-none focus:ring-1 focus:ring-red-200"
                        aria-label="Delete campaign"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4 backdrop-blur-sm">
          <div className="w-full max-w-2xl max-h-[90vh] rounded-xl border border-slate-100 bg-white shadow-xl shadow-slate-900/10 flex flex-col overflow-hidden">
            <div className="flex-none flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                <Mail size={16} className="text-sky-500" /> {editingCampaignId ? 'Edit draft campaign' : 'New email campaign'}
              </h2>
              <button
                onClick={resetComposer}
                className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <form onSubmit={submit} className="flex-1 min-h-0 overflow-y-auto p-5 space-y-4">
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

              <div className="rounded-xl border border-sky-100 bg-sky-50/50 p-4 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-slate-700 flex items-center gap-2">
                      <Package size={14} className="text-sky-600" /> Product and AI copy
                    </div>
                    <p className="text-[11px] text-slate-500 mt-1">
                      Pick a product, then either write manually below or generate copy from campaign details.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={loadCampaignResources}
                    className="text-[11px] px-2 py-1 rounded-lg border border-sky-200 bg-white text-sky-700 hover:bg-sky-50"
                  >
                    Refresh data
                  </button>
                </div>
                <Field label="Product from database">
                  <select
                    value={form.product_id}
                    onChange={(e) => setForm({ ...form, product_id: e.target.value })}
                    className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                    data-testid="campaign-product"
                  >
                    <option value="">Select a product</option>
                    {products.map((product) => (
                      <option key={product.id} value={product.id}>
                        {product.name || product.product_title || 'Untitled product'}
                      </option>
                    ))}
                  </select>
                  {!resourceError && products.length === 0 ? (
                    <p className="mt-1 text-[11px] font-medium text-slate-500">
                      No products are available in the existing product table for this workspace.
                    </p>
                  ) : null}
                  {resourceError ? (
                    <p className="mt-1 flex items-center gap-1 text-[11px] font-medium text-red-600">
                      <AlertCircle size={12} /> {resourceError}
                    </p>
                  ) : null}
                </Field>

                {/* Image Attachments */}
                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1">Campaign Images</label>
                  <input
                    ref={campaignImageInputRef}
                    type="file"
                    accept="image/*"
                    multiple
                    className="hidden"
                    onChange={handleCampaignImageAdd}
                  />
                  <button
                    type="button"
                    onClick={() => campaignImageInputRef.current?.click()}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-medium text-slate-600 hover:bg-slate-50"
                  >
                    <Package size={13} /> Attach Images
                  </button>
                  {campaignImages.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-2">
                      {campaignImages.map((img, idx) => (
                        <div key={idx} className="relative group">
                          <img src={img.dataUrl} alt={img.name} className="h-16 w-16 object-cover rounded-lg border border-slate-200" />
                          <button
                            type="button"
                            onClick={() => setCampaignImages(prev => prev.filter((_, i) => i !== idx))}
                            className="absolute -top-1 -right-1 bg-red-500 text-white rounded-full w-4 h-4 flex items-center justify-center text-[10px] opacity-0 group-hover:opacity-100"
                          >×</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Campaign goal *">
                    <input
                      value={aiDetails.campaign_goal}
                      onChange={(e) => setAiDetails({ ...aiDetails, campaign_goal: e.target.value })}
                      placeholder="Reactivate inactive customers"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="ai-campaign-goal"
                    />
                  </Field>
                  <Field label="Audience description *">
                    <input
                      value={aiDetails.audience_description}
                      onChange={(e) => setAiDetails({ ...aiDetails, audience_description: e.target.value })}
                      placeholder="Warm leads interested in support automation"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="ai-audience-description"
                    />
                  </Field>
                  <Field label="Tone *">
                    <select
                      value={aiDetails.tone}
                      onChange={(e) => setAiDetails({ ...aiDetails, tone: e.target.value })}
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="ai-tone"
                    >
                      <option value="friendly">Friendly</option>
                      <option value="professional">Professional</option>
                      <option value="urgent">Urgent</option>
                      <option value="premium">Premium</option>
                    </select>
                  </Field>
                  <Field label="Call to action *">
                    <input
                      value={aiDetails.call_to_action}
                      onChange={(e) => setAiDetails({ ...aiDetails, call_to_action: e.target.value })}
                      placeholder="Book a demo this week"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                      data-testid="ai-call-to-action"
                    />
                  </Field>
                  <Field label="Offer details (optional)">
                    <input
                      value={aiDetails.offer_details}
                      onChange={(e) => setAiDetails({ ...aiDetails, offer_details: e.target.value })}
                      placeholder="Free migration assessment"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                    />
                  </Field>
                  <Field label="Extra context (optional)">
                    <input
                      value={aiDetails.extra_context}
                      onChange={(e) => setAiDetails({ ...aiDetails, extra_context: e.target.value })}
                      placeholder="Mention quick setup and real-time inbox replies"
                      className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"
                    />
                  </Field>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-[11px] text-slate-500">
                    Generate unlocks after product, goal, audience, tone, and CTA are filled.
                  </p>
                  <button
                    type="button"
                    onClick={generateCampaignCopy}
                    disabled={aiDisabled || !aiGenerationReady || generatingCopy}
                    className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-sky-600 text-white text-xs font-medium hover:bg-sky-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    data-testid="campaign-generate-ai"
                  >
                    <Wand2 size={13} /> {generatingCopy ? 'Generating...' : 'Generate with AI'}
                  </button>
                </div>
                {aiGenerationError ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 flex items-start gap-2">
                    <AlertCircle size={13} className="mt-0.5 flex-none" /> <span>{aiGenerationError}</span>
                  </div>
                ) : null}
              </div>

              <Field label="Plain-text body">
                <textarea
                  value={form.body}
                  onChange={(e) => setForm({ ...form, body: e.target.value })}
                  placeholder="Write your message…"
                  rows={6}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm resize-y"
                  data-testid="campaign-body"
                />
              </Field>

              <details className="rounded-lg border border-slate-200 bg-slate-50/60">
                <summary className="px-3 py-2 text-xs font-medium text-slate-600 cursor-pointer">
                  HTML body
                </summary>
                <div className="space-y-3 px-3 pb-3">
                  <div className="rounded-lg border border-slate-200 bg-white p-3">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-slate-700">Generate HTML with AI</p>
                      <button
                        type="button"
                        onClick={generateHtmlBody}
                        disabled={aiDisabled || generatingHtmlBody || !htmlAiPrompt.trim()}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid="campaign-generate-html-ai"
                      >
                        <Wand2 size={13} /> {generatingHtmlBody ? 'Generating...' : 'Generate with AI'}
                      </button>
                    </div>
                    <textarea
                      value={htmlAiPrompt}
                      onChange={(e) => setHtmlAiPrompt(e.target.value)}
                      placeholder="Describe the HTML email body, layout, offer, and CTA..."
                      rows={3}
                      className="w-full resize-y rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm"
                      data-testid="campaign-html-ai-prompt"
                    />
                    {aiGenerationError ? (
                      <p className="mt-2 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs font-medium text-red-700">
                        {aiGenerationError}
                      </p>
                    ) : null}
                  </div>
                  <textarea
                    value={form.html_body}
                    onChange={(e) => setForm({ ...form, html_body: e.target.value })}
                    placeholder="<p>Rich HTML content…</p>"
                    rows={7}
                    className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-xs font-mono resize-y"
                    data-testid="campaign-html-body"
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

                <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-3">
                  <RecipientPicker
                    title="Select leads"
                    icon={<Users size={13} className="text-blue-500" />}
                    items={leads}
                    selectedIds={selectedLeadIds}
                    onToggle={toggleLead}
                    onToggleAll={toggleAllLeads}
                    emptyText="No email-ready leads found."
                    testIdPrefix="lead-recipient"
                  />
                  <RecipientPicker
                    title="Select customers"
                    icon={<Users size={13} className="text-emerald-500" />}
                    items={customers}
                    selectedIds={selectedCustomerIds}
                    onToggle={toggleCustomer}
                    onToggleAll={toggleAllCustomers}
                    emptyText="No email-ready customers found."
                    testIdPrefix="customer-recipient"
                  />
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

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 sticky bottom-0 bg-white">
                <button
                  type="button"
                  onClick={resetComposer}
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
    {confirmDialog}
    </>
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

function RecipientPicker({ title, icon, items, selectedIds, onToggle, onToggleAll, emptyText, testIdPrefix }) {
  const visibleItems = items.slice(0, 80);
  const visibleIds = visibleItems.map((item) => item.id).filter(Boolean);
  const selectedVisibleCount = visibleIds.filter((id) => selectedIds.includes(id)).length;
  const allSelected = visibleIds.length > 0 && selectedVisibleCount === visibleIds.length;
  const partiallySelected = selectedVisibleCount > 0 && !allSelected;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-semibold text-slate-600 flex items-center gap-1.5">
          {icon} {title}
        </p>
        <span className="text-[10px] text-slate-400">{selectedIds.length} selected</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-slate-400 py-2">{emptyText}</p>
      ) : (
        <div className="max-h-40 overflow-y-auto space-y-1 pr-1">
          <label className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs font-semibold text-slate-700">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(input) => {
                if (input) input.indeterminate = partiallySelected;
              }}
              onChange={(e) => onToggleAll?.(e.target.checked)}
              className="mt-0.5"
              data-testid={`${testIdPrefix}-select-all`}
            />
            Select all visible
          </label>
          {visibleItems.map((item) => {
            const selected = selectedIds.includes(item.id);
            return (
              <label
                key={item.id}
                className={`flex items-start gap-2 rounded-md px-2 py-1.5 text-xs cursor-pointer border ${
                  selected ? 'bg-blue-50 border-blue-100 text-blue-800' : 'bg-slate-50/80 border-transparent text-slate-600 hover:bg-slate-100'
                }`}
                data-testid={`${testIdPrefix}-${item.id}`}
              >
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => onToggle(item.id)}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="block font-medium truncate">{item.name || item.email}</span>
                  <span className="block text-[10px] opacity-70 truncate">{item.email}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}
    </div>
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
