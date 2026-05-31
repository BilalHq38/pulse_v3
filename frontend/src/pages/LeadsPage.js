import { useState, useEffect, useCallback, useDeferredValue, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { resolveMediaUrl } from '@/lib/backend-url';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import BulkUploadModal from '@/components/BulkUploadModal';
import { useSocket } from '@/lib/useSocket';
import { useAuth } from '@/contexts/AuthContext';
import {
  Target,
  Search,
  Plus,
  X,
  Phone,
  Mail,
  Building2,
  Sparkles,
  MessageSquare,
  Trash2,
  AlertCircle,
  UserCheck,
} from 'lucide-react';
import { buildLeadMethods, CHANNEL_META } from '@/lib/channelUtils';

const GRADE_COLORS = { hot: 'bg-red-50 text-red-500 border-red-500/30', warm: 'bg-amber-50 text-amber-600 border-amber-500/30', cold: 'bg-blue-50 text-blue-600 border-blue-500/30' };
const SOURCE_COLORS = {
  whatsapp: 'bg-green-100 text-green-700',
  email: 'bg-sky-100 text-sky-700',
  instagram: 'bg-pink-100 text-pink-700',
  facebook: 'bg-blue-100 text-blue-700',
  web_chat: 'bg-violet-100 text-violet-700',
};
const SOURCE_LABELS = {
  whatsapp: 'WhatsApp',
  email: 'Email',
  instagram: 'Instagram',
  facebook: 'Facebook',
  web_chat: 'Web Chat',
};
const STATUS_COLORS = { new: 'bg-blue-50 text-blue-600', contacted: 'bg-cyan-50 text-cyan-600', qualified: 'bg-emerald-50 text-emerald-600', proposal: 'bg-amber-50 text-amber-600', negotiation: 'bg-fuchsia-50 text-fuchsia-600', converted: 'bg-indigo-50 text-indigo-600', won: 'bg-green-500/10 text-green-600', lost: 'bg-red-50 text-red-500' };
const LEAD_BULK_TEMPLATE_HEADERS = ['name', 'email', 'phone', 'company', 'source', 'status', 'notes', 'tags', 'channels'];
const LEAD_BULK_TEMPLATE_SAMPLE = ['Avery Stone', 'avery@northstar.io', '+1 415 555 0188', 'Northstar Labs', 'whatsapp', 'new', 'Requested a pricing follow-up', 'hot_lead, interested', 'whatsapp, email'];
const LEAD_SOURCE_OPTIONS = ['web_chat', 'whatsapp', 'email', 'instagram', 'facebook'];
const LONG_REQUEST_TIMEOUT_MS = 120000;
const LEAD_BULK_GUIDE_ROWS = [
  { column: 'name', help: 'Lead name. Use full name when possible.' },
  { column: 'email', help: 'Optional, but recommended for matching and follow-up.' },
  { column: 'phone', help: 'Optional, but recommended. International format works best, for example +1 415 555 0188.' },
  { column: 'company', help: 'Optional company or organization name.' },
  { column: 'source', help: 'Optional channel source such as whatsapp, email, instagram, facebook, or web_chat.' },
  { column: 'status', help: 'Optional lead status such as new, contacted, qualified, won, or lost.' },
  { column: 'notes', help: 'Optional notes for the lead profile.' },
  { column: 'tags', help: 'Optional comma-separated tags.' },
  { column: 'channels', help: 'Optional comma-separated channels such as whatsapp, email, or instagram.' },
];

function LeadAvatar({ lead, className = 'w-8 h-8', textClass = 'text-xs' }) {
  const [failed, setFailed] = useState(false);
  const url = resolveMediaUrl(lead?.avatar || '');
  const initial = (lead?.name || '?').charAt(0);
  return (
    <div className={`${className} rounded-full bg-blue-50 text-blue-600 flex items-center justify-center ${textClass} font-bold overflow-hidden flex-shrink-0`}>
      {url && !failed ? (
        <img src={url} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : initial}
    </div>
  );
}

function formatStageLabel(status) {
  return String(status || 'new').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function parseLeadStageActivity(activity) {
  if (!activity || activity.type !== 'stage_changed') return null;
  try {
    const parsed = JSON.parse(activity.content || '{}');
    if (parsed && typeof parsed === 'object') return parsed;
  } catch {
    return { reason: activity.content || '' };
  }
  return null;
}

function getLatestLeadStageUpdate(lead) {
  if (lead?.last_stage_update) return lead.last_stage_update;
  const activities = Array.isArray(lead?.activities) ? lead.activities : [];
  for (let index = activities.length - 1; index >= 0; index -= 1) {
    const parsed = parseLeadStageActivity(activities[index]);
    if (parsed) return parsed;
  }
  return null;
}

function LeadCardSkeleton() {
  return (
    <div className="animate-pulse rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-full bg-slate-100" />
          <div className="space-y-1.5">
            <div className="h-3.5 w-32 rounded bg-slate-100" />
            <div className="h-3 w-20 rounded bg-slate-100" />
          </div>
        </div>
        <div className="h-5 w-14 rounded-full bg-slate-100" />
      </div>
      <div className="mt-3 flex gap-2">
        <div className="h-3 w-24 rounded bg-slate-100" />
        <div className="h-3 w-16 rounded bg-slate-100" />
      </div>
    </div>
  );
}

export default function LeadsPage() {
  const { user, loading: authLoading } = useAuth();
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  const [leads, setLeads] = useState([]);
  const [leadStatuses, setLeadStatuses] = useState([]);
  const [leadSources, setLeadSources] = useState([]);
  const [selectedLead, setSelectedLead] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search);
  const [filterGrade, setFilterGrade] = useState('');
  const [filterSource, setFilterSource] = useState('');
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: '', email: '', phone: '', company: '', source: 'web_chat', notes: '' });
  const [nurturingAll, setNurturingAll] = useState(false);
  const [pendingBatchNurture, setPendingBatchNurture] = useState([]);
  const [sendingNurtureAll, setSendingNurtureAll] = useState(false);
  const [autoScoring, setAutoScoring] = useState(false);
  const [nurturingLead, setNurturingLead] = useState(false);
  const [sendingNurtureId, setSendingNurtureId] = useState('');
  const [deletingLeadId, setDeletingLeadId] = useState(null);
  const [convertingLeadId, setConvertingLeadId] = useState('');
  const [editingNurtureId, setEditingNurtureId] = useState('');
  const [nurtureEditText, setNurtureEditText] = useState('');
  const [nurtureEditError, setNurtureEditError] = useState('');
  const [savingNurtureId, setSavingNurtureId] = useState('');
  const [deletingNurtureId, setDeletingNurtureId] = useState('');

  const [showMethodPicker, setShowMethodPicker] = useState(false);
  const [messageMethods, setMessageMethods] = useState([]);
  const [contactPrompt, setContactPrompt] = useState({ open: false, mode: 'message', lead: null, method: null });
  const [contactForm, setContactForm] = useState({ phone: '', email: '' });
  const [showEditLead, setShowEditLead] = useState(false);
  const [editLeadForm, setEditLeadForm] = useState({ name: '', email: '', phone: '', company: '', source: 'web_chat', notes: '', status: 'new' });
  const [nurtureComposer, setNurtureComposer] = useState({ open: false, lead: null, message: null, channel: '' });
  const [leadEmailComposer, setLeadEmailComposer] = useState({
    open: false,
    lead: null,
    subject: '',
    body: '',
    originalSubject: '',
    originalBody: '',
    sending: false,
  });
  const [creatingLead, setCreatingLead] = useState(false);
  const [createLeadError, setCreateLeadError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [showBulkUpload, setShowBulkUpload] = useState(false);
  const [bulkUploading, setBulkUploading] = useState(false);
  const dismissedLeadIdRef = useRef('');

  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const closeLeadDetail = useCallback(() => {
    dismissedLeadIdRef.current = selectedLead?.id || searchParams.get('lead') || '';
    setSelectedLead(null);
    setEditingNurtureId('');
    setNurtureEditText('');
    setNurtureEditError('');
    if (searchParams.get('lead')) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete('lead');
      setSearchParams(nextParams, { replace: true });
    }
  }, [searchParams, selectedLead?.id, setSearchParams]);

  const loadLeads = useCallback(async () => {
    setLoadError('');
    try {
      const params = {};
      if (deferredSearch) params.search = deferredSearch;
      if (filterGrade) params.grade = filterGrade;
      if (filterSource) params.source = filterSource;
      const res = await api.get('/leads', { params });
      setLeads(res.data);
    } catch (err) {
      console.error(err);
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 401 || status === 403) {
        setLoadError('Session expired. Please refresh the page.');
      } else {
        setLoadError(detail || 'Failed to load leads. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  }, [deferredSearch, filterGrade, filterSource]);

  const loadReferenceData = useCallback(async () => {
    try {
      const res = await api.get('/reference-data');
      setLeadStatuses(res.data?.lead_statuses || []);
      setLeadSources(res.data?.sources || []);
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => { if (user && !authLoading) loadLeads(); }, [user, authLoading, loadLeads]);
  useEffect(() => { if (user && !authLoading) loadReferenceData(); }, [user, authLoading, loadReferenceData]);
  // Socket-based real-time updates — fired by backend on lead CRUD
  const handleLeadSocketEvent = useCallback((eventName) => {
    if (['lead_created', 'lead_updated', 'lead_deleted'].includes(eventName)) {
      loadLeads();
    }
  }, [loadLeads]);
  useSocket(handleLeadSocketEvent);
  useEffect(() => {
    // Fallback poll every 30s for tab-backgrounded or missed socket events.
    // Skipped when tab is hidden to avoid wasted network requests.
    const intervalId = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      loadLeads();
    }, 30000);
    return () => clearInterval(intervalId);
  }, [loadLeads]);
  // Refresh immediately when tab becomes visible after being backgrounded
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === 'visible') loadLeads(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadLeads]);
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        closeLeadDetail();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [closeLeadDetail]);

  const openLeadDetail = useCallback(async (lead) => {
    if (!lead?.id) return;
    dismissedLeadIdRef.current = '';
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('lead', lead.id);
    setSearchParams(nextParams, { replace: true });
    try {
      const res = await api.get(`/leads/${lead.id}`);
      setSelectedLead(res.data);
    } catch (err) {
      console.error(err);
      setSelectedLead(lead);
    }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    const requestedLeadId = searchParams.get('lead');
    if (!requestedLeadId) {
      dismissedLeadIdRef.current = '';
      return;
    }
    if (loading || selectedLead?.id === requestedLeadId || dismissedLeadIdRef.current === requestedLeadId) return;
    const lead = leads.find((item) => item.id === requestedLeadId);
    if (lead) {
      openLeadDetail(lead);
    }
  }, [leads, loading, openLeadDetail, searchParams, selectedLead?.id]);

  const upsertLeadState = (lead) => {
    if (!lead) return;
    setSelectedLead(lead);
    setLeads((prev) => prev.map((item) => (item.id === lead.id ? lead : item)));
  };

  const createLead = async () => {
    if (!form.name.trim()) { setCreateLeadError('Full name is required.'); return; }
    setCreatingLead(true);
    setCreateLeadError('');
    try {
      await api.post('/leads', form);
      setShowForm(false);
      setForm({ name: '', email: '', phone: '', company: '', source: 'web_chat', notes: '' });
      setCreateLeadError('');
      loadLeads();
    } catch (err) {
      console.error(err);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 409) {
        setCreateLeadError(detail || 'This contact already exists as a customer and cannot be added as a lead.');
      } else if (status === 422) {
        setCreateLeadError('Please fill in all required fields correctly.');
      } else if (status === 401 || status === 403) {
        setCreateLeadError('Session expired. Please refresh the page and try again.');
      } else {
        setCreateLeadError(detail || 'Failed to create lead. Please try again.');
      }
    } finally {
      setCreatingLead(false);
    }
  };

  const scoreLead = async (leadId) => {
    setAutoScoring(true);
    try {
      const res = await api.post(`/leads/${leadId}/score`);
      const scored = res.data;
      // Merge score fields into existing selectedLead so nurture_messages/activities
      // from the detail view are preserved even if the score response omits them.
      setSelectedLead((prev) => {
        if (!prev || prev.id !== leadId) return scored;
        return {
          ...prev,
          ...scored,
          // Preserve existing nurture_messages/activities if the score response omits them
          nurture_messages: scored?.nurture_messages ?? prev.nurture_messages,
          activities: scored?.activities ?? prev.activities,
          channels: scored?.channels ?? prev.channels,
          tags: scored?.tags ?? prev.tags,
        };
      });
      setLeads((prev) => prev.map((item) => (item.id === leadId ? { ...item, ...scored } : item)));
      loadLeads();
      showToast({
        type: 'success',
        title: 'Lead Scored',
        message: `${scored?.name || 'This lead'} received a score of ${scored?.score ?? '—'} (${scored?.grade || 'assessed'}).`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Scoring Failed',
        message: getErrorMessage(err, 'We could not score that lead. Please try again.'),
      });
    }
    finally { setAutoScoring(false); }
  };

  const nurtureLead = async (leadId) => {
    setNurturingLead(true);
    try {
      await api.post(`/leads/${leadId}/nurture`);
      const refreshed = await api.get(`/leads/${leadId}`);
      upsertLeadState(refreshed.data);
      loadLeads();
      showToast({
        type: 'success',
        title: 'Draft Generated',
        message: `A nurture draft is ready for ${refreshed.data?.name || 'this lead'}.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Draft Failed',
        message: getErrorMessage(err, 'We could not generate a nurture message for that lead.'),
      });
    } finally {
      setNurturingLead(false);
    }
  };

  const nurtureAllLeads = async () => {
    if (nurturingAll || sendingNurtureAll) return;
    setNurturingAll(true);
    showToast({
      type: 'info',
      title: 'Generating Drafts',
      message: 'Analyzing leads and generating personalized nurture messages. This may take up to 60 seconds…',
    });
    try {
      // Use a 120-second timeout — concurrent AI generation for up to 20 leads
      const res = await api.post('/leads/auto-nurture-all', {}, { timeout: LONG_REQUEST_TIMEOUT_MS });
      const refreshed = await api.get('/leads');
      const refreshedLeads = Array.isArray(refreshed.data) ? refreshed.data : [];
      const generatedIds = new Set((res.data?.results || [])
        .filter((item) => item?.status === 'nurtured')
        .map((item) => item.lead_id));
      const pendingDrafts = collectBatchNurtureDrafts(
        refreshedLeads,
        generatedIds.size ? (lead) => generatedIds.has(lead?.id) : undefined,
      );
      setPendingBatchNurture(pendingDrafts);
      const nurtured = res.data?.total_nurtured ?? pendingDrafts.length;
      const total = res.data?.total_processed ?? 0;
      if (nurtured > 0) {
        showToast({
          type: 'success',
          title: 'Drafts Generated',
          message: `Generated ${nurtured} personalized nurture draft${nurtured !== 1 ? 's' : ''} from ${total} leads. Review and send when ready.`,
        });
      } else {
        showToast({
          type: 'warning',
          title: 'No Drafts Generated',
          message: total > 0 ? 'AI could not produce messages for these leads. Try again or add more lead data.' : 'No eligible leads found for nurturing.',
        });
      }
      await loadLeads();
    } catch (err) {
      console.error(err);
      const isTimeout = err?.code === 'ECONNABORTED' || err?.message?.includes('timeout');
      showToast({
        type: 'error',
        title: isTimeout ? 'Generation Timed Out' : 'Batch Failed',
        message: isTimeout
          ? 'Nurture generation took too long. Try nurturing individual leads or reduce the number of eligible leads.'
          : getErrorMessage(err, 'We could not run AI nurture for the selected leads.'),
      });
    }
    finally { setNurturingAll(false); }
  };

  const updateStatus = async (leadId, status) => {
    try {
      const res = await api.put(`/leads/${leadId}/stage`, { stage: status, reason: 'Manual stage update' });
      upsertLeadState(res.data?.lead || res.data);
      loadLeads();
      showToast({
        type: 'success',
        title: 'Stage Updated',
        message: `Lead moved to ${formatStageLabel(status)}.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Stage Update Failed',
        message: getErrorMessage(err, 'We could not update that lead stage.'),
      });
    }
  };

  const deleteLead = async (lead, event) => {
    if (event) event.stopPropagation();
    requestConfirmation({
      title: 'Delete Lead',
      description: `Delete ${lead.name || 'this lead'} permanently. This action cannot be undone.`,
      confirmLabel: 'Delete lead',
      onConfirm: async () => {
        setDeletingLeadId(lead.id);
        try {
          await api.delete(`/leads/${lead.id}`);
          setLeads((prev) => prev.filter((item) => item.id !== lead.id));
          if (selectedLead?.id === lead.id) closeLeadDetail();
          showToast({
            type: 'success',
            title: 'Lead Deleted',
            message: `${lead.name || 'The lead'} was removed.`,
          });
        } catch (err) {
          console.error(err);
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, `We could not delete ${lead.name || 'that lead'}.`),
          });
        } finally {
          setDeletingLeadId(null);
        }
      },
    });
  };

  const openEditLead = (lead) => {
    setEditLeadForm({
      name: lead?.name || '',
      email: lead?.email || '',
      phone: lead?.phone || '',
      company: lead?.company || '',
      source: lead?.source || 'web_chat',
      notes: lead?.notes || '',
      status: lead?.status || 'new',
    });
    setShowEditLead(true);
  };

  const saveLeadEdits = async () => {
    if (!selectedLead) return;
    try {
      const res = await api.put(`/leads/${selectedLead.id}`, editLeadForm);
      upsertLeadState(res.data);
      setShowEditLead(false);
      loadLeads();
      showToast({
        type: 'success',
        title: 'Lead Updated',
        message: `${res.data?.name || selectedLead.name || 'The lead'} was updated.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: getErrorMessage(err, `We could not update ${selectedLead.name || 'that lead'}.`),
      });
    }
  };

  const handleBulkUpload = async (file) => {
    setBulkUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await api.post('/leads/bulk-upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_REQUEST_TIMEOUT_MS,
      });
      const result = res.data || {};
      await loadLeads();
      setShowBulkUpload(false);
      const message = `Created ${result.created || 0}, updated ${result.updated || 0}, skipped ${result.skipped || 0}.`;
      if (Array.isArray(result.errors) && result.errors.length > 0) {
        const firstError = result.errors[0];
        showToast({
          type: 'warning',
          title: 'Import Completed with Warnings',
          message: `${message} First issue: row ${firstError.row} - ${firstError.error}`,
        });
      } else {
        showToast({
          type: 'success',
          title: 'Lead Import Complete',
          message,
        });
      }
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Import Failed',
        message: getErrorMessage(err, 'We could not import that lead spreadsheet.'),
      });
    } finally {
      setBulkUploading(false);
    }
  };

  const getLatestDraft = (lead) => {
    const messages = Array.isArray(lead?.nurture_messages) ? lead.nurture_messages : [];
    return [...messages].reverse().find((item) => !item?.sent) || null;
  };

  const buildLeadEmailDraft = (lead) => {
    const latestDraft = getLatestDraft(lead);
    const name = String(lead?.name || '').trim();
    const company = String(lead?.company || '').trim();
    const notes = String(lead?.notes || '').trim();
    const subject = company ? `Follow up for ${company}` : `Follow up${name ? ` for ${name}` : ''}`;
    const body = latestDraft?.message
      ? String(latestDraft.message)
      : [
          `Hi ${name || 'there'},`,
          '',
          notes
            ? `I wanted to follow up on your interest and the note we have: ${notes}`
            : 'I wanted to follow up and see what would be most useful for you next.',
          '',
          'Best regards,',
        ].join('\n');
    return { subject, body };
  };

  const closeLeadEmailComposer = () => {
    if (leadEmailComposer.sending) return;
    setLeadEmailComposer({
      open: false,
      lead: null,
      subject: '',
      body: '',
      originalSubject: '',
      originalBody: '',
      sending: false,
    });
  };

  const openLeadEmailComposer = (lead, { warnOnly = false } = {}) => {
    if (!lead?.email) {
      if (warnOnly) {
        showToast({
          type: 'warning',
          title: 'Email Missing',
          message: 'No email address found for this lead.',
        });
        return;
      }
      askForContacts(lead, 'email');
      return;
    }
    const draft = buildLeadEmailDraft(lead);
    setLeadEmailComposer({
      open: true,
      lead,
      subject: draft.subject,
      body: draft.body,
      originalSubject: draft.subject,
      originalBody: draft.body,
      sending: false,
    });
  };

  const submitLeadEmail = async () => {
    const lead = leadEmailComposer.lead;
    if (!lead || leadEmailComposer.sending) return;
    if (!lead.email) {
      closeLeadEmailComposer();
      askForContacts(lead, 'email');
      return;
    }
    const subject = leadEmailComposer.subject.trim();
    const body = leadEmailComposer.body.trim();
    if (!subject || !body) {
      showToast({
        type: 'error',
        title: 'Email Incomplete',
        message: 'Add a subject and message before sending the email.',
      });
      return;
    }
    setLeadEmailComposer((prev) => ({ ...prev, sending: true }));
    try {
      await api.post('/communications/email/send', {
        to_email: lead.email,
        subject,
        body,
      }, { timeout: LONG_REQUEST_TIMEOUT_MS });
      showToast({
        type: 'success',
        title: 'Email Sent',
        message: `${lead.name || lead.email} received an email on ${lead.email}.`,
      });
      closeLeadEmailComposer();
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Email Failed',
        message: getErrorMessage(err, `We could not send an email to ${lead.email}.`),
      });
      setLeadEmailComposer((prev) => ({ ...prev, sending: false }));
    }
  };

  const getLeadSendChannels = useCallback((lead) => {
    const methods = buildLeadMethods(lead);
    const available = new Map();

    methods.forEach((method) => {
      const channel = String(method?.channel || '').trim().toLowerCase();
      if (!['whatsapp', 'email', 'facebook', 'instagram'].includes(channel) || available.has(channel)) {
        return;
      }
      if (channel === 'whatsapp' && !String(lead?.phone || '').trim()) return;
      if (channel === 'email' && !String(lead?.email || '').trim()) return;
      if ((channel === 'facebook' || channel === 'instagram')
        && !String(
          lead?.channel_recipient_id
          || lead?.external_recipient_id
          || lead?.channel_user_id
          || lead?.social_profiles?.[channel]
          || ''
        ).trim()) {
        return;
      }
      available.set(channel, method);
    });

    return Array.from(available.values());
  }, []);

  function collectBatchNurtureDrafts(leadItems, filterLead = null) {
    const eligibleStatuses = new Set(['new', 'contacted', 'qualified']);
    return (Array.isArray(leadItems) ? leadItems : [])
      .filter((lead) => lead?.id && eligibleStatuses.has(String(lead.status || 'new')))
      .filter((lead) => (filterLead ? filterLead(lead) : true))
      .map((lead) => {
        const draft = getLatestDraft(lead);
        const channels = draft ? getLeadSendChannels(lead) : [];
        if (!draft?.id || channels.length === 0) return null;
        return {
          leadId: lead.id,
          leadName: lead.name || 'Lead',
          messageId: draft.id,
          channel: channels[0].channel,
        };
      })
      .filter(Boolean);
  }

  const closeNurtureComposer = () => {
    setNurtureComposer({ open: false, lead: null, message: null, channel: '' });
  };

  const openNurtureComposer = (lead, nurtureMessage, event) => {
    if (event) event.stopPropagation();
    const channels = getLeadSendChannels(lead);
    if (!channels.length) {
      showToast({
        type: 'error',
        title: 'No Channels',
        message: 'Add an email address or WhatsApp number before sending this nurture message.',
      });
      return;
    }
    setNurtureComposer({
      open: true,
      lead,
      message: nurtureMessage,
      channel: channels[0].channel,
    });
  };

  const openInboxForLead = async (lead) => {
    try {
      const res = await api.post(`/leads/${lead.id}/conversation`);
      const conversationId = res.data?.conversation?.id;
      if (!conversationId) throw new Error('Missing conversation id');
      navigate(`/inbox?conversation=${encodeURIComponent(conversationId)}`);
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Chat Unavailable',
        message: getErrorMessage(err, 'We could not open the lead conversation.'),
      });
    }
  };

  const isEmailChannelConfigured = async () => {
    try {
      const res = await api.get('/settings/channels');
      const channels = Array.isArray(res.data) ? res.data : [];
      const email = channels.find((item) => String(item?.channel || '').toLowerCase() === 'email');
      if (!email || email.enabled === false || email.email_send_enabled === false) return false;
      const provider = String(email.email_provider || 'smtp_imap').toLowerCase();
      if (provider === 'brevo') {
        return Boolean(String(email.api_key || '').trim() && String(email.email_address || email.smtp_user || '').trim());
      }
      if (provider === 'oauth_google') {
        return Boolean(email.enabled);
      }
      return Boolean(String(email.smtp_host || '').trim() && String(email.email_address || email.smtp_user || '').trim());
    } catch {
      return false;
    }
  };

  const buildLeadMessageMethods = (lead) => {
    const methods = buildLeadMethods(lead);
    if (!methods.some((method) => method.channel === 'email')) {
      methods.push({
        channel: 'email',
        profileUrl: lead?.email || '',
        source: 'lead_email',
        isPrimaryContact: false,
      });
    }
    return methods;
  };

  const openMessagePicker = (lead) => {
    const methods = buildLeadMessageMethods(lead);
    if (methods.length === 0) {
      showToast({
        type: 'error',
        title: 'No Channels',
        message: 'No messaging channels are available for this lead yet.',
      });
      return;
    }
    setMessageMethods(methods);
    setShowMethodPicker(true);
  };

  const askForContacts = (lead, mode, method = null) => {
    setContactForm({ phone: lead?.phone || '', email: lead?.email || '' });
    setContactPrompt({ open: true, mode, lead, method });
  };

  const handlePickMethod = async (method) => {
    if (!selectedLead) return;
    setShowMethodPicker(false);
    if (method.channel === 'email') {
      if (!selectedLead.email) {
        openLeadEmailComposer(selectedLead, { warnOnly: true });
        return;
      }
      const configured = await isEmailChannelConfigured();
      if (!configured) {
        showToast({
          type: 'warning',
          title: 'Email Not Configured',
          message: 'Email channel is not configured.',
        });
        return;
      }
      openLeadEmailComposer(selectedLead, { warnOnly: true });
      return;
    }
    if (!method.isPrimaryContact) {
      navigate(`/inbox?outbound=1&channel=${encodeURIComponent(method.channel)}&name=${encodeURIComponent(selectedLead.name || 'Lead')}&phone=${encodeURIComponent(selectedLead.phone || '')}`);
      return;
    }
    if (!selectedLead.phone) {
      askForContacts(selectedLead, 'message', method);
      return;
    }
    if (method.profileUrl) window.open(method.profileUrl, '_blank', 'noopener,noreferrer');
    openInboxForLead(selectedLead);
  };

  const handleEmailLead = (lead) => {
    openLeadEmailComposer(lead);
  };

  const submitContacts = async () => {
    const lead = contactPrompt.lead;
    if (!lead) return;

    const phone = contactForm.phone.trim();
    const email = contactForm.email.trim();

    if (contactPrompt.mode === 'message' && !phone) {
      showToast({
        type: 'error',
        title: 'Phone Missing',
        message: 'Add a phone number before starting a chat conversation.',
      });
      return;
    }

    if (contactPrompt.mode === 'email' && !email) {
      showToast({
        type: 'error',
        title: 'Email Missing',
        message: 'Add an email address before sending an email.',
      });
      return;
    }

    try {
      const updates = {};
      if (phone && phone !== (lead.phone || '')) updates.phone = phone;
      if (email && email !== (lead.email || '')) updates.email = email;

      let updatedLead = lead;
      if (Object.keys(updates).length > 0) {
        const res = await api.put(`/leads/${lead.id}`, updates);
        updatedLead = res.data;
        upsertLeadState(updatedLead);
        loadLeads();
      }

      setContactPrompt({ open: false, mode: 'message', lead: null, method: null });

      if (contactPrompt.mode === 'email') {
        openLeadEmailComposer(updatedLead);
        return;
      }

      if (contactPrompt.method?.profileUrl) {
        window.open(contactPrompt.method.profileUrl, '_blank', 'noopener,noreferrer');
      }
      await openInboxForLead(updatedLead);
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save those lead contact details.'),
      });
    }
  };

  const submitNurtureMessageSend = async () => {
    const lead = nurtureComposer.lead;
    const nurtureMessage = nurtureComposer.message;
    const selectedChannel = nurtureComposer.channel;
    if (!lead?.id || !nurtureMessage?.id || !selectedChannel || sendingNurtureId) return;
    setSendingNurtureId(nurtureMessage.id);
    try {
      const res = await api.post(`/leads/${lead.id}/nurture-messages/${nurtureMessage.id}/send`, {
        channel: selectedChannel,
      });
      const updatedLead = res.data?.lead;
      if (updatedLead) upsertLeadState(updatedLead);
      await loadLeads();
      setPendingBatchNurture((prev) => prev.filter((item) => item.messageId !== nurtureMessage.id));
      closeNurtureComposer();
      showToast({
        type: 'success',
        title: 'Message Sent',
        message: `${lead.name || 'This lead'} was contacted via ${(CHANNEL_META[selectedChannel] || {}).label || selectedChannel}.`,
      });
      if (res.data?.conversation_id) navigate(`/inbox?conversation=${encodeURIComponent(res.data.conversation_id)}`);
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Message Failed',
        message: getErrorMessage(err, 'We could not send that nurture message.'),
        dedupeKey: `lead-message-failed:${lead.id}:${selectedChannel}`,
      });
    } finally {
      setSendingNurtureId('');
    }
  };

  const sendAllGeneratedNurture = async () => {
    if (sendingNurtureAll || nurturingAll || pendingBatchNurture.length === 0) return;
    setSendingNurtureAll(true);
    const failed = [];
    let sentCount = 0;
    try {
      for (const item of pendingBatchNurture) {
        try {
          const res = await api.post(`/leads/${item.leadId}/nurture-messages/${item.messageId}/send`, {
            channel: item.channel,
          });
          if (res.data?.lead) upsertLeadState(res.data.lead);
          sentCount += 1;
        } catch (err) {
          console.error(err);
          failed.push(item);
        }
      }
      setPendingBatchNurture(failed);
      await loadLeads();
      showToast({
        type: failed.length ? 'warning' : 'success',
        title: failed.length ? 'Some Messages Failed' : 'Messages Sent',
        message: failed.length
          ? `Sent ${sentCount}; ${failed.length} still need attention.`
          : `Sent ${sentCount} nurture messages.`,
      });
    } finally {
      setSendingNurtureAll(false);
    }
  };

  const startNurtureEdit = (nurtureMessage, event) => {
    if (event) event.stopPropagation();
    setEditingNurtureId(nurtureMessage.id);
    setNurtureEditText(nurtureMessage.message || '');
    setNurtureEditError('');
  };

  const cancelNurtureEdit = (event) => {
    if (event) event.stopPropagation();
    setEditingNurtureId('');
    setNurtureEditText('');
    setNurtureEditError('');
  };

  const saveNurtureEdit = async (lead, nurtureMessage, event) => {
    if (event) event.stopPropagation();
    const nextMessage = nurtureEditText.trim();
    if (!nextMessage) {
      setNurtureEditError('Message cannot be empty.');
      return;
    }
    setSavingNurtureId(nurtureMessage.id);
    setNurtureEditError('');
    try {
      const res = await api.put(`/leads/${lead.id}/nurture-messages/${nurtureMessage.id}`, {
        message: nextMessage,
        phase: nurtureMessage.phase,
      });
      const updatedLead = res.data?.lead;
      if (updatedLead) {
        upsertLeadState(updatedLead);
      } else {
        upsertLeadState({
          ...lead,
          nurture_messages: (lead.nurture_messages || []).map((item) => (
            item.id === nurtureMessage.id ? { ...item, message: nextMessage } : item
          )),
        });
      }
      setEditingNurtureId('');
      setNurtureEditText('');
      showToast({
        type: 'success',
        title: 'Draft Saved',
        message: 'The nurture message was updated.',
      });
    } catch (err) {
      console.error(err);
      setNurtureEditError(getErrorMessage(err, 'We could not save that nurture message.'));
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save that nurture message.'),
      });
    } finally {
      setSavingNurtureId('');
    }
  };

  const deleteNurtureMessage = async (lead, nurtureMessage, event) => {
    if (event) event.stopPropagation();
    if (!lead?.id || !nurtureMessage?.id || deletingNurtureId) return;
    setDeletingNurtureId(nurtureMessage.id);
    try {
      const res = await api.delete(`/leads/${lead.id}/nurture-messages/${nurtureMessage.id}`);
      const updatedLead = res.data?.lead || {
        ...lead,
        nurture_messages: (lead.nurture_messages || []).filter((item) => item.id !== nurtureMessage.id),
      };
      upsertLeadState(updatedLead);
      if (editingNurtureId === nurtureMessage.id) cancelNurtureEdit();
      showToast({
        type: 'success',
        title: 'Draft Deleted',
        message: 'The selected nurture message was removed.',
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Delete Failed',
        message: getErrorMessage(err, 'We could not delete that nurture message.'),
      });
    } finally {
      setDeletingNurtureId('');
    }
  };

  const convertLeadToCustomer = async (lead) => {
    if (!lead?.id || convertingLeadId) return;
    setConvertingLeadId(lead.id);
    try {
      const res = await api.post(`/leads/${lead.id}/convert-to-customer`);
      const updatedLead = res.data?.lead ? { ...res.data.lead, status: res.data.lead.status || 'converted' } : { ...lead, status: 'converted' };
      upsertLeadState(updatedLead);
      await loadLeads();
      showToast({
        type: 'success',
        title: 'Lead Converted',
        message: `${lead.name || 'This lead'} is now linked to a customer profile.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Conversion Failed',
        message: getErrorMessage(err, 'We could not convert that lead.'),
      });
    } finally {
      setConvertingLeadId('');
    }
  };

  const availableLeadStatuses = leadStatuses.length
    ? leadStatuses.map((item) => item.status_name)
    : ['new', 'contacted', 'qualified', 'proposal', 'negotiation', 'converted', 'won', 'lost'];
  const availableLeadSources = leadSources.length
    ? Array.from(new Set([...LEAD_SOURCE_OPTIONS, ...leadSources.map((item) => item.source_name)]))
        .filter((source) => LEAD_SOURCE_OPTIONS.includes(source))
    : LEAD_SOURCE_OPTIONS;
  const grouped = availableLeadStatuses.reduce((acc, status) => ({ ...acc, [status]: [] }), {});
  leads.forEach((lead) => {
    const statusKey = lead?.status || availableLeadStatuses[0] || 'new';
    if (!grouped[statusKey]) grouped[statusKey] = [];
    grouped[statusKey].push(lead);
  });

  return (
    <>
    <div className="p-4 lg:p-6 space-y-4 overflow-x-hidden" data-testid="leads-page">
      {loadError && (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
          <span className="flex-1">{loadError}</span>
          <button
            type="button"
            onClick={() => setLoadError('')}
            className="flex-shrink-0 text-red-400 hover:text-red-600"
            aria-label="Dismiss error"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Lead Pipeline</h1>
          <p className="text-slate-400 text-sm mt-1">{leads.length} leads total</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowBulkUpload(true)}
            className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition-all hover:bg-slate-50"
            data-testid="bulk-upload-leads-btn"
          >
            Bulk Upload
          </button>
          <button
            onClick={nurtureAllLeads}
            disabled={nurturingAll || sendingNurtureAll || pendingBatchNurture.length > 0}
            className="flex items-center gap-2 px-3 py-2 bg-purple-50 border border-purple-200 text-purple-600 rounded-lg text-sm font-medium hover:bg-purple-100 transition-all disabled:opacity-50"
            data-testid="auto-nurture-all-btn"
          >
            <Sparkles size={16} className={nurturingAll ? 'animate-spin' : ''} /> {nurturingAll ? 'Nurturing All...' : 'AI Auto-Nurture All'}
          </button>
          {pendingBatchNurture.length > 0 && (
            <button
              onClick={sendAllGeneratedNurture}
              disabled={sendingNurtureAll || nurturingAll}
              className="flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-all disabled:opacity-50"
              data-testid="send-nurture-all-btn"
            >
              <MessageSquare size={16} /> {sendingNurtureAll ? 'Sending All...' : `Send Message to All (${pendingBatchNurture.length})`}
            </button>
          )}
          <button
            onClick={() => { setShowForm(true); setCreateLeadError(''); setForm({ name: '', email: '', phone: '', company: '', source: 'web_chat', notes: '' }); }}
            className="flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-all"
            data-testid="add-lead-btn"
          >
            <Plus size={16} /> Add Lead
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search leads..."
            className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20"
            data-testid="lead-search-input"
          />
        </div>
        {['', 'hot', 'warm', 'cold'].map((g) => (
          <button
            key={g}
            onClick={() => setFilterGrade(g)}
            className={`px-3 py-1.5 text-xs rounded-lg font-medium transition-colors ${filterGrade === g ? 'bg-blue-50 text-blue-600 border border-blue-200' : 'text-slate-400 hover:text-slate-600 border border-slate-200'}`}
            data-testid={`filter-grade-${g || 'all'}`}
          >
            {g || 'All'}
          </button>
        ))}
        {[
          { key: '', label: 'Any source' },
          { key: 'facebook', label: 'Facebook' },
          { key: 'instagram', label: 'Instagram' },
          { key: 'whatsapp', label: 'WhatsApp' },
          { key: 'email', label: 'Email' },
          { key: 'web_chat', label: 'Widget' },
        ].map((s) => (
          <button
            key={`src-${s.key || 'all'}`}
            onClick={() => setFilterSource(s.key)}
            className={`px-3 py-1.5 text-xs rounded-lg font-medium transition-colors ${filterSource === s.key ? 'bg-sky-50 text-sky-600 border border-sky-200' : 'text-slate-400 hover:text-slate-600 border border-slate-200'}`}
            data-testid={`filter-source-${s.key || 'all'}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {!loading && leads.length === 0 && !loadError && (
        <div
          className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-10 text-center"
          data-testid="leads-empty-state"
        >
          <h3 className="text-base font-semibold text-slate-700">No leads yet</h3>
          <p className="mt-1 text-sm text-slate-500">
            Leads from the chat widget, WhatsApp, or Instagram will appear here automatically.
            You can also add one manually with the Add Lead button above.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="lead-pipeline">
        {Object.entries(grouped).map(([status, items]) => (
          <div key={status} className="min-w-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className={`text-xs px-2 py-0.5 rounded-md font-medium capitalize ${STATUS_COLORS[status] || 'bg-slate-100 text-slate-500'}`}>{status}</span>
                <span className="text-xs text-slate-400">{items.length}</span>
              </div>
            </div>
            <div className="space-y-3">
              {items.map((lead) => (
                <div
                  key={lead.id}
                  onClick={() => openLeadDetail(lead)}
                  className="bg-white border border-slate-100 rounded-xl p-4 hover:border-blue-200 transition-all cursor-pointer group"
                  data-testid={`lead-card-${lead.id}`}
                >
                  {getLatestDraft(lead) && (
                    <div className="mb-3 flex justify-end">
                      <button
                        onClick={(e) => openNurtureComposer(lead, getLatestDraft(lead), e)}
                        disabled={sendingNurtureId === getLatestDraft(lead)?.id}
                        className="px-2.5 py-1 text-[11px] font-medium rounded-lg bg-indigo-50 border border-indigo-200 text-indigo-700 hover:bg-indigo-100 disabled:opacity-60"
                        data-testid={`send-nurture-${lead.id}`}
                      >
                        {sendingNurtureId === getLatestDraft(lead)?.id ? 'Sending...' : 'Send Draft'}
                      </button>
                    </div>
                  )}
                  {lead.source && (
                    <div className="mb-2">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold ${SOURCE_COLORS[lead.source] || 'bg-slate-100 text-slate-500'}`}>
                        {SOURCE_LABELS[lead.source] || lead.source.replace('_', ' ')}
                      </span>
                    </div>
                  )}
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <button
                        onClick={(e) => deleteLead(lead, e)}
                        disabled={deletingLeadId === lead.id}
                        className="w-5 h-5 rounded-full bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 inline-flex items-center justify-center flex-shrink-0 disabled:opacity-60"
                        title="Delete Lead"
                        data-testid={`delete-lead-${lead.id}`}
                      >
                        <Trash2 size={12} />
                      </button>
                      <LeadAvatar lead={lead} />
                      <h4 className="text-sm font-semibold text-slate-700 truncate">{lead.name}</h4>
                    </div>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${GRADE_COLORS[lead.grade] || GRADE_COLORS.warm}`}>{lead.grade}</span>
                  </div>
                  <p className="text-xs text-slate-400 mb-2">{lead.company}</p>
                  {Array.isArray(lead.tags) && lead.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2" data-testid={`lead-tags-${lead.id}`}>
                      {lead.tags.slice(0, 4).map((t) => (
                        <span
                          key={t}
                          className={`text-[9px] px-1.5 py-0.5 rounded border font-medium ${
                            t === 'hot_lead' ? 'bg-red-50 text-red-500 border-red-200' :
                            t === 'social_lead' ? 'bg-fuchsia-50 text-fuchsia-600 border-fuchsia-200' :
                            t === 'interested' ? 'bg-emerald-50 text-emerald-600 border-emerald-200' :
                            t === 'contact_shared' ? 'bg-sky-50 text-sky-600 border-sky-200' :
                            'bg-slate-50 text-slate-500 border-slate-200'
                          }`}
                        >
                          {t.replace('_', ' ')}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <div className="h-1.5 w-16 bg-slate-100 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${lead.score >= 80 ? 'bg-emerald-500' : lead.score >= 50 ? 'bg-amber-500' : 'bg-gray-600'}`} style={{ width: `${lead.score}%` }}></div>
                      </div>
                      <span className="text-[10px] text-slate-400">{lead.score}%</span>
                    </div>
                    <span className="text-[10px] text-slate-300">{lead.source?.replace('_', ' ')}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {selectedLead && (
        <div
          className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center overflow-y-auto p-4"
          data-testid="lead-detail-modal"
          onClick={closeLeadDetail}
        >
          <div
            className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg max-h-[calc(100vh-2rem)] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex-none bg-white border-b border-slate-100 p-6">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <LeadAvatar lead={selectedLead} className="w-12 h-12" textClass="text-lg" />
                  <div className="min-w-0">
                    <h3 className="text-xl font-bold text-slate-900 truncate">{selectedLead.name}</h3>
                    <p className="text-sm text-slate-400 truncate">{selectedLead.company}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEditLead(selectedLead)}
                    className="px-2.5 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 rounded-lg text-xs font-medium hover:bg-slate-100 transition-colors"
                    data-testid="edit-lead-btn"
                  >
                    Edit
                  </button>
                  <button onClick={closeLeadDetail} className="text-slate-400 hover:text-slate-600" data-testid="close-lead-detail">
                    <X size={20} />
                  </button>
                </div>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-6" data-testid="lead-detail-scroll-body">
              <div>
                <div className="grid grid-cols-2 gap-4 mb-6">
                  <div className="bg-slate-50 rounded-lg p-3">
                    <p className="text-[10px] text-slate-400 mb-1">Score</p>
                    <p className="text-2xl font-bold text-slate-900">{selectedLead.score}</p>
                  </div>
                  <div className="bg-slate-50 rounded-lg p-3">
                    <p className="text-[10px] text-slate-400 mb-1">Grade</p>
                    <p className={`text-xl font-bold capitalize ${selectedLead.grade === 'hot' ? 'text-red-500' : selectedLead.grade === 'warm' ? 'text-amber-600' : 'text-blue-600'}`}>{selectedLead.grade}</p>
                  </div>
                </div>

                <div className="space-y-3 mb-6">
                  {selectedLead.email && <p className="text-sm text-slate-600 flex items-center gap-2"><Mail size={14} className="text-slate-400" /> {selectedLead.email}</p>}
                  {selectedLead.phone && <p className="text-sm text-slate-600 flex items-center gap-2"><Phone size={14} className="text-slate-400" /> {selectedLead.phone}</p>}
                  <p className="text-sm text-slate-600 flex items-center gap-2"><Building2 size={14} className="text-slate-400" /> {selectedLead.company || 'N/A'}</p>
                  <p className="text-sm text-slate-600 flex items-center gap-2"><Target size={14} className="text-slate-400" /> Source: {selectedLead.source?.replace('_', ' ')}</p>
                </div>

                {selectedLead.notes && (
                  <div className="bg-slate-50 rounded-lg p-3 mb-6">
                    <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-1">Notes</p>
                    <p className="text-sm text-slate-600">{selectedLead.notes}</p>
                  </div>
                )}

                {selectedLead.scoring_reason && (
                  <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 mb-6">
                    <p className="text-[10px] text-purple-600 uppercase tracking-wider mb-1 flex items-center gap-1"><Sparkles size={10} /> AI Scoring Insight</p>
                    <p className="text-sm text-slate-600">{selectedLead.scoring_reason}</p>
                    {selectedLead.phase && <p className="text-xs text-purple-600 mt-1.5">Phase: <span className="font-medium capitalize">{selectedLead.phase}</span></p>}
                    {selectedLead.next_action && <p className="text-xs text-purple-600 mt-1">Next: {selectedLead.next_action}</p>}
                  </div>
                )}
              </div>

              {selectedLead.nurture_messages && selectedLead.nurture_messages.length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6">
                  <p className="text-[10px] text-blue-600 uppercase tracking-wider mb-2 flex items-center gap-1"><Sparkles size={10} /> AI Nurture Messages</p>
                  <div className="max-h-72 space-y-2 overflow-y-auto pr-1" data-testid="lead-nurture-message-list">
                    {selectedLead.nurture_messages.slice(-3).map((nm) => (
                      <div key={nm.id} className="bg-white rounded-lg p-2.5 border border-blue-100">
                        {editingNurtureId === nm.id ? (
                          <div className="space-y-2">
                            <textarea
                              value={nurtureEditText}
                              onChange={(e) => {
                                setNurtureEditText(e.target.value);
                                if (nurtureEditError) setNurtureEditError('');
                              }}
                              rows={4}
                              className={`w-full resize-none rounded-lg border px-2.5 py-2 text-xs text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500/20 ${nurtureEditError ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-slate-50'}`}
                              data-testid={`edit-nurture-text-${nm.id}`}
                            />
                            {nurtureEditError && (
                              <p className="text-[10px] font-medium text-red-600">{nurtureEditError}</p>
                            )}
                            <div className="flex justify-end gap-2">
                              <button
                                type="button"
                                onClick={(e) => cancelNurtureEdit(e)}
                                disabled={savingNurtureId === nm.id}
                                className="px-2 py-1 text-[10px] font-medium rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                                data-testid={`cancel-nurture-edit-${nm.id}`}
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={(e) => saveNurtureEdit(selectedLead, nm, e)}
                                disabled={savingNurtureId === nm.id}
                                className="px-2 py-1 text-[10px] font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60"
                                data-testid={`save-nurture-edit-${nm.id}`}
                              >
                                {savingNurtureId === nm.id ? 'Saving...' : 'Save'}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <p className="text-xs text-slate-600">{nm.message}</p>
                        )}
                        <div className="flex items-center justify-between gap-2 mt-1">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] text-blue-500 capitalize">{nm.phase}</span>
                            <span className="text-[10px] text-slate-400">{new Date(nm.created_at).toLocaleDateString()}</span>
                            <span className={`text-[10px] px-1 py-0.5 rounded ${nm.sent ? 'bg-green-50 text-green-600' : 'bg-amber-50 text-amber-600'}`}>{nm.sent ? 'Sent' : 'Draft'}</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={(e) => openNurtureComposer(selectedLead, nm, e)}
                              disabled={sendingNurtureId === nm.id}
                              className="px-2 py-1 text-[10px] font-medium rounded-md bg-indigo-50 border border-indigo-200 text-indigo-700 hover:bg-indigo-100 disabled:opacity-60"
                              data-testid={`send-nurture-detail-${nm.id}`}
                            >
                              {sendingNurtureId === nm.id ? 'Sending...' : nm.sent ? 'Send Again' : 'Send'}
                            </button>
                            <button
                              type="button"
                              onClick={(e) => startNurtureEdit(nm, e)}
                              disabled={editingNurtureId === nm.id || savingNurtureId === nm.id}
                              className="px-2 py-1 text-[10px] font-medium rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                              data-testid={`edit-nurture-${nm.id}`}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={(e) => deleteNurtureMessage(selectedLead, nm, e)}
                              disabled={deletingNurtureId === nm.id}
                              className="px-2 py-1 text-[10px] font-medium rounded-md border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-60"
                              data-testid={`delete-nurture-${nm.id}`}
                            >
                              {deletingNurtureId === nm.id ? 'Deleting...' : 'Delete'}
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex-none border-t border-slate-100 bg-white p-6">
              <div className="grid grid-cols-2 gap-2 mb-4">
                <button
                  onClick={() => scoreLead(selectedLead.id)}
                  disabled={autoScoring}
                  className="flex items-center justify-center gap-2 px-4 py-2.5 bg-purple-50 border border-purple-200 text-purple-600 rounded-xl text-sm font-medium hover:bg-purple-100 transition-colors disabled:opacity-50"
                  data-testid="score-lead-btn"
                >
                  <Sparkles size={14} /> {autoScoring ? 'Scoring...' : 'AI Score'}
                </button>
                <button
                  onClick={() => nurtureLead(selectedLead.id)}
                  disabled={nurturingLead}
                  className="flex items-center justify-center gap-2 px-4 py-2.5 bg-indigo-50 border border-indigo-200 text-indigo-700 rounded-xl text-sm font-medium hover:bg-indigo-100 transition-colors disabled:opacity-50"
                  data-testid="nurture-lead-btn"
                >
                  <Sparkles size={14} /> {nurturingLead ? 'Nurturing...' : 'AI Nurture'}
                </button>
              </div>

              <div className="grid grid-cols-2 gap-2 mb-4">
                <button
                  onClick={() => openMessagePicker(selectedLead)}
                  className="px-3 py-2.5 bg-blue-50 border border-blue-200 text-blue-600 rounded-xl text-sm font-medium hover:bg-blue-100 transition-colors"
                  data-testid="message-lead-btn"
                >
                  <span className="inline-flex items-center gap-1"><MessageSquare size={14} /> Message</span>
                </button>
                <button
                  onClick={() => handleEmailLead(selectedLead)}
                  className="px-3 py-2.5 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-xl text-sm font-medium hover:bg-emerald-100 transition-colors"
                  data-testid="email-lead-btn"
                >
                  <span className="inline-flex items-center gap-1"><Mail size={14} /> Email</span>
                </button>
                <button
                  onClick={() => convertLeadToCustomer(selectedLead)}
                  disabled={convertingLeadId === selectedLead.id || ['converted', 'won'].includes(selectedLead.status)}
                  className="px-3 py-2.5 bg-indigo-50 border border-indigo-200 text-indigo-700 rounded-xl text-sm font-medium hover:bg-indigo-100 transition-colors disabled:opacity-60"
                  data-testid="convert-lead-btn"
                >
                  <span className="inline-flex items-center gap-1"><UserCheck size={14} /> {convertingLeadId === selectedLead.id ? 'Converting...' : 'Convert to Customer'}</span>
                </button>
                <button
                  onClick={() => deleteLead(selectedLead)}
                  disabled={deletingLeadId === selectedLead.id}
                  className="px-3 py-2.5 bg-red-50 border border-red-200 text-red-700 rounded-xl text-sm font-medium hover:bg-red-100 transition-colors disabled:opacity-60"
                  data-testid="delete-lead-detail-btn"
                >
                  <span className="inline-flex items-center gap-1"><Trash2 size={14} /> {deletingLeadId === selectedLead.id ? 'Deleting...' : 'Delete Lead'}</span>
                </button>
              </div>

              <select
                value={selectedLead.status}
                onChange={(e) => updateStatus(selectedLead.id, e.target.value)}
                className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500/20"
                data-testid="lead-status-select"
              >
                {availableLeadStatuses.map((s) => (
                  <option key={s} value={s}>{formatStageLabel(s)}</option>
                ))}
              </select>
              {getLatestLeadStageUpdate(selectedLead)?.reason && (
                <p
                  className="mt-2 text-[11px] text-slate-500"
                  title={getLatestLeadStageUpdate(selectedLead).reason}
                  data-testid="lead-stage-reason"
                >
                  {getLatestLeadStageUpdate(selectedLead).source === 'manual_update' ? 'Manual update' : 'Auto-updated'}: {getLatestLeadStageUpdate(selectedLead).reason}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {showEditLead && selectedLead && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="edit-lead-modal" onClick={() => setShowEditLead(false)}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-900">Edit Lead</h3>
                <button onClick={() => setShowEditLead(false)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="space-y-4">
                <input value={editLeadForm.name} onChange={(e) => setEditLeadForm({ ...editLeadForm, name: e.target.value })} placeholder="Full Name" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <input value={editLeadForm.email} onChange={(e) => setEditLeadForm({ ...editLeadForm, email: e.target.value })} placeholder="Email" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <input value={editLeadForm.phone} onChange={(e) => setEditLeadForm({ ...editLeadForm, phone: e.target.value })} placeholder="Phone" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <input value={editLeadForm.company} onChange={(e) => setEditLeadForm({ ...editLeadForm, company: e.target.value })} placeholder="Company" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <select value={editLeadForm.source} onChange={(e) => setEditLeadForm({ ...editLeadForm, source: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm">
                  {availableLeadSources.map((s) => (
                    <option key={s} value={s}>{s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</option>
                  ))}
                </select>
                <select value={editLeadForm.status} onChange={(e) => setEditLeadForm({ ...editLeadForm, status: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm">
                  {availableLeadStatuses.map((s) => (
                    <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                  ))}
                </select>
                <textarea value={editLeadForm.notes} onChange={(e) => setEditLeadForm({ ...editLeadForm, notes: e.target.value })} placeholder="Notes" rows={3} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm resize-none" />
                <button onClick={saveLeadEdits} className="w-full py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700">Save Changes</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showMethodPicker && selectedLead && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="lead-message-methods-modal" onClick={() => setShowMethodPicker(false)}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-bold text-slate-900">Choose Message Method</h3>
                <button onClick={() => setShowMethodPicker(false)} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
              </div>
              <p className="text-sm text-slate-500 mb-4">Choose the channel for this lead.</p>
              <div className="space-y-2">
                {messageMethods.map((method, idx) => {
                  const meta = CHANNEL_META[method.channel] || CHANNEL_META.whatsapp;
                  const Icon = meta.icon;
                  return (
                    <button
                      key={`${method.channel}-${idx}`}
                      onClick={() => handlePickMethod(method)}
                      className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg border border-slate-200 hover:border-blue-200 hover:bg-blue-50/40"
                      data-testid={`lead-method-${method.channel}`}
                    >
                      <span className="inline-flex items-center gap-2 text-sm text-slate-700"><Icon size={14} /> {meta.label}</span>
                      <span className="text-xs text-slate-400">{method.isPrimaryContact ? `Person is on ${meta.label}` : 'Open'}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {contactPrompt.open && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="lead-contact-prompt-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-md">
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-bold text-slate-900">Add Contact Details</h3>
                <button onClick={() => setContactPrompt({ open: false, mode: 'message', lead: null, method: null })} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
              </div>
              <p className="text-sm text-slate-500 mb-4">
                {contactPrompt.mode === 'email' ? 'Email is required before sending an email.' : 'Phone is required before starting a chat. Add any missing contacts below.'}
              </p>
              <div className="space-y-3">
                <input
                  value={contactForm.phone}
                  onChange={(e) => setContactForm({ ...contactForm, phone: e.target.value })}
                  placeholder="Phone"
                  className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm"
                />
                <input
                  value={contactForm.email}
                  onChange={(e) => setContactForm({ ...contactForm, email: e.target.value })}
                  placeholder="Email"
                  className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm"
                />
                <button onClick={submitContacts} className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">Save and Continue</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {leadEmailComposer.open && leadEmailComposer.lead && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-sm"
          data-testid="lead-email-composer-modal"
          onClick={closeLeadEmailComposer}
        >
          <div
            className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="space-y-4 p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="text-lg font-bold text-slate-900">Email Lead</h3>
                  <p className="mt-1 truncate text-sm text-slate-500">
                    To {leadEmailComposer.lead.email}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={closeLeadEmailComposer}
                  disabled={leadEmailComposer.sending}
                  className="text-slate-400 hover:text-slate-600 disabled:opacity-50"
                  data-testid="close-lead-email-composer"
                >
                  <X size={18} />
                </button>
              </div>

              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wider text-slate-400">Subject</span>
                <input
                  value={leadEmailComposer.subject}
                  onChange={(e) => setLeadEmailComposer((prev) => ({ ...prev, subject: e.target.value }))}
                  disabled={leadEmailComposer.sending}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500/20 disabled:opacity-60"
                  data-testid="lead-email-subject"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wider text-slate-400">Message</span>
                <textarea
                  value={leadEmailComposer.body}
                  onChange={(e) => setLeadEmailComposer((prev) => ({ ...prev, body: e.target.value }))}
                  disabled={leadEmailComposer.sending}
                  rows={8}
                  className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm leading-6 text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500/20 disabled:opacity-60"
                  data-testid="lead-email-body"
                />
              </label>

              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={closeLeadEmailComposer}
                  disabled={leadEmailComposer.sending}
                  className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={submitLeadEmail}
                  disabled={leadEmailComposer.sending}
                  className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
                  data-testid="send-lead-email"
                >
                  {leadEmailComposer.sending ? 'Sending...' : 'Send Email'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {nurtureComposer.open && nurtureComposer.lead && nurtureComposer.message && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="lead-nurture-send-modal" onClick={closeNurtureComposer}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-bold text-slate-900">Send Nurture Message</h3>
                  <p className="text-sm text-slate-500">Choose the channel for this saved draft.</p>
                </div>
                <button onClick={closeNurtureComposer} className="text-slate-400 hover:text-slate-600">
                  <X size={18} />
                </button>
              </div>

              <div className="flex flex-wrap gap-2">
                {getLeadSendChannels(nurtureComposer.lead).map((method) => {
                  const meta = CHANNEL_META[method.channel] || CHANNEL_META.whatsapp;
                  const Icon = meta.icon;
                  const active = nurtureComposer.channel === method.channel;
                  return (
                    <button
                      key={method.channel}
                      type="button"
                      onClick={() => setNurtureComposer((prev) => ({ ...prev, channel: method.channel }))}
                      className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${active ? `${meta.bg} ${meta.border} ${meta.color}` : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'}`}
                    >
                      <Icon size={14} />
                      {meta.label}
                    </button>
                  );
                })}
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Draft</p>
                <p className="mt-2 text-sm leading-6 text-slate-700">{nurtureComposer.message.message}</p>
              </div>

              <button
                type="button"
                onClick={submitNurtureMessageSend}
                disabled={sendingNurtureId === nurtureComposer.message.id}
                className="w-full rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                data-testid="submit-nurture-send"
              >
                {sendingNurtureId === nurtureComposer.message.id
                  ? 'Sending...'
                  : `Send via ${(CHANNEL_META[nurtureComposer.channel] || {}).label || nurtureComposer.channel}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {showForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="new-lead-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-900">Add New Lead</h3>
                <button onClick={() => { setShowForm(false); setCreateLeadError(''); }} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="space-y-4">
                <input value={form.name} onChange={(e) => { setForm({ ...form, name: e.target.value }); if (createLeadError) setCreateLeadError(''); }} placeholder="Full Name *" className={`w-full px-4 py-2.5 bg-slate-50 border rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20 ${!form.name.trim() && createLeadError ? 'border-red-300' : 'border-slate-200'}`} data-testid="new-lead-name" />
                <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="Email" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-lead-email" />
                <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="Phone" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-lead-phone" />
                <input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} placeholder="Company" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-lead-company" />
                <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-lead-source">
                  {availableLeadSources.map((s) => (
                    <option key={s} value={s}>{s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</option>
                  ))}
                </select>
                <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="Notes" rows={3} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20 resize-none" data-testid="new-lead-notes" />
                {createLeadError && (
                  <div className="flex items-start gap-2 px-3 py-2.5 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
                    <span className="mt-0.5 flex-shrink-0">⚠</span>
                    <span>{createLeadError}</span>
                  </div>
                )}
                <button
                  onClick={createLead}
                  disabled={creatingLead}
                  className="w-full py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15 disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                  data-testid="create-lead-submit"
                >
                  {creatingLead ? (
                    <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /><span>Creating…</span></>
                  ) : 'Create Lead'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <BulkUploadModal
        isOpen={showBulkUpload}
        onClose={() => { if (!bulkUploading) setShowBulkUpload(false); }}
        title="Bulk Upload Leads"
        subtitle="Import a spreadsheet of leads in one pass, with duplicate-safe create or update behavior."
        entityLabel="Lead"
        uploading={bulkUploading}
        onUpload={handleBulkUpload}
        templateHeaders={LEAD_BULK_TEMPLATE_HEADERS}
        templateSample={LEAD_BULK_TEMPLATE_SAMPLE}
        guideRows={LEAD_BULK_GUIDE_ROWS}
      />

      {loading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Loading leads">
          {Array.from({ length: 6 }).map((_, i) => <LeadCardSkeleton key={i} />)}
        </div>
      )}
    </div>
    {confirmDialog}
    </>
  );
}
