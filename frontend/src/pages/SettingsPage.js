import { useState, useEffect, useCallback, useRef } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { resolveMediaUrl } from '@/lib/backend-url';
import { normalizeAvatarUrl, displayNameInitial } from '@/lib/avatar';
import { PHONE_COUNTRIES } from '@/lib/phoneCountries';
import {
  getWhatsAppBridgeMessage,
  getWhatsAppBridgeProgress,
  isWhatsAppBridgeConnecting,
  normalizeWhatsAppBridgeState,
} from '@/lib/whatsappBridgeStatus';
import AiSettingsTab from '@/components/settings/AiSettingsTab';
import UnificationTab from '@/components/settings/UnificationTab';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import {
  Shield,
  Users,
  Bell,
  Key,
  Globe,
  Bot,
  Save,
  Plus,
  X,
  Trash2,
  Eye,
  EyeOff,
  Check,
  AlertCircle,
  MessageSquare,
  Package,
  HelpCircle,
  Lock,
  CheckCircle,
  Link2,
  Copy,
  Activity,
  Edit,
  UserMinus,
  ChevronLeft,
  ChevronRight,
  Phone,
  Mail,
  MapPin,
  Building2,
  Image,
  Briefcase,
  UserCircle,
  Wand2,
  QrCode,
  Smile,
  TrendingUp,
  HeadphonesIcon,
  Sparkles,
  Zap,
} from 'lucide-react';

const CHANNEL_CONFIG = {
  whatsapp: { label: 'WhatsApp', color: 'emerald', fields: [], bgClass: 'bg-emerald-50', textClass: 'text-emerald-500' },
  instagram: { label: 'Instagram', color: 'pink', fields: ['page_id', 'access_token'], bgClass: 'bg-pink-50', textClass: 'text-pink-500' },
  facebook: { label: 'Facebook Messenger', color: 'blue', fields: ['page_id', 'access_token'], bgClass: 'bg-blue-50', textClass: 'text-blue-500' },
  email: { label: 'Email', color: 'sky', fields: ['email_address', 'email_provider', 'imap_host', 'smtp_host'], bgClass: 'bg-sky-50', textClass: 'text-sky-500' },
  web_chat: { label: 'Web Chat Widget', color: 'violet', fields: [], bgClass: 'bg-violet-50', textClass: 'text-violet-500' },
};
const META_CHANNELS = ['whatsapp', 'instagram', 'facebook'];

/** Order shown in Settings → Channels; fills gaps if API omits a row (e.g. legacy DB). */
const CHANNEL_LIST_ORDER = ['whatsapp', 'instagram', 'facebook', 'email', 'web_chat'];

function LogoUploadTrigger({ onClick, disabled = false, uploading = false, title = 'Change image' }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full border-2 border-white bg-blue-600 text-white shadow transition-colors hover:bg-blue-500 disabled:opacity-60"
      title={title}
    >
      {uploading ? <div className="h-2.5 w-2.5 rounded-full border-2 border-white border-t-transparent animate-spin" /> : <Plus size={10} className="text-white" />}
    </button>
  );
}

function normalizeListPayload(payload, keys = []) {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== 'object') return [];
  for (const key of keys) {
    if (Array.isArray(payload[key])) return payload[key];
  }
  if (Array.isArray(payload.data)) return payload.data;
  return [];
}

const MCP_PRESETS = [
  {
    id: 'claude-desktop',
    name: 'Claude Desktop',
    description: 'Local desktop bridge with a ready-to-use localhost endpoint and desktop-friendly defaults.',
    endpoint: 'http://127.0.0.1:8811/mcp',
    region: 'desktop-local',
    status: 'active',
    capabilities: {
      platform: 'Claude Desktop',
      transport: 'Local HTTP bridge',
      auth: 'Local session',
      connection_details: 'Use this when your MCP bridge runs locally on port 8811.',
    },
  },
  {
    id: 'cursor-ide',
    name: 'Cursor / Codex IDE',
    description: 'Preset for IDE-based local MCP servers with a loopback endpoint and low-friction connection details.',
    endpoint: 'http://127.0.0.1:8812/mcp',
    region: 'ide-local',
    status: 'active',
    capabilities: {
      platform: 'Cursor / Codex IDE',
      transport: 'Loopback HTTP',
      auth: 'Workspace token',
      connection_details: 'Use this when your editor exposes an MCP bridge on port 8812.',
    },
  },
  {
    id: 'https-gateway',
    name: 'Hosted HTTPS Gateway',
    description: 'Cloud-friendly preset for a remote MCP endpoint behind HTTPS with secure token auth.',
    endpoint: 'https://mcp.your-company.com/connect',
    region: 'global',
    status: 'active',
    capabilities: {
      platform: 'Hosted MCP',
      transport: 'HTTPS',
      auth: 'Bearer token',
      connection_details: 'Replace the hostname with your hosted MCP gateway if you connect through a cloud platform.',
    },
  },
];

function normalizeChannelsResponse(raw) {
  const rows = Array.isArray(raw) ? raw : [];
  const byKey = new Map();
  for (const row of rows) {
    const k = String(row?.channel || '').trim().toLowerCase();
    if (!k || k === 'twitter') continue;
    byKey.set(k, { ...row, channel: k });
  }
  const defaults = {
    whatsapp: { channel: 'whatsapp', display_name: 'WhatsApp', enabled: false, phone_number_id: '', access_token: '', webhook_url: '', verify_token: '' },
    instagram: { channel: 'instagram', display_name: 'Instagram', enabled: false, page_id: '', access_token: '', webhook_url: '', verify_token: '' },
    facebook: { channel: 'facebook', display_name: 'Facebook Messenger', enabled: false, page_id: '', access_token: '', webhook_url: '', verify_token: '' },
    email: {
      channel: 'email',
      display_name: 'Email',
      enabled: false,
      email_address: '',
      email_provider: 'smtp_imap',
      imap_host: '',
      imap_port: 993,
      imap_user: '',
      imap_pass_enc: '',
      smtp_host: '',
      smtp_port: 587,
      smtp_user: '',
      smtp_pass_enc: '',
      email_receive_enabled: true,
      email_send_enabled: true,
      webhook_url: '',
      api_key: '',
    },
    web_chat: { channel: 'web_chat', display_name: 'Web Chat Widget', enabled: true, webhook_url: '' },
  };
  return CHANNEL_LIST_ORDER.map((id) => {
    const existing = byKey.get(id);
    const base = defaults[id] || { channel: id, display_name: id, enabled: false };
    return existing ? { ...base, ...existing, channel: id } : base;
  });
}
const ROLE_OPTIONS = ['admin', 'company_agent'];
const ROLE_LABELS = { admin: 'Admin', company_agent: 'Company Agent' };
const PRODUCT_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
const MAX_PRODUCT_IMAGES = 3;
const MAX_PRODUCT_IMAGE_SIZE_MB = 5;

/* eslint-disable no-useless-escape -- punctuation class mirrors backend password policy */
const PASSWORD_SPECIAL_CHAR_RE = /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?`~]/;
/* eslint-enable no-useless-escape */

function validatePassword(password) {
  const errors = [];
  if (password.length < 8) errors.push('At least 8 characters');
  if (!/[a-zA-Z]/.test(password)) errors.push('At least one letter');
  if (!/[0-9]/.test(password)) errors.push('At least one number');
  if (!PASSWORD_SPECIAL_CHAR_RE.test(password)) errors.push('At least one special character');
  return { isValid: errors.length === 0, errors };
}

const _TEMPLATE_META = {
  Friendly:        { icon: Smile,          color: 'amber',   preview: 'Hi there! Happy to help you find exactly what you need. Here\'s what I found…' },
  Professional:    { icon: Briefcase,      color: 'slate',   preview: 'Thank you for your inquiry. The following information addresses your question…' },
  'Sales-Oriented':{ icon: TrendingUp,     color: 'blue',    preview: 'Great news — this is exactly what you\'re looking for. Here\'s why it fits your needs…' },
  'Support-Focused':{ icon: HeadphonesIcon,color: 'violet',  preview: 'I completely understand. Let me walk you through the solution step by step…' },
};
const _COLOR_CLASSES = {
  amber:  { ring: 'border-amber-300  bg-amber-50/50',  icon: 'bg-amber-100  text-amber-600',  badge: 'bg-amber-100  text-amber-700  border-amber-200' },
  slate:  { ring: 'border-slate-300  bg-slate-50/50',  icon: 'bg-slate-100  text-slate-600',  badge: 'bg-slate-100  text-slate-700  border-slate-200' },
  blue:   { ring: 'border-blue-300   bg-blue-50/50',   icon: 'bg-blue-100   text-blue-600',   badge: 'bg-blue-100   text-blue-700   border-blue-200' },
  violet: { ring: 'border-violet-300 bg-violet-50/50', icon: 'bg-violet-100 text-violet-600', badge: 'bg-violet-100 text-violet-700 border-violet-200' },
};

const _PREBUILT_TEMPLATES = [
  { name: 'Friendly',        icon: Smile,          color: 'amber',  style_prompt: 'Adopt a warm, conversational tone. Use second-person (\'you\'), greet the customer, and offer to help further at the end of each answer. Keep replies short — two to four sentences for simple questions.' },
  { name: 'Professional',    icon: Briefcase,      color: 'slate',  style_prompt: 'Adopt a clear, neutral, business-formal tone. Avoid contractions and slang. Lead each answer with the factual answer first, then add one sentence of context.' },
  { name: 'Sales-Oriented',  icon: TrendingUp,     color: 'blue',   style_prompt: 'Adopt a confident, helpful tone focused on resolving the customer\'s purchase intent. When the question is about a product, surface the key value proposition and the next step (price, availability, link) without exaggerating or inventing details. Do not pressure the customer.' },
  { name: 'Support-Focused', icon: HeadphonesIcon, color: 'violet', style_prompt: 'Adopt a patient, empathetic problem-solving tone. Acknowledge the customer\'s situation, restate the problem in one sentence so they know you understood, then give the next concrete step. Escalate to a human teammate when the context doesn\'t have a clear resolution.' },
];

function AiResponseTemplatesSection({ isAdmin }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);
  const [addTab, setAddTab] = useState('prebuilt'); // 'prebuilt' | 'custom'
  const [customName, setCustomName] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [adding, setAdding] = useState('');

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get('/ai/templates');
      setTemplates(Array.isArray(res.data) ? res.data : []);
    } catch (err) {
      setError(getErrorMessage(err, 'We could not load response templates.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const setDefault = useCallback(async (id) => {
    setBusyId(id);
    try {
      await api.post(`/ai/templates/${id}/set-default`);
      await loadTemplates();
      showToast({ type: 'success', title: 'Active template updated', message: 'New conversations will use this style.' });
    } catch (err) {
      showToast({ type: 'error', title: 'Could not update', message: getErrorMessage(err, 'Failed to change the active template.') });
    } finally {
      setBusyId('');
    }
  }, [loadTemplates]);

  const deleteTemplate = useCallback(async (id) => {
    setBusyId(id);
    try {
      await api.delete(`/ai/templates/${id}`);
      await loadTemplates();
      showToast({ type: 'success', title: 'Template deleted' });
    } catch (err) {
      showToast({ type: 'error', title: 'Could not delete', message: getErrorMessage(err, 'Failed to delete template.') });
    } finally {
      setBusyId('');
    }
  }, [loadTemplates]);

  const addPrebuilt = useCallback(async (prebuilt) => {
    setAdding(prebuilt.name);
    try {
      await api.post('/ai/templates', { name: prebuilt.name, style_prompt: prebuilt.style_prompt });
      await loadTemplates();
      setShowAddModal(false);
      showToast({ type: 'success', title: `"${prebuilt.name}" style added` });
    } catch (err) {
      showToast({ type: 'error', title: 'Could not add', message: getErrorMessage(err, 'Template may already exist.') });
    } finally {
      setAdding('');
    }
  }, [loadTemplates]);

  const addCustom = useCallback(async () => {
    if (!customName.trim() || !customPrompt.trim()) return;
    setAdding('custom');
    try {
      await api.post('/ai/templates', { name: customName.trim(), style_prompt: customPrompt.trim() });
      await loadTemplates();
      setShowAddModal(false);
      setCustomName('');
      setCustomPrompt('');
      showToast({ type: 'success', title: `"${customName.trim()}" style added` });
    } catch (err) {
      showToast({ type: 'error', title: 'Could not add', message: getErrorMessage(err, 'Template name may already be taken.') });
    } finally {
      setAdding('');
    }
  }, [customName, customPrompt, loadTemplates]);

  const existingNames = new Set(templates.map((t) => t.name));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-blue-100"><Sparkles size={14} className="text-blue-600" /></div>
          <div>
            <h3 className="text-sm font-semibold text-slate-900">AI Response Style</h3>
            <p className="text-xs text-slate-500">Controls the tone and style of every AI reply. Set one as the default for this workspace.</p>
          </div>
        </div>
        {isAdmin && (
          <button
            type="button"
            onClick={() => { setShowAddModal(true); setAddTab('prebuilt'); }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50 hover:border-slate-300 transition-colors shadow-sm"
          >
            <Plus size={12} /> Add style
          </button>
        )}
      </div>

      {loading ? (
        <div className="text-xs text-slate-400 py-3">Loading styles…</div>
      ) : error ? (
        <div className="space-y-2">
          <div className="text-xs text-red-500 py-2">{error}</div>
          {isAdmin && (
            <p className="text-xs text-slate-400">Use the <strong>Add style</strong> button above to add your first response style.</p>
          )}
        </div>
      ) : templates.length === 0 ? (
        <div className="space-y-2">
          <div className="text-xs text-slate-400 py-3">No response styles added yet.</div>
          {isAdmin && (
            <p className="text-xs text-slate-400">Click <strong>Add style</strong> to pick from 4 built-in styles or create your own.</p>
          )}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {templates.map((tmpl) => {
            const meta = _TEMPLATE_META[tmpl.name] || { icon: Zap, color: 'slate', preview: (tmpl.style_prompt || '').slice(0, 80) + '…' };
            const colors = _COLOR_CLASSES[meta.color] || _COLOR_CLASSES.slate;
            const Icon = meta.icon;
            const isActive = tmpl.is_default;
            const isBuiltin = Boolean(_TEMPLATE_META[tmpl.name]);
            return (
              <div
                key={tmpl.id}
                data-testid={`response-template-${tmpl.id}`}
                className={`rounded-xl border-2 p-4 transition-all ${isActive ? colors.ring + ' shadow-sm' : 'border-slate-100 bg-white hover:border-slate-200'}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <div className={`p-1.5 rounded-lg ${colors.icon}`}><Icon size={14} /></div>
                    <span className="text-sm font-semibold text-slate-900">{tmpl.name}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {isActive ? (
                      <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${colors.badge}`}>
                        <Check size={9} /> Active
                      </span>
                    ) : isAdmin ? (
                      <button
                        type="button"
                        disabled={busyId === tmpl.id}
                        onClick={() => setDefault(tmpl.id)}
                        className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-500 hover:bg-slate-50 hover:border-slate-300 disabled:opacity-40 transition-colors"
                      >
                        {busyId === tmpl.id ? 'Saving…' : 'Set active'}
                      </button>
                    ) : null}
                    {isAdmin && !isActive && !isBuiltin && (
                      <button
                        type="button"
                        disabled={busyId === tmpl.id}
                        onClick={() => deleteTemplate(tmpl.id)}
                        className="rounded-lg border border-red-100 px-1.5 py-1 text-[11px] text-red-400 hover:bg-red-50 hover:border-red-200 disabled:opacity-40 transition-colors"
                        title="Delete template"
                      >
                        <Trash2 size={11} />
                      </button>
                    )}
                  </div>
                </div>
                <p className="mt-2.5 text-[11px] leading-relaxed text-slate-500 italic border-l-2 border-slate-200 pl-2.5">
                  "{meta.preview}"
                </p>
              </div>
            );
          })}
        </div>
      )}

      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg">
            <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-slate-100">
              <h4 className="text-sm font-semibold text-slate-900">Add Response Style</h4>
              <button type="button" onClick={() => setShowAddModal(false)} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
            </div>
            <div className="flex gap-1 px-5 pt-4">
              {['prebuilt', 'custom'].map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setAddTab(tab)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${addTab === tab ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                >
                  {tab === 'prebuilt' ? 'Choose prebuilt' : 'Create custom'}
                </button>
              ))}
            </div>
            <div className="p-5">
              {addTab === 'prebuilt' ? (
                <div className="grid gap-3">
                  {_PREBUILT_TEMPLATES.map((pb) => {
                    const colors = _COLOR_CLASSES[pb.color] || _COLOR_CLASSES.slate;
                    const Icon = pb.icon;
                    const alreadyAdded = existingNames.has(pb.name);
                    return (
                      <div key={pb.name} className={`flex items-center justify-between rounded-xl border p-3 ${alreadyAdded ? 'opacity-50 bg-slate-50' : 'bg-white hover:border-slate-300'}`}>
                        <div className="flex items-center gap-2.5">
                          <div className={`p-1.5 rounded-lg ${colors.icon}`}><Icon size={13} /></div>
                          <div>
                            <p className="text-sm font-semibold text-slate-900">{pb.name}</p>
                            <p className="text-[10px] text-slate-400 mt-0.5 max-w-xs truncate">{pb.style_prompt.slice(0, 70)}…</p>
                          </div>
                        </div>
                        {alreadyAdded ? (
                          <span className="text-[10px] text-slate-400 font-medium">Added</span>
                        ) : (
                          <button
                            type="button"
                            disabled={adding === pb.name}
                            onClick={() => addPrebuilt(pb)}
                            className="rounded-lg bg-blue-600 text-white px-3 py-1 text-xs font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
                          >
                            {adding === pb.name ? 'Adding…' : 'Add'}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-700 mb-1">Style name</label>
                    <input
                      type="text"
                      value={customName}
                      onChange={(e) => setCustomName(e.target.value)}
                      placeholder="e.g. Casual & Brief"
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-700 mb-1">Style instruction for the AI</label>
                    <textarea
                      value={customPrompt}
                      onChange={(e) => setCustomPrompt(e.target.value)}
                      rows={4}
                      placeholder="Describe how the AI should respond — tone, length, what to emphasise…"
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                    />
                  </div>
                  <button
                    type="button"
                    disabled={!customName.trim() || !customPrompt.trim() || adding === 'custom'}
                    onClick={addCustom}
                    className="w-full rounded-lg bg-blue-600 text-white py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
                  >
                    {adding === 'custom' ? 'Adding…' : 'Add custom style'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { user, logout, refreshUser } = useAuth();
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const standaloneUnification = location.pathname.startsWith('/unification');
  const activeTab = standaloneUnification ? 'unification' : (searchParams.get('tab') || 'personal');
  const onboardingInviteMode =
    !standaloneUnification && activeTab === 'users' && searchParams.get('onboarding') === '1';
  const setActiveTab = (tab) => {
    if (standaloneUnification) return;
    setSearchParams({ tab }, { replace: true });
  };
  const [channels, setChannels] = useState([]);
  const [company, setCompany] = useState(null);
  const [companyValidationErrors, setCompanyValidationErrors] = useState({});
  const [users, setUsers] = useState([]);
  const [showKeyMap, setShowKeyMap] = useState({});
  const [saving, setSaving] = useState('');
  const [products, setProducts] = useState([]);
  const [faqs, setFaqs] = useState([]);
  const [personalSettings, setPersonalSettings] = useState(null);
  const [personalAvatarUrl, setPersonalAvatarUrl] = useState(() => normalizeAvatarUrl(localStorage.getItem('pe_avatar') || ''));
  const savePersonalAvatar = (rawUrl) => {
    const avatar = normalizeAvatarUrl(rawUrl || '');
    setPersonalAvatarUrl(avatar);
    if (avatar) {
      localStorage.setItem('pe_avatar', avatar);
    } else {
      localStorage.removeItem('pe_avatar');
    }
    window.dispatchEvent(new CustomEvent('pe-avatar-changed', { detail: avatar }));
    return avatar;
  };
  const personalAvatarRef = useRef(null);
  const companyLogoInputRef = useRef(null);
  const settingsSidebarRef = useRef(null);
  const [companyLogoUploading, setCompanyLogoUploading] = useState(false);
  const [showProductForm, setShowProductForm] = useState(false);
  const [editingProduct, setEditingProduct] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [lightboxImages, setLightboxImages] = useState([]);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [showFaqForm, setShowFaqForm] = useState(false);
  const [productForm, setProductForm] = useState({ name: '', product_title: '', description: '', price: '', price_currency: 'USD', category: 'general', product_type: '', images: [] });
  const [productImageError, setProductImageError] = useState('');
  const [productDragActive, setProductDragActive] = useState(false);
  const [generatingDesc, setGeneratingDesc] = useState(false);
  const [genDescNudge, setGenDescNudge] = useState(false);
  const [faqForm, setFaqForm] = useState({ question: '', answer: '', category: 'general' });

  // Password change state
  const [passwordModal, setPasswordModal] = useState(null);
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [deleteAccountModal, setDeleteAccountModal] = useState(false);
  const [deleteAccountMethod, setDeleteAccountMethod] = useState(user?.auth_provider === 'email' ? 'password' : 'email');
  const [deleteAccountPassword, setDeleteAccountPassword] = useState('');
  const [deleteAccountCode, setDeleteAccountCode] = useState('');
  const [deleteAccountConfirmText, setDeleteAccountConfirmText] = useState('');
  const [deleteAccountError, setDeleteAccountError] = useState('');
  const [deleteAccountInfo, setDeleteAccountInfo] = useState('');
  const [deleteAccountSending, setDeleteAccountSending] = useState(false);
  const [deleteAccountSubmitting, setDeleteAccountSubmitting] = useState(false);

  // User edit state
  const [editUserModal, setEditUserModal] = useState(null);
  const [editUserForm, setEditUserForm] = useState({});
  const [showCreateUserModal, setShowCreateUserModal] = useState(false);
  const [createUserForm, setCreateUserForm] = useState({ name: '', email: '', role: 'company_agent', sub_role: '', status: 'active' });

  // Security state
  const [securityOverview, setSecurityOverview] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [, setLoginHistory] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [showApiKeyForm, setShowApiKeyForm] = useState(false);
  const [apiKeyName, setApiKeyName] = useState('');
  const [newApiKey, setNewApiKey] = useState('');

  // Webhook state
  const [webhookInfo, setWebhookInfo] = useState(null);

  // AI / MCP / Social / Logs state
  const [llmEngines, setLlmEngines] = useState([]);
  const [aiAgents, setAiAgents] = useState([]);
  const [aiConfigError, setAiConfigError] = useState('');
  const [mcpServers, setMcpServers] = useState([]);
  const [socialAccounts, setSocialAccounts] = useState([]);
  const [systemLogs, setSystemLogs] = useState([]);

  // Unification state
  const [unifiedProfiles, setUnifiedProfiles] = useState([]);
  const [mergeSuggestions, setMergeSuggestions] = useState([]);
  const [unificationLoading, setUnificationLoading] = useState(false);
  const [selectedUnifiedProfile, setSelectedUnifiedProfile] = useState(null);
  const [suggestionDetail, setSuggestionDetail] = useState(null);
  const [manualMergeIds, setManualMergeIds] = useState(['', '']);

  const [waQrPanelOpen, setWaQrPanelOpen] = useState(false);
  const [waQrLoading, setWaQrLoading] = useState(false);
  const [waQrImageSrc, setWaQrImageSrc] = useState('');
  const [waQrStatus, setWaQrStatus] = useState('');
  const [waQrProgress, setWaQrProgress] = useState(0);
  const [waQrMessage, setWaQrMessage] = useState('Starting WhatsApp session');
  const [waQrRetrying, setWaQrRetrying] = useState(false);
  const [waQrUpdatedAt, setWaQrUpdatedAt] = useState('');
  const [waQrError, setWaQrError] = useState('');
  const [waDisconnecting, setWaDisconnecting] = useState(false);
  const waQrPollRef = useRef(null);
  const waStatusPollRef = useRef(null);

  // AI Config interactive state
  const [editingLlmId, setEditingLlmId] = useState(null);
  const [llmDraft, setLlmDraft] = useState({});
  const [editingAgentId, setEditingAgentId] = useState(null);
  const [agentDraft, setAgentDraft] = useState({});
  const [showAddLlmForm, setShowAddLlmForm] = useState(false);
  const [addLlmForm, setAddLlmForm] = useState({ model_name: 'gemini-2.5-flash', model_category: 'text_generation', provider: 'gemini', temperature: 0.7, max_tokens: 2048 });
  const [showAddAgentForm, setShowAddAgentForm] = useState(false);
  const [addAgentForm, setAddAgentForm] = useState({ agent_type: 'support', llm_id: '', is_active: true, mcp_server_id: '' });

  // Integrations interactive state
  const [editingMcpId, setEditingMcpId] = useState(null);
  const [mcpDraft, setMcpDraft] = useState({});
  const [showAddMcpForm, setShowAddMcpForm] = useState(false);
  const [addMcpForm, setAddMcpForm] = useState({ endpoint: '', region: '', status: 'active', capabilities: {} });
  const [mcpPresetSaving, setMcpPresetSaving] = useState('');
  const [editingSocialPlatform, setEditingSocialPlatform] = useState(null);
  const [socialDraft, setSocialDraft] = useState({});

  const waChannelRow = channels.find((c) => c.channel === 'whatsapp');
  const waMetaBlocksQr = !!(
    String(waChannelRow?.phone_number_id || '').trim() && String(waChannelRow?.access_token || '').trim()
  );
  const waQrBlocksMeta = waQrStatus === 'ready';
  const waQrNormalizedStatus = normalizeWhatsAppBridgeState(waQrStatus);
  const waQrIsConnecting = isWhatsAppBridgeConnecting(waQrNormalizedStatus);
  const waQrShowProgress = waQrPanelOpen && waQrNormalizedStatus && waQrNormalizedStatus !== 'qr_required';

  const isAdmin = user?.role === 'admin';
  const selectedLlmEngine = llmEngines.find((engine) => engine.is_selected) || null;
  const availableCreateRoles = ROLE_OPTIONS;
  const getAvailableEditRoles = () => ROLE_OPTIONS;

  const refreshAiConfig = useCallback(async () => {
    setAiConfigError('');
    const [le, ag] = await Promise.allSettled([
      api.get('/ai/llm-engines'),
      api.get('/ai/agents'),
    ]);

    const errors = [];
    if (le.status === 'fulfilled') {
      setLlmEngines(normalizeListPayload(le.value.data, ['engines', 'models']));
    } else {
      errors.push(`LLM engines: ${getErrorMessage(le.reason, 'failed to load')}`);
    }
    if (ag.status === 'fulfilled') {
      setAiAgents(normalizeListPayload(ag.value.data, ['agents']));
    } else {
      errors.push(`AI agents: ${getErrorMessage(ag.reason, 'failed to load')}`);
    }
    if (errors.length) {
      const message = errors.join(' | ');
      setAiConfigError(message);
      showToast({
        type: 'error',
        title: 'AI Settings Load Failed',
        message,
        dedupeKey: 'ai-config-load',
      });
    }
  }, []);

  const loadUnificationData = useCallback(async () => {
    setUnificationLoading(true);
    try {
      const [profilesRes, suggestionsRes] = await Promise.all([
        api.get('/identity/profiles').catch(() => ({ data: [] })),
        api.get('/identity/suggestions').catch(() => ({ data: [] })),
      ]);
      setUnifiedProfiles(profilesRes.data || []);
      setMergeSuggestions(suggestionsRes.data || []);
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Load Failed',
        message: getErrorMessage(err, 'We could not load identity unification data.'),
      });
    }
    finally { setUnificationLoading(false); }
  }, []);

  const runAutoDetect = async () => {
    setUnificationLoading(true);
    try {
      const res = await api.post('/identity/auto-detect');
      await loadUnificationData();
      showToast({
        type: 'success',
        title: 'Scan Complete',
        message: `Found ${res.data.new_suggestions || 0} new merge suggestion${res.data.new_suggestions === 1 ? '' : 's'}.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Scan Failed',
        message: getErrorMessage(err, 'We could not run customer auto-detection.'),
      });
    }
    finally { setUnificationLoading(false); }
  };

  const acceptSuggestion = async (suggestionId) => {
    setUnificationLoading(true);
    try {
      await api.post(`/identity/suggestions/${suggestionId}/resolve`, { action: 'accept' });
      await loadUnificationData();
      showToast({
        type: 'success',
        title: 'Suggestion Accepted',
        message: 'The merge suggestion was accepted.',
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Accept Failed',
        message: getErrorMessage(err, 'We could not accept that suggestion.'),
      });
    } finally {
      setUnificationLoading(false);
    }
  };

  const rejectSuggestion = async (suggestionId) => {
    setUnificationLoading(true);
    try {
      await api.post(`/identity/suggestions/${suggestionId}/resolve`, { action: 'reject' });
      await loadUnificationData();
      showToast({
        type: 'success',
        title: 'Suggestion Rejected',
        message: 'The merge suggestion was rejected.',
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Reject Failed',
        message: getErrorMessage(err, 'We could not reject that suggestion.'),
      });
    } finally {
      setUnificationLoading(false);
    }
  };

  const manualMerge = async () => {
    const ids = manualMergeIds.filter(Boolean);
    if (ids.length < 2) {
      showToast({
        type: 'error',
        title: 'Selection Needed',
        message: 'Select at least two customers before merging profiles.',
      });
      return;
    }
    setUnificationLoading(true);
    try {
      await api.post('/identity/merge', { customer_ids: ids });
      setManualMergeIds(['', '']);
      await loadUnificationData();
      showToast({
        type: 'success',
        title: 'Profiles Merged',
        message: `${ids.length} customer profiles were merged successfully.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Merge Failed',
        message: getErrorMessage(err, 'We could not merge those customer profiles.'),
      });
    } finally {
      setUnificationLoading(false);
    }
  };

  const splitFromProfile = async (profileId, mappingId) => {
    requestConfirmation({
      title: 'Remove From Profile',
      description: 'Remove this customer from the unified profile. This can be reversed only by merging again later.',
      confirmLabel: 'Remove customer',
      onConfirm: async () => {
        setUnificationLoading(true);
        try {
          await api.post('/identity/split', {
            profile_id: profileId,
            customer_id: String(mappingId || '').trim(),
            mapping_ids: mappingId ? [String(mappingId).trim()] : [],
          });
          await loadUnificationData();
          setSelectedUnifiedProfile(null);
          showToast({
            type: 'success',
            title: 'Profile Updated',
            message: 'The customer was removed from the unified profile.',
          });
        } catch (err) {
          showToast({
            type: 'error',
            title: 'Split Failed',
            message: getErrorMessage(err, 'We could not split that customer from the unified profile.'),
          });
        } finally {
          setUnificationLoading(false);
        }
      },
    });
  };

  const handleUnificationIdentityEvent = useCallback(async () => {
    await loadUnificationData();
    if (!selectedUnifiedProfile?.id) return;
    try {
      const profileRes = await api.get(`/identity/profiles/${selectedUnifiedProfile.id}`);
      setSelectedUnifiedProfile(profileRes.data || null);
    } catch {
      setSelectedUnifiedProfile(null);
    }
  }, [loadUnificationData, selectedUnifiedProfile?.id]);

  const loadSettings = useCallback(async () => {
    try {
      if (onboardingInviteMode) {
        const [co, personal] = await Promise.all([
          api.get('/settings/company').catch(() => ({ data: null })),
          api.get('/settings/personal').catch(() => ({ data: null })),
        ]);

        setChannels(normalizeChannelsResponse([]));
        setCompany(co.data || null);
        setUsers(user ? [{
          id: user.id || 'current-user',
          name: user.name || '',
          email: user.email || '',
          role: user.role || 'admin',
          sub_role: user.sub_role || '',
          status: user.status || 'active',
          last_login: user.last_login || null,
          auth_provider: user.auth_provider || 'email',
        }] : []);
        setProducts([]);
        setFaqs([]);

        const fallbackPersonal = {
          name: user?.name || '',
          company_name: co?.data?.company_name || '',
          email: user?.email || '',
          mobile_number: user?.phone || '',
          role: user?.role || '',
          website_address: co?.data?.website_address || '',
          address_info: co?.data?.address_info || '',
          locale_information: co?.data?.locale_information || co?.data?.language || 'en',
          timezone: co?.data?.timezone || 'UTC',
          preferred_language: co?.data?.language || 'en',
          date_format: co?.data?.date_format || 'YYYY-MM-DD',
          currency: co?.data?.currency || 'USD',
        };
        setPersonalSettings(personal.data || fallbackPersonal);
        if (personal.data?.avatar) savePersonalAvatar(personal.data.avatar);
        return;
      }

      const shouldLoadChannels = activeTab === 'channels';
      const shouldLoadUsers = activeTab === 'users';
      const shouldLoadKnowledge = activeTab === 'templates';

      const [co, personal, ch, us, pr, fq] = await Promise.all([
        api.get('/settings/company').catch(() => ({ data: null })),
        api.get('/settings/personal').catch(() => ({ data: null })),
        shouldLoadChannels ? api.get('/settings/channels').catch(() => ({ data: [] })) : Promise.resolve(null),
        shouldLoadUsers ? api.get('/users').catch(() => ({ data: [] })) : Promise.resolve(null),
        shouldLoadKnowledge ? api.get('/company-data/products').catch(() => ({ data: [] })) : Promise.resolve(null),
        shouldLoadKnowledge ? api.get('/company-data/faqs').catch(() => ({ data: [] })) : Promise.resolve(null),
      ]);

      setCompany(co.data || null);
      if (ch) setChannels(normalizeChannelsResponse(ch.data));
      if (us) setUsers(us.data || []);
      if (pr) setProducts(pr.data || []);
      if (fq) setFaqs(fq.data || []);

      const fallbackPersonal = {
        name: user?.name || '',
        company_name: co?.data?.company_name || '',
        email: user?.email || '',
        mobile_number: user?.phone || '',
        role: user?.role || '',
        website_address: co?.data?.website_address || '',
        address_info: co?.data?.address_info || '',
        locale_information: co?.data?.locale_information || co?.data?.language || 'en',
        timezone: co?.data?.timezone || 'UTC',
        preferred_language: co?.data?.language || 'en',
        date_format: co?.data?.date_format || 'YYYY-MM-DD',
        currency: co?.data?.currency || 'USD',
      };
      setPersonalSettings(personal.data || fallbackPersonal);
      if (personal.data?.avatar) savePersonalAvatar(personal.data.avatar);
    } catch (err) {
      console.error(err);
      setPersonalSettings({
        name: user?.name || '',
        company_name: '',
        email: user?.email || '',
        mobile_number: user?.phone || '',
        role: user?.role || '',
        website_address: '',
        address_info: '',
        locale_information: 'en',
        timezone: 'UTC',
        preferred_language: 'en',
        date_format: 'YYYY-MM-DD',
        currency: 'USD',
      });
    }
  }, [activeTab, onboardingInviteMode, user]);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  useEffect(() => {
    if (!onboardingInviteMode || !isAdmin || searchParams.get('invite') !== '1') return;
    const firstRole = availableCreateRoles[0] || 'company_agent';
    setCreateUserForm((prev) => ({ ...prev, role: prev.role || firstRole }));
    setShowCreateUserModal(true);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('invite');
      return next;
    }, { replace: true });
  }, [onboardingInviteMode, isAdmin, searchParams, setSearchParams, availableCreateRoles]);

  useEffect(() => {
    if (standaloneUnification) return;
    if (searchParams.get('tab') === 'email') {
      setSearchParams({ tab: 'channels' }, { replace: true });
    }
  }, [standaloneUnification, searchParams, setSearchParams]);

  useEffect(() => {
    if (standaloneUnification) return;
    const linked = searchParams.get('oauth_linked');
    const linkErr = searchParams.get('oauth_link_error');
    if (!linked && !linkErr) return;
    if (typeof refreshUser === 'function') {
      refreshUser();
    }
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.delete('oauth_linked');
        p.delete('oauth_link_error');
        if (!p.get('tab')) p.set('tab', 'security');
        return p;
      },
      { replace: true },
    );
  }, [standaloneUnification, searchParams, setSearchParams, refreshUser]);

  // Lazy-load data for AI/Integrations/Logs tabs
  useEffect(() => {
    if (activeTab === 'ai') {
      refreshAiConfig();
    }
    if (activeTab === 'integrations') {
      api.get('/mcp/servers').catch(() => ({ data: [] })).then(r => setMcpServers(r.data || []));
    }
    if (activeTab === 'channels') {
      api.get('/social/accounts').catch(() => ({ data: [] })).then(r => setSocialAccounts(r.data || []));
      api.get('/webhooks/info').catch(() => ({ data: null })).then((r) => {
        if (r?.data) setWebhookInfo((prev) => ({ ...(prev || {}), ...r.data }));
      });
    }
    if (activeTab === 'logs') {
      api.get('/system/logs?limit=50').catch(() => ({ data: [] })).then(r => setSystemLogs(r.data || []));
    }
  }, [activeTab, refreshAiConfig]);

  useEffect(() => {
    if (activeTab !== 'channels') return;
    setChannels((prev) =>
      prev.map((ch) => {
        if (!META_CHANNELS.includes(ch.channel)) return ch;
        const fallbackWebhook = (webhookInfo?.webhook_urls?.[ch.channel] || '').trim();
        const fallbackVerifyToken = (webhookInfo?.verify_token || '').trim();
        return {
          ...ch,
          webhook_url: (ch.webhook_url || '').trim() || fallbackWebhook,
          verify_token: (ch.verify_token || '').trim() || fallbackVerifyToken,
        };
      }),
    );
  }, [activeTab, webhookInfo]);

  const loadSecurity = async () => {
    try {
      const [overview, sess, hist, keys] = await Promise.all([
        api.get('/security/overview').catch(() => ({ data: null })),
        api.get('/security/sessions').catch(() => ({ data: [] })),
        api.get('/security/login-history').catch(() => ({ data: [] })),
        api.get('/security/api-keys').catch(() => ({ data: [] })),
      ]);
      setSecurityOverview(overview.data);
      setSessions(sess.data);
      setLoginHistory(hist.data);
      setApiKeys(keys.data);
    } catch (err) { console.error(err); }
  };

  const loadWebhookInfo = async () => {
    try {
      const res = await api.get('/webhooks/info');
      setWebhookInfo(res.data);
    } catch (err) { console.error(err); }
  };

  useEffect(() => {
    if (activeTab === 'security') loadSecurity();
    if (activeTab === 'webhooks') loadWebhookInfo();
    if (activeTab === 'notifications') loadNotifSettings();
    if (activeTab === 'unification') loadUnificationData();
  }, [activeTab, loadUnificationData]);

  // -- Notification settings --
  const [notifSettings, setNotifSettings] = useState(null);
  const [notifSaving, setNotifSaving] = useState(false);
  const [notifSaved, setNotifSaved] = useState(false);

  const loadNotifSettings = async () => {
    try {
      const res = await api.get('/notification-settings');
      setNotifSettings(res.data);
    } catch {}
  };

  const saveNotifSettings = async () => {
    if (!notifSettings) return;
    setNotifSaving(true);
    try {
      await api.put('/notification-settings', notifSettings);
      setNotifSaved(true);
      setTimeout(() => setNotifSaved(false), 2500);
      showToast({
        type: 'success',
        title: 'Notifications Saved',
        message: 'Your notification preferences were updated.',
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save your notification settings.'),
      });
    }
    setNotifSaving(false);
  };

  const getMetaChannelValidationError = (channel) => {
    if (!channel || !META_CHANNELS.includes(channel.channel) || !channel.enabled) return '';
    const verifyToken = (channel.verify_token || '').trim();
    if (channel.channel === 'whatsapp') {
      const phoneNumberId = (channel.phone_number_id || '').trim();
      const accessToken = (channel.access_token || '').trim();
      const usingMeta = !!(phoneNumberId || accessToken);
      if (!usingMeta) return '';
      const missing = [];
      if (!phoneNumberId) missing.push('Phone number ID');
      if (!accessToken) missing.push('Access Token');
      if (!verifyToken) missing.push('Verify Token');
      return missing.length ? `WhatsApp Meta API requires: ${missing.join(', ')}` : '';
    }
    const pageId = (channel.page_id || '').trim();
    const accessToken = (channel.access_token || '').trim();
    const missing = [];
    if (!pageId) missing.push('Page ID');
    if (!accessToken) missing.push('Access Token');
    if (!verifyToken) missing.push('Verify Token');
    return missing.length ? `${CHANNEL_CONFIG[channel.channel]?.label || channel.channel} requires: ${missing.join(', ')}` : '';
  };

  const saveChannel = async (channel) => {
    const validationError = getMetaChannelValidationError(channel);
    if (validationError) {
      showToast({
        type: 'error',
        title: 'Channel Incomplete',
        message: validationError,
      });
      return;
    }
    const fallbackVerifyToken = (webhookInfo?.verify_token || '').trim();
    const payload = META_CHANNELS.includes(channel.channel) && !(channel.verify_token || '').trim()
      ? { ...channel, verify_token: fallbackVerifyToken }
      : channel;
    setSaving(channel.channel);
    try {
      const res = await api.put(`/settings/channels/${channel.channel}`, payload);
      if (res?.data) {
        setChannels((prev) => prev.map((item) => (item.channel === channel.channel ? { ...item, ...res.data } : item)));
      }
      showToast({
        type: 'success',
        title: 'Channel Saved',
        message: `${CHANNEL_CONFIG[channel.channel]?.label || channel.channel} settings were updated.`,
      });
      setSaving('');
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, `We could not save ${CHANNEL_CONFIG[channel.channel]?.label || channel.channel} settings.`),
      });
      setSaving('');
    }
  };

  const updateChannelField = (channelName, field, value) => {
    setChannels(prev => prev.map(ch => ch.channel === channelName ? { ...ch, [field]: value } : ch));
  };

  const connectMcpPreset = async (preset) => {
    if (!preset?.endpoint || !isAdmin) return;
    setMcpPresetSaving(preset.id);
    const payload = {
      endpoint: preset.endpoint,
      region: preset.region,
      status: preset.status || 'active',
      capabilities: preset.capabilities || {},
    };
    try {
      const existing = mcpServers.find((server) => server.endpoint === preset.endpoint);
      const res = existing
        ? await api.put(`/mcp/servers/${existing.id}`, payload)
        : await api.post('/mcp/servers', payload);
      if (res?.data) {
        const serverData = { ...res.data, capabilities: res.data.capabilities || payload.capabilities };
        setMcpServers((prev) => {
          const withoutExisting = prev.filter((server) => server.id !== serverData.id && server.endpoint !== preset.endpoint);
          return [serverData, ...withoutExisting];
        });
      }
      setShowAddMcpForm(false);
      showToast({
        type: 'success',
        title: 'MCP Connected',
        message: `${preset.name} is now connected.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Connect Failed',
        message: getErrorMessage(err, `We could not connect ${preset.name}.`),
      });
    } finally {
      setMcpPresetSaving('');
    }
  };

  const validateCompanyProfile = (draft = {}) => {
    const checks = [
      ['company_name', 'Company name is required.', ''],
      ['industry', 'Industry is required.', ''],
      ['timezone', 'Timezone is required.', 'UTC'],
      ['language', 'Language is required.', 'en'],
    ];
    return checks.reduce((errors, [field, message, fallback]) => {
      if (!String(draft?.[field] || fallback || '').trim()) errors[field] = message;
      return errors;
    }, {});
  };

  const updateCompanyField = (field, value) => {
    setCompany((prev) => ({ ...(prev || {}), [field]: value }));
    setCompanyValidationErrors((prev) => {
      if (!prev[field]) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  };

  const saveCompany = async () => {
    const validationErrors = validateCompanyProfile(company);
    if (Object.keys(validationErrors).length) {
      setCompanyValidationErrors(validationErrors);
      showToast({
        type: 'error',
        title: 'Company Profile Incomplete',
        message: Object.values(validationErrors).join(' '),
      });
      return;
    }
    setSaving('company');
    try {
      const payload = {
        ...company,
        timezone: company?.timezone || 'UTC',
        language: company?.language || 'en',
      };
      const res = await api.put('/settings/company', payload);
      if (res?.data) setCompany(res.data);
      const updatedCompanyName = res?.data?.company_name || company?.company_name || '';
      if (updatedCompanyName) {
        localStorage.setItem('pe_company_name', updatedCompanyName);
        window.dispatchEvent(new CustomEvent('pe-company-changed', { detail: { company_name: updatedCompanyName } }));
      }
      setCompanyValidationErrors({});
      setSaving('company_done');
      showToast({
        type: 'success',
        title: 'Company Saved',
        message: `${res?.data?.company_name || company?.company_name || 'Company'} settings were updated.`,
      });
      setTimeout(() => setSaving(''), 2000);
    } catch (err) {
      console.error(err);
      const detail = err?.response?.data?.detail;
      if (detail && typeof detail === 'object' && Array.isArray(detail.fields)) {
        const fieldMap = {
          'Company name': 'company_name',
          Industry: 'industry',
          Timezone: 'timezone',
          Language: 'language',
        };
        const nextErrors = {};
        detail.fields.forEach((label) => {
          const key = fieldMap[label] || String(label || '').toLowerCase().replace(/\s+/g, '_');
          nextErrors[key] = `${label} is required.`;
        });
        setCompanyValidationErrors(nextErrors);
      }
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save the company profile.'),
      });
      setSaving('');
    }
  };

  const savePersonalProfile = async () => {
    if (!personalSettings) return;
    setSaving('personal');
    try {
      const payload = {
        name: personalSettings.name || '',
        email: personalSettings.email || '',
        mobile_number: personalSettings.mobile_number || '',
      };
      if (personalAvatarUrl) payload.avatar = personalAvatarUrl;
      try {
        const res = await api.put('/settings/personal', payload);
        setPersonalSettings(res.data);
        if (res.data?.avatar) savePersonalAvatar(res.data.avatar);
        else if (personalAvatarUrl) savePersonalAvatar(personalAvatarUrl);
      } catch (err) {
        if (err?.response?.status !== 404) throw err;
        await api.put(`/users/${user?.id}`, { name: payload.name, email: payload.email, phone: payload.mobile_number, avatar: personalAvatarUrl || undefined });
        setPersonalSettings(prev => ({ ...prev, ...payload }));
      }
      setSaving('personal_done');
      showToast({
        type: 'success',
        title: 'Profile Saved',
        message: 'Your personal settings were updated.',
      });
      setTimeout(() => setSaving(''), 2000);
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save your personal profile.'),
      });
      setSaving('');
    }
  };

  const handlePersonalAvatarChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const dataUrl = normalizeAvatarUrl(ev.target.result || '');
      savePersonalAvatar(dataUrl);
      // Immediately persist to backend so Layout can pick it up on next fetch
      try {
        await api.put('/settings/personal', { avatar: dataUrl });
      } catch {
        try { await api.put(`/users/${user?.id}`, { avatar: dataUrl }); } catch {}
      }
    };
    reader.readAsDataURL(file);
  };

  const handleCompanyLogoChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    if (!String(file.type || '').startsWith('image/')) {
      showToast({
        type: 'error',
        title: 'Unsupported File',
        message: 'Upload a JPG, PNG, WEBP, or GIF image for the company logo.',
      });
      return;
    }
    setCompanyLogoUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await api.post('/settings/company/logo/upload', formData);
      const nextCompany = res?.data?.company || { ...(company || {}), logo_url: res?.data?.url || '' };
      setCompany(nextCompany);
      showToast({
        type: 'success',
        title: 'Logo Updated',
        message: 'Company logo was updated.',
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Logo Upload Failed',
        message: getErrorMessage(err, 'We could not update the company logo.'),
      });
    } finally {
      setCompanyLogoUploading(false);
    }
  };


  const openPasswordModal = (u) => { setPasswordModal(u); setNewPassword(''); setConfirmPassword(''); setShowPassword(false); setPasswordError(''); setPasswordSuccess(''); };
  const closePasswordModal = () => { setPasswordModal(null); setNewPassword(''); setConfirmPassword(''); setPasswordError(''); setPasswordSuccess(''); };
  const openDeleteAccountModal = () => {
    setDeleteAccountModal(true);
    setDeleteAccountMethod(user?.auth_provider === 'email' ? 'password' : 'email');
    setDeleteAccountPassword('');
    setDeleteAccountCode('');
    setDeleteAccountConfirmText('');
    setDeleteAccountError('');
    setDeleteAccountInfo('');
  };
  const closeDeleteAccountModal = () => {
    if (deleteAccountSending || deleteAccountSubmitting) return;
    setDeleteAccountModal(false);
    setDeleteAccountPassword('');
    setDeleteAccountCode('');
    setDeleteAccountConfirmText('');
    setDeleteAccountError('');
    setDeleteAccountInfo('');
  };

  const handlePasswordChange = async () => {
    setPasswordError(''); setPasswordSuccess('');
    const { isValid, errors } = validatePassword(newPassword);
    if (!isValid) { setPasswordError(errors.join('; ')); return; }
    if (newPassword !== confirmPassword) { setPasswordError('Passwords do not match'); return; }
    setPasswordSaving(true);
    try {
      await api.put(`/users/${passwordModal.id}/password`, { new_password: newPassword });
      setPasswordSuccess('Password updated successfully!');
      setTimeout(() => closePasswordModal(), 1500);
    } catch (err) { setPasswordError(err.response?.data?.detail || 'Failed to update password'); }
    finally { setPasswordSaving(false); }
  };

  const sendDeleteVerificationFingerprint = async () => {
    setDeleteAccountError('');
    setDeleteAccountInfo('');
    setDeleteAccountSending(true);
    try {
      const res = await api.post('/account/delete/request-verification', { method: 'email' });
      setDeleteAccountInfo(res.data?.message || 'A verification fingerprint has been sent to your email address.');
    } catch (err) {
      setDeleteAccountError(err.response?.data?.detail || 'Failed to send verification fingerprint');
    } finally {
      setDeleteAccountSending(false);
    }
  };

  const handleDeleteAccount = async () => {
    setDeleteAccountError('');
    setDeleteAccountInfo('');
    if (deleteAccountConfirmText.trim().toUpperCase() !== 'DELETE') {
      setDeleteAccountError('Type DELETE to confirm account removal.');
      return;
    }

    const payload = { method: deleteAccountMethod };
    if (deleteAccountMethod === 'password') {
      if (!deleteAccountPassword) {
        setDeleteAccountError('Current password is required.');
        return;
      }
      payload.current_password = deleteAccountPassword;
    } else {
      if (!deleteAccountCode.trim()) {
        setDeleteAccountError('Verification fingerprint is required.');
        return;
      }
      payload.verification_code = deleteAccountCode.trim();
    }

    setDeleteAccountSubmitting(true);
    try {
      await api.post('/account/delete/confirm', payload);
      await logout();
      window.location.href = '/signin';
    } catch (err) {
      const status = err.response?.status;
      const detail = err.response?.data?.detail || '';
      if (status === 401 || status === 404) {
        await logout();
        window.location.href = '/signin';
        return;
      }
      setDeleteAccountError(detail || 'Failed to delete account');
    } finally {
      setDeleteAccountSubmitting(false);
    }
  };

  const openEditUser = (u) => { setEditUserModal(u); setEditUserForm({ name: u.name, email: u.email, role: u.role, sub_role: u.sub_role || '', status: u.status }); };

  const saveUserEdit = async () => {
    try {
      await api.put(`/users/${editUserModal.id}`, editUserForm);
      setEditUserModal(null);
      loadSettings();
      showToast({
        type: 'success',
        title: 'User Updated',
        message: `${editUserForm.name || 'The user'} was updated.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: getErrorMessage(err, 'We could not update that user.'),
      });
    }
  };

  const deleteUser = async (u) => {
    requestConfirmation({
      title: 'Delete User',
      description: `Delete ${u.name || 'this user'} permanently. This action cannot be undone.`,
      confirmLabel: 'Delete user',
      onConfirm: async () => {
        try {
          await api.delete(`/users/${u.id}`);
          loadSettings();
          showToast({
            type: 'success',
            title: 'User Deleted',
            message: `${u.name || 'The user'} was removed.`,
          });
        } catch (err) {
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that user.'),
          });
        }
      },
    });
  };

  const createUser = async () => {
    if (!createUserForm.name.trim() || !createUserForm.email.trim()) {
      showToast({
        type: 'error',
        title: 'Details Missing',
        message: 'Name and email are required before sending an invitation.',
      });
      return;
    }
    try {
      await api.post('/auth/onboarding/invite', createUserForm);
      setShowCreateUserModal(false);
      const invitedEmail = createUserForm.email.trim();
      setCreateUserForm({ name: '', email: '', role: 'company_agent', sub_role: '', status: 'active' });
      if (onboardingInviteMode) {
        showToast({
          type: 'success',
          title: 'Invite Sent',
          message: `Invitation sent to ${invitedEmail}.`,
        });
        navigate('/billing', { replace: true });
        return;
      }
      showToast({
        type: 'success',
        title: 'Invite Sent',
        message: `Invitation sent to ${invitedEmail}.`,
      });
      loadSettings();
    } catch (err) {
      const message = getErrorMessage(err, 'We could not send that invitation.');
      const lowerMessage = message.toLowerCase();
      showToast({
        type: 'error',
        title: lowerMessage.includes('team member limit') || lowerMessage.includes('limit has been reached') ? 'Team Limit Reached' : 'Invite Failed',
        message,
      });
    }
  };

  const revokeSession = async (sessionToken) => {
    try {
      await api.delete(`/security/sessions/${sessionToken}`);
      loadSecurity();
      showToast({
        type: 'success',
        title: 'Session Revoked',
        message: 'The selected session was signed out.',
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Revoke Failed',
        message: getErrorMessage(err, 'We could not revoke that session.'),
      });
    }
  };

  const createApiKey = async () => {
    const trimmedName = apiKeyName.trim();
    if (!trimmedName) {
      showToast({
        type: 'error',
        title: 'Name Required',
        message: 'Enter an API key name before generating it.',
      });
      return;
    }
    try {
      const res = await api.post('/security/api-keys', { name: trimmedName });
      setNewApiKey(res.data.key);
      setShowApiKeyForm(false); setApiKeyName('');
      loadSecurity();
      showToast({
        type: 'success',
        title: 'Key Created',
        message: `${trimmedName} is ready. Store the key securely now.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Create Failed',
        message: getErrorMessage(err, 'We could not generate that API key.'),
      });
    }
  };

  const revokeApiKey = async (keyId) => {
    const key = apiKeys.find((item) => item.id === keyId);
    requestConfirmation({
      title: 'Revoke API Key',
      description: `Revoke ${key?.name || 'this API key'}. Existing integrations using it will stop working immediately.`,
      confirmLabel: 'Revoke key',
      onConfirm: async () => {
        try {
          await api.delete(`/security/api-keys/${keyId}`);
          loadSecurity();
          showToast({
            type: 'success',
            title: 'Key Revoked',
            message: `${key?.name || 'The API key'} was revoked.`,
          });
        } catch (err) {
          console.error(err);
          showToast({
            type: 'error',
            title: 'Revoke Failed',
            message: getErrorMessage(err, 'We could not revoke that API key.'),
          });
        }
      },
    });
  };

  const copyToClipboard = (text, label = 'Value') => {
    navigator.clipboard.writeText(text);
    showToast({
      type: 'success',
      title: 'Copied',
      message: `${label} was copied to your clipboard.`,
    });
  };

  const readFileAsDataUrl = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });

  const handleProductFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const remainingSlots = MAX_PRODUCT_IMAGES - (productForm.images?.length || 0);
    if (remainingSlots <= 0) {
      setProductImageError(`Maximum ${MAX_PRODUCT_IMAGES} images allowed`);
      return;
    }

    const accepted = files.slice(0, remainingSlots);
    const nextImages = [];

    for (const file of accepted) {
      if (!PRODUCT_IMAGE_TYPES.includes(file.type)) {
        setProductImageError('Only JPG, PNG, and WEBP images are allowed');
        continue;
      }
      if (file.size > MAX_PRODUCT_IMAGE_SIZE_MB * 1024 * 1024) {
        setProductImageError(`Each image must be <= ${MAX_PRODUCT_IMAGE_SIZE_MB}MB`);
        continue;
      }
      try {
        const dataUrl = await readFileAsDataUrl(file);
        nextImages.push({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
          name: file.name,
          type: file.type,
          size: file.size,
          dataUrl,
        });
      } catch {
        setProductImageError('Failed to read one of the selected files');
      }
    }

    if (nextImages.length) {
      setProductImageError('');
      setProductForm((prev) => ({ ...prev, images: [...(prev.images || []), ...nextImages].slice(0, MAX_PRODUCT_IMAGES) }));
    }
  };

  const removeProductImage = (imageId) => {
    setProductForm((prev) => ({ ...prev, images: (prev.images || []).filter((img) => img.id !== imageId) }));
  };

  const openLightbox = useCallback((images, index) => {
    setLightboxImages(images);
    setLightboxIndex(index);
  }, []);

  const closeLightbox = useCallback(() => setLightboxImages([]), []);

  useEffect(() => {
    if (!lightboxImages.length) return;
    const handler = (e) => {
      if (e.key === 'ArrowRight') setLightboxIndex(i => (i + 1) % lightboxImages.length);
      if (e.key === 'ArrowLeft') setLightboxIndex(i => (i - 1 + lightboxImages.length) % lightboxImages.length);
      if (e.key === 'Escape') closeLightbox();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [lightboxImages, closeLightbox]);

  const generateProductDescription = async () => {
    if (!productForm.name) {
      setGenDescNudge(true);
      setTimeout(() => setGenDescNudge(false), 2800);
      return;
    }
    if (generatingDesc) return;
    setGeneratingDesc(true);
    try {
      const res = await api.post('/company-data/products/generate-description', {
        name: productForm.name,
        product_title: productForm.product_title || '',
        product_type: productForm.product_type || '',
        category: productForm.category || 'general',
        price: productForm.price || '',
        price_currency: productForm.price_currency || 'USD',
        images: (productForm.images || []).map(img => img.dataUrl || img).filter(Boolean),
      });
      if (res.data?.description) {
        setProductForm(prev => ({ ...prev, description: res.data.description }));
        showToast({
          type: 'success',
          title: 'Description Ready',
          message: `AI drafted a description for ${productForm.name}.`,
        });
      }
    } catch (err) {
      console.error('Failed to generate description:', err);
      showToast({
        type: 'error',
        title: 'Generation Failed',
        message: getErrorMessage(err, 'We could not generate a product description.'),
      });
    } finally {
      setGeneratingDesc(false);
    }
  };

  const resetProductForm = () => {
    setProductForm({ name: '', product_title: '', description: '', price: '', price_currency: 'USD', category: 'general', product_type: '', images: [] });
    setProductImageError('');
    setProductDragActive(false);
    setEditingProduct(null);
    setShowProductForm(false);
  };

  const openEditProduct = (product) => {
    setEditingProduct(product);
    setProductImageError('');
    setProductDragActive(false);
    setProductForm({
      name: product.name || '',
      product_title: product.product_title || '',
      description: product.description || '',
      price: product.price || '',
      price_currency: product.price_currency || 'USD',
      category: product.category || 'general',
      product_type: product.product_type || '',
      images: Array.isArray(product.images)
        ? product.images.slice(0, MAX_PRODUCT_IMAGES).map((img, idx) => ({
          id: `${product.id}-existing-${idx}`,
          name: `image-${idx + 1}`,
          type: 'image',
          size: 0,
          dataUrl: img,
        }))
        : [],
    });
    setShowProductForm(true);
  };

  const saveProduct = async () => {
    try {
      const payload = {
        ...productForm,
        product_type: productForm.product_type || 'standard',
        images: (productForm.images || []).map((img) => img.dataUrl).slice(0, MAX_PRODUCT_IMAGES),
      };
      if (editingProduct?.id) {
        await api.put(`/company-data/products/${editingProduct.id}`, payload);
      } else {
        await api.post('/company-data/products', payload);
      }
      resetProductForm();
      const r = await api.get('/company-data/products');
      setProducts(r.data);
      showToast({
        type: 'success',
        title: editingProduct?.id ? 'Product Updated' : 'Product Added',
        message: `${payload.name || 'The product'} was saved.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save that product.'),
      });
    }
  };
  const deleteProduct = async (id) => {
    const product = products.find((item) => item.id === id) || selectedProduct;
    try {
      await api.delete(`/company-data/products/${id}`);
      setProducts(prev => prev.filter(p => p.id !== id));
      setSelectedProduct(prev => prev?.id === id ? null : prev);
      showToast({
        type: 'success',
        title: 'Product Deleted',
        message: `${product?.name || 'The product'} was removed.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Delete Failed',
        message: getErrorMessage(err, 'We could not delete that product.'),
      });
    }
  };
  const createFaq = async () => {
    try {
      await api.post('/company-data/faqs', faqForm);
      setShowFaqForm(false);
      setFaqForm({ question: '', answer: '', category: 'general' });
      const r = await api.get('/company-data/faqs');
      setFaqs(r.data);
      showToast({
        type: 'success',
        title: 'FAQ Saved',
        message: 'The FAQ entry was added.',
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save that FAQ entry.'),
      });
    }
  };
  const deleteFaq = async (id) => {
    try {
      await api.delete(`/company-data/faqs/${id}`);
      setFaqs(prev => prev.filter(f => f.id !== id));
      showToast({
        type: 'success',
        title: 'FAQ Deleted',
        message: 'The FAQ entry was removed.',
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Delete Failed',
        message: getErrorMessage(err, 'We could not delete that FAQ entry.'),
      });
    }
  };

  const passwordValidation = validatePassword(newPassword);

  const fetchWaBridgeQr = useCallback(async (opts = {}) => {
    const silent = opts.silent === true;
    if (!silent) {
      setWaQrLoading(true);
    }
    try {
      const res = await api.get('/settings/channels/whatsapp/bridge-qr');
      const d = res.data || {};
      const st = normalizeWhatsAppBridgeState(d.state || d.bridge_status || d.status || '');
      const progress = getWhatsAppBridgeProgress(st, d.progress);
      const retrying = Boolean(d.retrying);
      const statusMessage = getWhatsAppBridgeMessage(st, {
        message: d.message,
        retrying,
      });
      setWaQrStatus(st);
      setWaQrProgress(progress);
      setWaQrRetrying(retrying);
      setWaQrMessage(statusMessage);
      setWaQrUpdatedAt(String(d.updated_at || ''));
      if (d.detail && !d.qr_data_url && !d.qr_png_base64 && st !== 'ready') {
        setWaQrError(String(d.detail));
      } else if (d.last_error && st === 'failed' && !retrying) {
        setWaQrError(String(d.last_error));
      } else {
        setWaQrError('');
      }
      if (!silent) {
        if (d.qr_data_url) {
          setWaQrImageSrc(d.qr_data_url);
        } else if (d.qr_png_base64) {
          setWaQrImageSrc(`data:image/png;base64,${d.qr_png_base64}`);
        } else if (st === 'ready' || st === 'authenticated' || st === 'initializing' || st === 'qr_scanned' || st === 'reconnecting') {
          setWaQrImageSrc('');
        } else {
          setWaQrImageSrc('');
        }
        setWaQrLoading(false);
      }
      if (st === 'ready' && waQrPollRef.current) {
        clearInterval(waQrPollRef.current);
        waQrPollRef.current = null;
      }
      return d;
    } catch (err) {
      const raw = err.response?.data?.detail;
      const msg = typeof raw === 'string' ? raw : (Array.isArray(raw) ? raw.map((x) => x.msg || x).join(' ') : err.message) || 'Bridge unavailable';
      if (!silent) {
        setWaQrError(String(msg));
        setWaQrImageSrc('');
        setWaQrProgress(0);
        setWaQrMessage('Failed to connect, please scan again');
        setWaQrLoading(false);
      }
      return null;
    }
  }, []);

  const collapseWaWebPanel = useCallback(() => {
    setWaQrPanelOpen(false);
    if (waQrPollRef.current) {
      clearInterval(waQrPollRef.current);
      waQrPollRef.current = null;
    }
  }, []);

  const disconnectWaWeb = useCallback(async () => {
    if (waDisconnecting) return;
    setWaDisconnecting(true);
    setWaQrError('');
    try {
      await api.post('/settings/channels/whatsapp/bridge-disconnect', {});
      setWaQrStatus('disconnected');
      setWaQrProgress(0);
      setWaQrMessage('Disconnected');
      setWaQrRetrying(false);
      setWaQrUpdatedAt('');
      setWaQrImageSrc('');
      setWaQrPanelOpen(false);
      if (waQrPollRef.current) {
        clearInterval(waQrPollRef.current);
        waQrPollRef.current = null;
      }
      setTimeout(() => { void fetchWaBridgeQr({ silent: true }); }, 1200);
    } catch (err) {
      const raw = err.response?.data?.detail || err.response?.data?.error;
      setWaQrError(typeof raw === 'string' ? raw : 'Failed to disconnect WhatsApp Web.');
    } finally {
      setWaDisconnecting(false);
    }
  }, [fetchWaBridgeQr, waDisconnecting]);

  const expandWaWebPanel = () => {
    if (waMetaBlocksQr) return;
    setWaQrPanelOpen(true);
    setWaQrLoading(true);
    setWaQrError('');
    setWaQrImageSrc('');
    setWaQrStatus('');
    setWaQrProgress(0);
    setWaQrMessage('Starting WhatsApp session');
    setWaQrRetrying(false);
    setWaQrUpdatedAt('');
    fetchWaBridgeQr();
    if (waQrPollRef.current) clearInterval(waQrPollRef.current);
    waQrPollRef.current = setInterval(() => {
      fetchWaBridgeQr();
    }, 1500);
  };

  useEffect(() => {
    if (activeTab === 'channels') {
      void fetchWaBridgeQr({ silent: true });
      waStatusPollRef.current = setInterval(() => {
        void fetchWaBridgeQr({ silent: true });
      }, 3500);
      return () => {
        if (waStatusPollRef.current) {
          clearInterval(waStatusPollRef.current);
          waStatusPollRef.current = null;
        }
      };
    }
    return undefined;
  }, [activeTab, fetchWaBridgeQr]);

  useEffect(() => {
    if (activeTab !== 'channels' || !waMetaBlocksQr) return;
    collapseWaWebPanel();
  }, [activeTab, waMetaBlocksQr, collapseWaWebPanel]);

  useEffect(() => () => {
    if (waQrPollRef.current) clearInterval(waQrPollRef.current);
    if (waStatusPollRef.current) clearInterval(waStatusPollRef.current);
  }, []);

  const tabs = [
    { id: 'personal',      label: 'Personal',       icon: Users },
    { id: 'company',       label: 'Company',        icon: Globe },
    { id: 'channels',      label: 'Channels',       icon: MessageSquare },
    { id: 'ai',            label: 'AI Config',      icon: Bot },
    { id: 'templates',     label: 'Templates & FAQs', icon: MessageSquare },
    { id: 'users',         label: 'Teams',          icon: Users },
    { id: 'notifications', label: 'Notifications',  icon: Bell },
    { id: 'webhooks',      label: 'Webhooks',       icon: Link2 },
    { id: 'integrations',  label: 'Integrations',   icon: Package },
    { id: 'logs',          label: 'System Logs',    icon: Activity },
    { id: 'security',      label: 'Security',       icon: Shield },
  ];

  return (
    <>
    <div className="p-6 lg:p-8" data-testid="settings-page">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">{standaloneUnification ? 'Unification' : 'Settings'}</h1>

      <div className={`flex ${standaloneUnification ? '' : 'gap-8'} h-[calc(100vh-8rem)] overflow-hidden`}>
        {!standaloneUnification && (
          <div ref={settingsSidebarRef} className="h-full w-48 flex-shrink-0 space-y-0.5 overflow-y-auto pr-1">
            {tabs.map(tab => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`group w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-all duration-200 hover:translate-x-1 active:scale-95 ${
                  activeTab === tab.id
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'text-slate-500 hover:text-slate-900 hover:bg-slate-100 hover:shadow-sm'
                }`}>
                <tab.icon size={15} className={`transition-transform duration-200 group-hover:scale-110 ${activeTab === tab.id ? 'text-white' : 'text-slate-400'}`} />
                <span className="flex-1 text-left">{tab.label}</span>
                {activeTab === tab.id && <span className="w-1.5 h-1.5 rounded-full bg-white/70" />}
              </button>
            ))}
          </div>
        )}

        <div className={`${standaloneUnification ? 'w-full' : 'flex-1 min-w-0 overflow-y-auto'}`}>

          {/* === PERSONAL SETTINGS === */}
          {activeTab === 'personal' && (
            <div className="space-y-6">
              <h2 className="text-lg font-semibold text-slate-900">Personal Profile</h2>

              {!personalSettings && (
                <div className="bg-white border border-slate-100 rounded-xl p-6 text-sm text-slate-500">
                  Loading personal settings...
                </div>
              )}

              {personalSettings && (
                <>
                  {/* Profile preview banner */}
                  <div className="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-100 rounded-xl p-5 flex items-center gap-5">
                    <div className="relative flex-shrink-0">
                      <div className="w-16 h-16 rounded-xl bg-white border border-blue-200 shadow flex items-center justify-center text-blue-500 font-bold text-xl select-none overflow-hidden">
                        {personalAvatarUrl
                          ? <img src={personalAvatarUrl} alt="avatar" referrerPolicy="no-referrer" className="w-full h-full object-cover" onError={() => savePersonalAvatar('')} />
                          : displayNameInitial(personalSettings.name)
                        }
                      </div>
                      <LogoUploadTrigger onClick={() => personalAvatarRef.current?.click()} title="Change profile photo" />
                      <input ref={personalAvatarRef} type="file" accept="image/*" className="hidden" onChange={handlePersonalAvatarChange} />
                    </div>
                    <div>
                      <p className="text-base font-bold text-slate-900">{personalSettings.name || 'Your Name'}</p>
                      {personalSettings.email && <p className="text-xs text-slate-500 mt-0.5">{personalSettings.email}</p>}
                      {personalSettings.role && <span className="mt-1 inline-block text-[10px] font-medium bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full capitalize">{personalSettings.role.charAt(0).toUpperCase() + personalSettings.role.slice(1)}{company?.company_name ? ` at ${company.company_name}` : ''}</span>}
                      <p className="text-[10px] text-slate-400 mt-1">Click the + to change your photo. Save to apply.</p>
                    </div>
                  </div>

                  {/* -- Account Information -- */}
                  <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                    <div className="flex items-center gap-2"><UserCircle size={13} className="text-slate-400" /><span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Account Information</span></div>
                    <div className="grid grid-cols-2 gap-4">
                      <div><label className="text-xs text-slate-400 mb-1 block">Full Name</label><input value={personalSettings.name || ''} onChange={(e) => setPersonalSettings({ ...personalSettings, name: e.target.value })} placeholder="Jane Smith" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                      <div><label className="text-xs text-slate-400 mb-1 block">Email Address</label><input value={personalSettings.email || ''} onChange={(e) => setPersonalSettings({ ...personalSettings, email: e.target.value })} placeholder="jane@company.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                      <div><label className="text-xs text-slate-400 mb-1 block">Mobile Number</label><input value={personalSettings.mobile_number || ''} onChange={(e) => setPersonalSettings({ ...personalSettings, mobile_number: e.target.value })} placeholder="+1 555 000 0000" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                      <div><label className="text-xs text-slate-400 mb-1 block">Role</label><input value={personalSettings.role ? (personalSettings.role.charAt(0).toUpperCase() + personalSettings.role.slice(1) + (company?.company_name ? ` at ${company.company_name}` : '')) : ''} disabled className="w-full px-3 py-2 bg-slate-100 border border-slate-200 rounded-lg text-sm text-slate-400" /></div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <button onClick={savePersonalProfile} disabled={saving === 'personal'} className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50 transition-colors"><Save size={14} /> {saving === 'personal' ? 'Saving...' : 'Save Personal Profile'}</button>
                    {saving === 'personal_done' && <span className="flex items-center gap-1 text-emerald-600 text-xs"><Check size={13} /> Saved</span>}
                  </div>
                </>
              )}
            </div>
          )}

          {/* Channels (includes email mailbox) */}
          {activeTab === 'channels' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 mb-1">Channels</h2>
                <p className="text-sm text-slate-400">Configure messaging channels, email (IMAP / SMTP or Brevo), and social accounts</p>
              </div>
              {channels.map((channel) => {
                const config = CHANNEL_CONFIG[channel.channel] || { label: channel.channel, color: 'gray', fields: [] };
                const showKeys = showKeyMap[channel.channel] || false;
                const emailProv = (channel.email_provider || 'smtp_imap').toLowerCase();
                const recvOn = channel.email_receive_enabled !== false;
                const sendOn = channel.email_send_enabled !== false;
                return (
                  <div key={channel.channel} className="bg-white border border-slate-100 rounded-xl p-6">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-lg ${config.bgClass} flex items-center justify-center`}>{channel.channel === 'email' ? <Mail size={18} className={config.textClass} /> : <MessageSquare size={18} className={config.textClass} />}</div>
                        <div><h3 className="text-sm font-semibold text-slate-900">{config.label}</h3><p className="text-xs text-slate-400">{channel.enabled ? 'Active' : 'Inactive'}</p></div>
                      </div>
                      <button onClick={() => updateChannelField(channel.channel, 'enabled', !channel.enabled)} className={`relative w-11 h-6 rounded-full transition-colors ${channel.enabled ? 'bg-blue-600' : 'bg-gray-300'}`}>
                        <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow ${channel.enabled ? 'translate-x-5' : ''}`}></span>
                      </button>
                    </div>
                    {channel.channel !== 'web_chat' && (
                      <div className="space-y-3">
                        {channel.channel === 'whatsapp' && (
                          <div className="w-full space-y-3" data-testid="whatsapp-channel-settings">
                            <div id="settings-whatsapp-meta" className="space-y-3 w-full" data-testid="whatsapp-meta-api">
                              <p className="text-[11px] font-medium text-slate-500">Meta API</p>
                              <p className="text-xs text-slate-500">
                                Configure in Meta Business Manager → WhatsApp → API Setup, then save (same style as other Meta channels).
                              </p>
                              {waQrBlocksMeta && (
                                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/90 px-3 py-2 text-xs text-amber-950">
                                  <Lock size={14} className="text-amber-700 flex-shrink-0 mt-0.5" aria-hidden />
                                  <span>
                                    WhatsApp Web is connected via QR. Unlink the device in WhatsApp (Linked devices) to edit these fields.
                                  </span>
                                </div>
                              )}
                              <div>
                                <label className="text-xs text-slate-400 mb-1 block">Phone number ID</label>
                                <input
                                  value={channel.phone_number_id || ''}
                                  onChange={(e) => updateChannelField(channel.channel, 'phone_number_id', e.target.value)}
                                  placeholder="Enter WhatsApp Phone number ID"
                                  disabled={waQrBlocksMeta}
                                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm disabled:bg-slate-100 disabled:text-slate-500"
                                />
                              </div>
                              <div>
                                <label className="text-xs text-slate-400 mb-1 block">Access Token</label>
                                <div className="relative">
                                  <input
                                    type={showKeys ? 'text' : 'password'}
                                    value={channel.access_token || ''}
                                    onChange={(e) => updateChannelField(channel.channel, 'access_token', e.target.value)}
                                    placeholder="Enter Access Token"
                                    disabled={waQrBlocksMeta}
                                    className="w-full px-3 py-2 pr-10 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono disabled:bg-slate-100 disabled:text-slate-500"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => setShowKeyMap({ ...showKeyMap, [channel.channel]: !showKeys })}
                                    disabled={waQrBlocksMeta}
                                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 disabled:opacity-40"
                                  >
                                    {showKeys ? <EyeOff size={14} /> : <Eye size={14} />}
                                  </button>
                                </div>
                              </div>
                              <div>
                                <label className="text-xs text-slate-400 mb-1 block">Webhook URL</label>
                                <div className="flex items-stretch gap-2">
                                  <input
                                    value={channel.webhook_url || ''}
                                    onChange={(e) => updateChannelField(channel.channel, 'webhook_url', e.target.value)}
                                    placeholder={webhookInfo?.webhook_urls?.whatsapp || 'https://your-domain.com/api/webhooks/whatsapp'}
                                    disabled={waQrBlocksMeta}
                                    className="flex-1 min-w-0 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono disabled:bg-slate-100 disabled:text-slate-500"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => {
                                      const t = (channel.webhook_url || webhookInfo?.webhook_urls?.whatsapp || '').trim();
                                      if (!t) return;
                                      void navigator.clipboard?.writeText(t).catch(() => {});
                                    }}
                                    disabled={waQrBlocksMeta || (!(channel.webhook_url || '').trim() && !((webhookInfo?.webhook_urls?.whatsapp || '').trim()))}
                                    className="flex-shrink-0 px-3 py-2 text-slate-600 bg-slate-50 border border-slate-200 rounded-lg hover:bg-slate-100 disabled:opacity-40 disabled:pointer-events-none"
                                    title="Copy webhook URL"
                                  >
                                    <Copy size={14} className="mx-auto" />
                                  </button>
                                </div>
                              </div>
                              <div>
                                <label className="text-xs text-slate-400 mb-1 block">Verify Token</label>
                                <input
                                  value={channel.verify_token || ''}
                                  onChange={(e) => updateChannelField(channel.channel, 'verify_token', e.target.value)}
                                  placeholder="Enter Verify Token"
                                  disabled={waQrBlocksMeta}
                                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono disabled:bg-slate-100 disabled:text-slate-500"
                                />
                              </div>
                            </div>
                            <div className={`pt-3 border-t border-slate-100 space-y-3 w-full min-w-0 ${waMetaBlocksQr ? 'opacity-90' : ''}`}>
                              <p className="text-[11px] font-medium text-slate-500">WhatsApp Web (QR)</p>
                              <p className="text-xs text-slate-500">
                                Not both: clear Phone number ID and Access token and save to use QR, or unlink the device in WhatsApp to return to Meta API fields.
                              </p>
                              {waMetaBlocksQr && (
                                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/90 px-3 py-2 text-xs text-amber-950">
                                  <Lock size={14} className="text-amber-700 flex-shrink-0 mt-0.5" aria-hidden />
                                  <span>Meta API fields are set. Remove Phone number ID and Access token and save, then you can use QR here.</span>
                                </div>
                              )}
                              {waQrStatus === 'ready' ? (
                                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2">
                                  <span className="inline-flex items-center gap-2 text-sm font-medium text-emerald-800">
                                    <CheckCircle size={16} className="text-emerald-600" /> Connected
                                  </span>
                                  <button
                                    type="button"
                                    onClick={disconnectWaWeb}
                                    disabled={waDisconnecting}
                                    className="ml-auto px-3 py-1.5 text-xs font-medium text-red-600 bg-white border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50"
                                  >
                                    {waDisconnecting ? 'Disconnecting...' : 'Disconnect'}
                                  </button>
                                </div>
                              ) : !waQrPanelOpen ? (
                                <button
                                  type="button"
                                  onClick={expandWaWebPanel}
                                  disabled={waMetaBlocksQr}
                                  className="inline-flex items-center gap-2 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                  <QrCode size={16} /> Connect with WhatsApp Web (QR)
                                </button>
                              ) : (
                                <div className="space-y-3">
                                  {waQrError && (
                                    <div className="flex items-start gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-900">{waQrError}</div>
                                  )}
                                  {waQrStatus && waQrStatus !== 'ready' && (
                                    <div className="space-y-2 rounded-lg border border-slate-200 bg-white px-3 py-3" data-testid="whatsapp-bridge-progress">
                                      <div className="flex items-center justify-between gap-3">
                                        <div className="min-w-0">
                                          <p className="text-xs font-semibold text-slate-800">{waQrMessage}</p>
                                          <p className="text-[11px] text-slate-500">
                                            Status: <span className="font-medium text-emerald-800">{waQrStatus}</span>
                                            {waQrRetrying ? <span className="ml-1 text-amber-700">retrying</span> : null}
                                          </p>
                                        </div>
                                        <span className="text-xs font-semibold tabular-nums text-slate-600">{waQrProgress}%</span>
                                      </div>
                                      <div
                                        className="h-2 w-full overflow-hidden rounded-full bg-slate-100"
                                        role="progressbar"
                                        aria-label="WhatsApp connection progress"
                                        aria-valuemin={0}
                                        aria-valuemax={100}
                                        aria-valuenow={waQrProgress}
                                      >
                                        <div
                                          className={`h-full rounded-full transition-all duration-700 ${waQrRetrying || waQrNormalizedStatus === 'reconnecting' ? 'bg-amber-500' : 'bg-emerald-500'}`}
                                          style={{ width: `${Math.max(0, Math.min(100, waQrProgress))}%` }}
                                        />
                                      </div>
                                      {waQrUpdatedAt && (
                                        <p className="text-[10px] text-slate-400">Updated {waQrUpdatedAt.replace('T', ' ').slice(0, 19)}</p>
                                      )}
                                    </div>
                                  )}
                                  <div className="flex min-h-[200px] items-center justify-center rounded-lg border border-slate-200 bg-slate-50/80 p-4">
                                    {waQrImageSrc ? (
                                      <img src={waQrImageSrc} alt="WhatsApp QR" className="max-w-[260px] max-h-[260px] w-full h-auto" />
                                    ) : waQrShowProgress || waQrIsConnecting ? (
                                      <div className="w-full max-w-sm text-center" aria-live="polite">
                                        <div className="mx-auto mb-3 h-9 w-9 animate-spin rounded-full border-2 border-emerald-200 border-t-emerald-600" />
                                        <p className="text-sm font-medium text-slate-700">{waQrMessage}</p>
                                        <p className="mt-1 text-xs text-slate-500">Keep this page open while WhatsApp finishes linking.</p>
                                      </div>
                                    ) : (
                                      <div className="text-center text-sm text-slate-500 px-4">
                                        {waQrLoading && !waQrError
                                          ? 'Starting session... If this is the first run, the QR can take a few seconds.'
                                          : 'No QR yet. Ensure the WhatsApp bridge service is running (included in Docker Compose).'}
                                      </div>
                                    )}
                                  </div>
                                  <div className="flex flex-wrap gap-2">
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setWaQrLoading(true);
                                        fetchWaBridgeQr();
                                      }}
                                      disabled={waMetaBlocksQr}
                                      className="px-3 py-2 text-xs font-medium text-white bg-emerald-600 border border-emerald-600 rounded-lg hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                      Connect / Refresh QR
                                    </button>
                                    <button
                                      type="button"
                                      onClick={collapseWaWebPanel}
                                      className="px-3 py-2 text-xs font-medium text-slate-600 bg-slate-100 rounded-lg hover:bg-slate-200"
                                    >
                                      Hide
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                        {(channel.channel === 'instagram' || channel.channel === 'facebook') && (
                          <>
                            <div>
                              <label className="text-xs text-slate-400 mb-1 block">Page ID</label>
                              <input value={channel.page_id || ''} onChange={(e) => updateChannelField(channel.channel, 'page_id', e.target.value)} placeholder={`Enter ${config.label} Page ID`} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" />
                            </div>
                            <div>
                              <label className="text-xs text-slate-400 mb-1 block">Access Token</label>
                              <div className="relative">
                                <input type={showKeys ? 'text' : 'password'} value={channel.access_token || ''} onChange={(e) => updateChannelField(channel.channel, 'access_token', e.target.value)} placeholder="Enter Access Token" className="w-full px-3 py-2 pr-10 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" />
                                <button type="button" onClick={() => setShowKeyMap({...showKeyMap, [channel.channel]: !showKeys})} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400">{showKeys ? <EyeOff size={14} /> : <Eye size={14} />}</button>
                              </div>
                            </div>
                            <div>
                              <label className="text-xs text-slate-400 mb-1 block">Verify Token</label>
                              <input value={channel.verify_token || ''} onChange={(e) => updateChannelField(channel.channel, 'verify_token', e.target.value)} placeholder="Enter Verify Token" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" />
                            </div>
                          </>
                        )}
                        {channel.channel === 'email' && (
                          <>
                            <div><label className="text-xs text-slate-400 mb-1 block">Email address</label><input type="email" value={channel.email_address || ''} onChange={(e) => updateChannelField(channel.channel, 'email_address', e.target.value)} placeholder="you@yourdomain.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                            <div><label className="text-xs text-slate-400 mb-1 block">Connection</label><select value={emailProv === 'brevo' ? 'brevo' : emailProv === 'oauth_google' ? 'oauth_google' : 'smtp_imap'} onChange={(e) => updateChannelField(channel.channel, 'email_provider', e.target.value)} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm"><option value="smtp_imap">IMAP + SMTP (personal / workspace mailbox)</option><option value="brevo">Brevo (API key)</option><option value="oauth_google">Google (OAuth) — link via admin when available</option></select></div>
                            {emailProv === 'oauth_google' && (
                              <p className="text-xs text-slate-500">OAuth-based sign-in for Gmail is planned for a dedicated connect flow. Use IMAP + SMTP with an app password, or Brevo, for production today.</p>
                            )}
                            {emailProv === 'brevo' && (
                              <div><label className="text-xs text-slate-400 mb-1 block">Brevo API key</label><div className="relative"><input type={showKeys ? 'text' : 'password'} value={channel.api_key || ''} onChange={(e) => updateChannelField(channel.channel, 'api_key', e.target.value)} placeholder="xkeysib-..." className="w-full px-3 py-2 pr-10 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" /><button type="button" onClick={() => setShowKeyMap({ ...showKeyMap, [channel.channel]: !showKeys })} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400">{showKeys ? <EyeOff size={14} /> : <Eye size={14} />}</button></div></div>
                            )}
                            {(emailProv === 'smtp_imap' || emailProv === 'smtp' || !emailProv) && (
                              <>
                                <p className="text-[11px] text-slate-500 font-medium uppercase tracking-wide">IMAP (receive)</p>
                                <div className="grid grid-cols-2 gap-3">
                                  <div className="col-span-2 sm:col-span-1"><label className="text-xs text-slate-400 mb-1 block">IMAP host</label><input value={channel.imap_host || ''} onChange={(e) => updateChannelField(channel.channel, 'imap_host', e.target.value)} placeholder="imap.gmail.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                                  <div><label className="text-xs text-slate-400 mb-1 block">IMAP port</label><input type="number" value={channel.imap_port ?? 993} onChange={(e) => updateChannelField(channel.channel, 'imap_port', parseInt(e.target.value, 10) || 993)} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                                  <div className="col-span-2"><label className="text-xs text-slate-400 mb-1 block">IMAP username</label><input value={channel.imap_user || ''} onChange={(e) => updateChannelField(channel.channel, 'imap_user', e.target.value)} placeholder="Usually your email address" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                                  <div className="col-span-2"><label className="text-xs text-slate-400 mb-1 block">IMAP password / app password</label><div className="relative"><input type={showKeyMap.email_imap ? 'text' : 'password'} value={channel.imap_pass_enc || ''} onChange={(e) => updateChannelField(channel.channel, 'imap_pass_enc', e.target.value)} placeholder="Stored for mailbox access" className="w-full px-3 py-2 pr-10 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" /><button type="button" onClick={() => setShowKeyMap({ ...showKeyMap, email_imap: !showKeyMap.email_imap })} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400">{showKeyMap.email_imap ? <EyeOff size={14} /> : <Eye size={14} />}</button></div></div>
                                </div>
                                <p className="text-[11px] text-slate-500 font-medium uppercase tracking-wide pt-2">SMTP (send)</p>
                                <div className="grid grid-cols-2 gap-3">
                                  <div className="col-span-2 sm:col-span-1"><label className="text-xs text-slate-400 mb-1 block">SMTP host</label><input value={channel.smtp_host || ''} onChange={(e) => updateChannelField(channel.channel, 'smtp_host', e.target.value)} placeholder="smtp.gmail.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                                  <div><label className="text-xs text-slate-400 mb-1 block">SMTP port</label><input type="number" value={channel.smtp_port ?? 587} onChange={(e) => updateChannelField(channel.channel, 'smtp_port', parseInt(e.target.value, 10) || 587)} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                                  <div className="col-span-2"><label className="text-xs text-slate-400 mb-1 block">SMTP username</label><input value={channel.smtp_user || ''} onChange={(e) => updateChannelField(channel.channel, 'smtp_user', e.target.value)} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                                  <div className="col-span-2"><label className="text-xs text-slate-400 mb-1 block">SMTP password</label><div className="relative"><input type={showKeyMap.email_smtp ? 'text' : 'password'} value={channel.smtp_pass_enc || ''} onChange={(e) => updateChannelField(channel.channel, 'smtp_pass_enc', e.target.value)} className="w-full px-3 py-2 pr-10 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" /><button type="button" onClick={() => setShowKeyMap({ ...showKeyMap, email_smtp: !showKeyMap.email_smtp })} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400">{showKeyMap.email_smtp ? <EyeOff size={14} /> : <Eye size={14} />}</button></div></div>
                                </div>
                              </>
                            )}
                            <div className="flex flex-wrap items-center gap-6 pt-2 border-t border-slate-100">
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-600">Receive into inbox</span>
                                <button type="button" onClick={() => updateChannelField(channel.channel, 'email_receive_enabled', !recvOn)} className={`relative w-11 h-6 rounded-full transition-colors ${recvOn ? 'bg-sky-600' : 'bg-gray-300'}`}><span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow ${recvOn ? 'translate-x-5' : ''}`} /></button>
                              </div>
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-600">Send messages</span>
                                <button type="button" onClick={() => updateChannelField(channel.channel, 'email_send_enabled', !sendOn)} className={`relative w-11 h-6 rounded-full transition-colors ${sendOn ? 'bg-sky-600' : 'bg-gray-300'}`}><span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow ${sendOn ? 'translate-x-5' : ''}`} /></button>
                              </div>
                            </div>
                            <div><label className="text-xs text-slate-400 mb-1 block">Inbound webhook URL (optional)</label><input value={channel.webhook_url || ''} onChange={(e) => updateChannelField(channel.channel, 'webhook_url', e.target.value)} placeholder="Provider inbound parse URL → your /api/webhooks/..." className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" /></div>
                          </>
                        )}
                        {channel.channel !== 'email' && channel.channel !== 'whatsapp' && (
                          <div><label className="text-xs text-slate-400 mb-1 block">Webhook URL</label><input value={channel.webhook_url || ''} onChange={(e) => updateChannelField(channel.channel, 'webhook_url', e.target.value)} placeholder="https://your-domain.com/api/webhooks/..." className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" /></div>
                        )}
                        <button type="button" onClick={() => saveChannel(channel)} disabled={saving === channel.channel} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50"><Save size={14} /> Save</button>
                      </div>
                    )}
                    {channel.channel === 'web_chat' && (<div className="bg-slate-50 rounded-lg p-4"><div className="flex items-center gap-2"><Check size={14} className="text-emerald-600" /><span className="text-xs text-emerald-600">Ready to use</span></div></div>)}
                  </div>
                );
              })}

              {/* Social Analytics */}
              {activeTab === 'channels' && (
              <div className="bg-white border border-slate-100 rounded-xl p-6">
                <div className="mb-5">
                  <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2"><MessageSquare size={14} className="text-pink-500" /> Social Analytics</h3>
                  <p className="text-[10px] text-slate-400 mt-1">Connect Meta business accounts to pull performance analytics. Access tokens are stored as references only.</p>
                </div>
                <div className="space-y-3">
                  {[
                    { platform: 'instagram', label: 'Instagram Business', iconBg: 'bg-pink-100', iconText: 'text-pink-700', btnCls: 'bg-pink-600 hover:bg-pink-500', abbr: 'IG',
                      fields: [
                        { key: 'account_handle', label: 'Account Handle', ph: '@yourbrand' },
                        { key: 'page_id', label: 'Business Account ID', ph: '123456789' },
                        { key: 'app_id', label: 'App ID', ph: '111222333' },
                        { key: 'access_token_ref', label: 'Page Access Token', ph: 'EAAU...', secret: true },
                      ]},
                    { platform: 'facebook', label: 'Facebook Page', iconBg: 'bg-blue-100', iconText: 'text-blue-700', btnCls: 'bg-blue-600 hover:bg-blue-500', abbr: 'FB',
                      fields: [
                        { key: 'account_handle', label: 'Page Name', ph: 'My Brand Page' },
                        { key: 'page_id', label: 'Page ID', ph: '123456789' },
                        { key: 'app_id', label: 'App ID', ph: '111222333' },
                        { key: 'access_token_ref', label: 'Page Access Token', ph: 'EAAU...', secret: true },
                      ]},
                  ].map(({ platform, label, iconBg, iconText, btnCls, abbr, fields }) => {
                    const acct = socialAccounts.find(a => a.platform === platform);
                    const isEditing = editingSocialPlatform === platform;
                    return (
                      <div key={platform} className="border border-slate-200 rounded-lg overflow-hidden">
                        <div className="flex items-center justify-between p-3 bg-slate-50">
                          <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-full ${iconBg} flex items-center justify-center ${iconText} text-xs font-bold flex-shrink-0`}>{abbr}</div>
                            <div>
                              <p className="text-sm font-medium text-slate-700">{label}</p>
                              <p className="text-[10px] text-slate-400">{acct ? `Connected — ${acct.account_handle || acct.page_id || ''}` : 'Not configured'}</p>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            {acct && <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${acct.is_active !== false ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-400'}`}>{acct.is_active !== false ? 'Active' : 'Inactive'}</span>}
                            {isAdmin && <button onClick={() => { if (isEditing) { setEditingSocialPlatform(null); } else { setEditingSocialPlatform(platform); setSocialDraft(acct ? { account_handle: acct.account_handle || '', page_id: acct.page_id || '', access_token_ref: '', app_id: acct.app_id || '', phone_number_id: acct.phone_number_id || '' } : { account_handle: '', page_id: '', access_token_ref: '', app_id: '', phone_number_id: '' }); } }} className="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 rounded-lg text-xs text-slate-600 font-medium">{isEditing ? 'Close' : acct ? 'Edit' : 'Configure'}</button>}
                            {isAdmin && acct && <button onClick={() => {
                              requestConfirmation({
                                title: 'Disconnect Social Account',
                                description: `Disconnect ${label}. Incoming and outgoing social messaging for this account will stop until it is reconnected.`,
                                confirmLabel: 'Disconnect account',
                                onConfirm: async () => {
                                  try {
                                    await api.delete(`/social/accounts/${acct.id}`);
                                    setSocialAccounts(prev => prev.filter(x => x.id !== acct.id));
                                    if (editingSocialPlatform === platform) setEditingSocialPlatform(null);
                                    showToast({
                                      type: 'success',
                                      title: 'Account Disconnected',
                                      message: `${label} was disconnected.`,
                                    });
                                  } catch (err) {
                                    showToast({
                                      type: 'error',
                                      title: 'Disconnect Failed',
                                      message: getErrorMessage(err, `We could not disconnect ${label}.`),
                                    });
                                  }
                                },
                              });
                            }} className="p-1.5 hover:bg-red-50 rounded text-slate-400 hover:text-red-500"><Trash2 size={13} /></button>}
                          </div>
                        </div>
                        {isEditing && (
                          <div className="p-4 bg-white border-t border-slate-100 space-y-3">
                            <div className="grid grid-cols-2 gap-3">
                              {fields.map(f => (
                                <div key={f.key}>
                                  <label className="text-[10px] text-slate-400 font-medium mb-1 block">{f.label}</label>
                                  <input type={f.secret ? 'password' : 'text'} value={socialDraft[f.key] || ''} onChange={ev => setSocialDraft(p => ({...p, [f.key]: ev.target.value}))} placeholder={f.ph} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" />
                                </div>
                              ))}
                            </div>
                            <div className="flex gap-2">
                              <button onClick={async () => {
                                const payload = { ...socialDraft, platform };
                                let r;
                                if (acct) {
                                  r = await api.put(`/social/accounts/${acct.id}`, payload).catch(() => null);
                                  if (r?.data) setSocialAccounts(prev => prev.map(x => x.id === acct.id ? r.data : x));
                                } else {
                                  r = await api.post('/social/accounts', payload).catch(() => null);
                                  if (r?.data) setSocialAccounts(prev => [...prev, r.data]);
                                }
                                if (r) setEditingSocialPlatform(null);
                              }} className={`px-4 py-2 text-white rounded-lg text-xs font-medium ${btnCls}`}>{acct ? 'Update' : 'Connect'}</button>
                              <button onClick={() => setEditingSocialPlatform(null)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs">Cancel</button>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
              )}
            </div>
          )}

          {/* === COMPANY === */}
          {activeTab === 'company' && company && (
            <div className="space-y-6">
              <h2 className="text-lg font-semibold text-slate-900">Company Profile</h2>

              {/* Profile preview card */}
              <div className="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-100 rounded-xl p-5 flex items-center gap-5">
                <div className="relative">
                  {company.logo_url
                    ? <img src={resolveMediaUrl(company.logo_url)} alt="logo" className="w-16 h-16 rounded-xl object-cover border border-white shadow" onError={() => setCompany((prev) => ({ ...(prev || {}), logo_url: '' }))} />
                    : <div className="w-16 h-16 rounded-xl bg-white border border-blue-200 shadow flex items-center justify-center"><Building2 size={28} className="text-blue-400" /></div>
                  }
                  <LogoUploadTrigger
                    onClick={() => companyLogoInputRef.current?.click()}
                    disabled={companyLogoUploading}
                    uploading={companyLogoUploading}
                    title="Upload company logo"
                  />
                  <input ref={companyLogoInputRef} type="file" accept="image/*" className="hidden" onChange={handleCompanyLogoChange} />
                </div>
                <div>
                  <p className="text-base font-bold text-slate-900">{company.company_name || 'Your Company'}</p>
                  {company.tagline && <p className="text-xs text-slate-500 italic mt-0.5">{company.tagline}</p>}
                  {company.industry && <span className="mt-1 inline-block text-[10px] font-medium bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">{company.industry}</span>}
                </div>
              </div>

              {/* -- Brand Identity -- */}
              <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                <div className="flex items-center gap-2 mb-1"><Briefcase size={13} className="text-slate-400" /><span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Brand Identity</span></div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Company Name <span className="text-red-400">*</span></label>
                  <input
                    value={company.company_name || ''}
                    onChange={(e) => updateCompanyField('company_name', e.target.value)}
                    placeholder="Acme Corp"
                    aria-invalid={Boolean(companyValidationErrors.company_name)}
                    className={`w-full px-3 py-2 bg-slate-50 border rounded-lg text-sm focus:outline-none focus:ring-2 ${companyValidationErrors.company_name ? 'border-red-300 focus:ring-red-100' : 'border-slate-200 focus:ring-blue-200'}`}
                  />
                  {companyValidationErrors.company_name && <p className="mt-1 text-xs text-red-600">{companyValidationErrors.company_name}</p>}
                </div>
                <div><label className="text-xs text-slate-400 mb-1 block">Tagline</label><input value={company.tagline || ''} onChange={(e) => setCompany({...company, tagline: e.target.value})} placeholder="A short catchy phrase that describes your brand" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                <div><label className="text-xs text-slate-400 mb-1 block">Industry <span className="text-red-400">*</span></label>
                  <select
                    value={company.industry || ''}
                    onChange={(e) => updateCompanyField('industry', e.target.value)}
                    aria-invalid={Boolean(companyValidationErrors.industry)}
                    className={`w-full px-3 py-2 bg-slate-50 border rounded-lg text-sm focus:outline-none focus:ring-2 ${companyValidationErrors.industry ? 'border-red-300 focus:ring-red-100' : 'border-slate-200 focus:ring-blue-200'}`}
                  >
                    <option value="">Select industry</option>
                    {['Technology', 'E-commerce', 'Finance & Banking', 'Healthcare', 'Education', 'Real Estate', 'Marketing & Advertising', 'Logistics', 'Hospitality', 'Retail', 'Manufacturing', 'Consulting', 'Other'].map(i => <option key={i} value={i}>{i}</option>)}
                  </select>
                  {companyValidationErrors.industry && <p className="mt-1 text-xs text-red-600">{companyValidationErrors.industry}</p>}
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Description</label>
                  <textarea
                    value={company.description || ''}
                    onChange={(e) => setCompany({...company, description: e.target.value})}
                    rows={5}
                    maxLength={5000}
                    placeholder="Brief description of your company - the AI will use this when introducing itself to customers."
                    className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200"
                  />
                  <div className="mt-1 text-right text-[11px] text-slate-400">
                    {(company.description || '').length} / 5000
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block flex items-center gap-1"><Image size={11} /> Logo URL</label>
                  <div className="flex gap-2">
                    <input value={company.logo_url || ''} onChange={(e) => setCompany({...company, logo_url: e.target.value})} placeholder="https://yourcompany.com/logo.png" className="min-w-0 flex-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" />
                    <button
                      type="button"
                      onClick={() => companyLogoInputRef.current?.click()}
                      disabled={companyLogoUploading}
                      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                    >
                      {companyLogoUploading ? <div className="h-3 w-3 rounded-full border-2 border-slate-400 border-t-transparent animate-spin" /> : <Plus size={13} />}
                      Upload
                    </button>
                  </div>
                </div>
              </div>

              {/* -- Contact Info -- */}
              <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                <div className="flex items-center gap-2 mb-1"><Phone size={13} className="text-slate-400" /><span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Contact Information</span></div>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">Default Phone Region</label>
                    <select
                      value={(company.default_phone_region || '').toUpperCase()}
                      onChange={(e) => setCompany({...company, default_phone_region: e.target.value})}
                      className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200"
                    >
                      <option value="">Use workspace default</option>
                      {PHONE_COUNTRIES.map((country) => (
                        <option key={country.code} value={country.code}>
                          {country.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div><label className="text-xs text-slate-400 mb-1 block">Phone Number</label><input value={company.phone || ''} onChange={(e) => setCompany({...company, phone: e.target.value})} placeholder="+1 555 000 0000" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                  <div><label className="text-xs text-slate-400 mb-1 block">Support Email</label><input type="email" value={company.support_email || ''} onChange={(e) => setCompany({...company, support_email: e.target.value})} placeholder="support@yourcompany.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                </div>
                <div><label className="text-xs text-slate-400 mb-1 block flex items-center gap-1"><Globe size={11} /> Website</label><input value={company.website_address || ''} onChange={(e) => setCompany({...company, website_address: e.target.value})} placeholder="https://yourcompany.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
              </div>

              {/* -- Address -- */}
              <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                <div className="flex items-center gap-2 mb-1"><MapPin size={13} className="text-slate-400" /><span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Address</span></div>
                <div><label className="text-xs text-slate-400 mb-1 block">Street Address</label><input value={company.address_line1 || ''} onChange={(e) => setCompany({...company, address_line1: e.target.value})} placeholder="123 Main St, Suite 400" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                <div className="grid grid-cols-2 gap-4">
                  <div><label className="text-xs text-slate-400 mb-1 block">City</label><input value={company.city || ''} onChange={(e) => setCompany({...company, city: e.target.value})} placeholder="San Francisco" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                  <div><label className="text-xs text-slate-400 mb-1 block">State / Province</label><input value={company.state || ''} onChange={(e) => setCompany({...company, state: e.target.value})} placeholder="CA" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div><label className="text-xs text-slate-400 mb-1 block">Country</label><input value={company.country || ''} onChange={(e) => setCompany({...company, country: e.target.value})} placeholder="United States" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                  <div><label className="text-xs text-slate-400 mb-1 block">Postal Code</label><input value={company.postal_code || ''} onChange={(e) => setCompany({...company, postal_code: e.target.value})} placeholder="94105" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" /></div>
                </div>
              </div>

              {/* -- Social Links -- */}
              <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                <div className="flex items-center gap-2 mb-1"><Link2 size={13} className="text-slate-400" /><span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Social Links</span></div>
                <div className="grid grid-cols-2 gap-4">
                  {[['linkedin', 'LinkedIn', 'https://linkedin.com/company/...'], ['twitter', 'Twitter / X', 'https://twitter.com/...'], ['facebook', 'Facebook', 'https://facebook.com/...'], ['instagram', 'Instagram', 'https://instagram.com/...']]
                    .map(([key, label, ph]) => (
                      <div key={key}>
                        <label className="text-xs text-slate-400 mb-1 block">{label}</label>
                        <input value={(company.social_links || {})[key] || ''} onChange={(e) => setCompany({...company, social_links: {...(company.social_links || {}), [key]: e.target.value}})} placeholder={ph} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" />
                      </div>
                    ))
                  }
                </div>
              </div>

              {/* -- Locale -- */}
              <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                <div className="flex items-center gap-2 mb-1"><Globe size={13} className="text-slate-400" /><span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Locale</span></div>
                <div className="grid grid-cols-2 gap-4">
                  <div><label className="text-xs text-slate-400 mb-1 block">Timezone <span className="text-red-400">*</span></label><select value={company.timezone || 'UTC'} onChange={(e) => updateCompanyField('timezone', e.target.value)} aria-invalid={Boolean(companyValidationErrors.timezone)} className={`w-full px-3 py-2 bg-slate-50 border rounded-lg text-sm focus:outline-none focus:ring-2 ${companyValidationErrors.timezone ? 'border-red-300 focus:ring-red-100' : 'border-slate-200 focus:ring-blue-200'}`}>{[
                        { value: 'UTC', label: 'UTC / GMT (+00:00)' },
                        { value: 'Pacific/Honolulu', label: 'Hawaii (HST, -10:00)' },
                        { value: 'America/Anchorage', label: 'Alaska (AKST, -09:00)' },
                        { value: 'America/Los_Angeles', label: 'Pacific Time - Los Angeles (PST, -08:00)' },
                        { value: 'America/Vancouver', label: 'Pacific Time - Vancouver (PST, -08:00)' },
                        { value: 'America/Denver', label: 'Mountain Time - Denver (MST, -07:00)' },
                        { value: 'America/Phoenix', label: 'Arizona - Phoenix (MST, -07:00)' },
                        { value: 'America/Chicago', label: 'Central Time - Chicago (CST, -06:00)' },
                        { value: 'America/Mexico_City', label: 'Mexico City (CST, -06:00)' },
                        { value: 'America/New_York', label: 'Eastern Time - New York (EST, -05:00)' },
                        { value: 'America/Toronto', label: 'Eastern Time - Toronto (EST, -05:00)' },
                        { value: 'America/Bogota', label: 'Bogota (COT, -05:00)' },
                        { value: 'America/Lima', label: 'Lima (PET, -05:00)' },
                        { value: 'America/Caracas', label: 'Caracas (VET, -04:00)' },
                        { value: 'America/Halifax', label: 'Atlantic Time - Halifax (AST, -04:00)' },
                        { value: 'America/Santiago', label: 'Santiago (CLT, -04:00)' },
                        { value: 'America/St_Johns', label: 'Newfoundland (NST, -03:30)' },
                        { value: 'America/Sao_Paulo', label: 'Sao Paulo (BRT, -03:00)' },
                        { value: 'America/Argentina/Buenos_Aires', label: 'Buenos Aires (ART, -03:00)' },
                        { value: 'Atlantic/Reykjavik', label: 'Reykjavik (GMT, +00:00)' },
                        { value: 'Europe/London', label: 'London (GMT/BST, +00:00)' },
                        { value: 'Europe/Dublin', label: 'Dublin (GMT/IST, +00:00)' },
                        { value: 'Europe/Lisbon', label: 'Lisbon (WET, +00:00)' },
                        { value: 'Africa/Casablanca', label: 'Casablanca (WET, +00:00)' },
                        { value: 'Europe/Paris', label: 'Paris (CET, +01:00)' },
                        { value: 'Europe/Berlin', label: 'Berlin (CET, +01:00)' },
                        { value: 'Europe/Madrid', label: 'Madrid (CET, +01:00)' },
                        { value: 'Europe/Rome', label: 'Rome (CET, +01:00)' },
                        { value: 'Europe/Amsterdam', label: 'Amsterdam (CET, +01:00)' },
                        { value: 'Europe/Brussels', label: 'Brussels (CET, +01:00)' },
                        { value: 'Europe/Zurich', label: 'Zurich (CET, +01:00)' },
                        { value: 'Europe/Vienna', label: 'Vienna (CET, +01:00)' },
                        { value: 'Europe/Stockholm', label: 'Stockholm (CET, +01:00)' },
                        { value: 'Europe/Oslo', label: 'Oslo (CET, +01:00)' },
                        { value: 'Europe/Copenhagen', label: 'Copenhagen (CET, +01:00)' },
                        { value: 'Europe/Warsaw', label: 'Warsaw (CET, +01:00)' },
                        { value: 'Europe/Prague', label: 'Prague (CET, +01:00)' },
                        { value: 'Europe/Budapest', label: 'Budapest (CET, +01:00)' },
                        { value: 'Africa/Lagos', label: 'Lagos / West Africa (WAT, +01:00)' },
                        { value: 'Europe/Helsinki', label: 'Helsinki (EET, +02:00)' },
                        { value: 'Europe/Athens', label: 'Athens (EET, +02:00)' },
                        { value: 'Europe/Bucharest', label: 'Bucharest (EET, +02:00)' },
                        { value: 'Europe/Kyiv', label: 'Kyiv (EET, +02:00)' },
                        { value: 'Africa/Cairo', label: 'Cairo (EET, +02:00)' },
                        { value: 'Africa/Johannesburg', label: 'Johannesburg (SAST, +02:00)' },
                        { value: 'Asia/Jerusalem', label: 'Jerusalem (IST, +02:00)' },
                        { value: 'Asia/Beirut', label: 'Beirut (EET, +02:00)' },
                        { value: 'Europe/Istanbul', label: 'Istanbul (TRT, +03:00)' },
                        { value: 'Europe/Moscow', label: 'Moscow (MSK, +03:00)' },
                        { value: 'Asia/Baghdad', label: 'Baghdad (AST, +03:00)' },
                        { value: 'Asia/Riyadh', label: 'Riyadh (AST, +03:00)' },
                        { value: 'Asia/Qatar', label: 'Qatar (AST, +03:00)' },
                        { value: 'Africa/Nairobi', label: 'Nairobi / East Africa (EAT, +03:00)' },
                        { value: 'Asia/Tehran', label: 'Tehran (IRST, +03:30)' },
                        { value: 'Asia/Dubai', label: 'Dubai (GST, +04:00)' },
                        { value: 'Asia/Muscat', label: 'Muscat (GST, +04:00)' },
                        { value: 'Asia/Karachi', label: 'Karachi / Pakistan (PKT, +05:00)' },
                        { value: 'Asia/Tashkent', label: 'Tashkent (UZT, +05:00)' },
                        { value: 'Asia/Kolkata', label: 'India / Kolkata (IST, +05:30)' },
                        { value: 'Asia/Colombo', label: 'Colombo (IST, +05:30)' },
                        { value: 'Asia/Kathmandu', label: 'Kathmandu (NPT, +05:45)' },
                        { value: 'Asia/Dhaka', label: 'Dhaka (BST, +06:00)' },
                        { value: 'Asia/Almaty', label: 'Almaty (ALMT, +06:00)' },
                        { value: 'Asia/Yangon', label: 'Yangon (MMT, +06:30)' },
                        { value: 'Asia/Bangkok', label: 'Bangkok (ICT, +07:00)' },
                        { value: 'Asia/Jakarta', label: 'Jakarta (WIB, +07:00)' },
                        { value: 'Asia/Ho_Chi_Minh', label: 'Ho Chi Minh City (ICT, +07:00)' },
                        { value: 'Asia/Shanghai', label: 'China / Shanghai (CST, +08:00)' },
                        { value: 'Asia/Hong_Kong', label: 'Hong Kong (HKT, +08:00)' },
                        { value: 'Asia/Taipei', label: 'Taipei (CST, +08:00)' },
                        { value: 'Asia/Singapore', label: 'Singapore (SGT, +08:00)' },
                        { value: 'Asia/Kuala_Lumpur', label: 'Kuala Lumpur (MYT, +08:00)' },
                        { value: 'Asia/Manila', label: 'Manila (PHT, +08:00)' },
                        { value: 'Asia/Seoul', label: 'Seoul (KST, +09:00)' },
                        { value: 'Asia/Tokyo', label: 'Tokyo (JST, +09:00)' },
                        { value: 'Australia/Perth', label: 'Perth (AWST, +08:00)' },
                        { value: 'Australia/Darwin', label: 'Darwin (ACST, +09:30)' },
                        { value: 'Australia/Adelaide', label: 'Adelaide (ACST, +09:30)' },
                        { value: 'Australia/Brisbane', label: 'Brisbane (AEST, +10:00)' },
                        { value: 'Australia/Sydney', label: 'Sydney (AEST, +10:00)' },
                        { value: 'Australia/Melbourne', label: 'Melbourne (AEST, +10:00)' },
                        { value: 'Pacific/Auckland', label: 'Auckland (NZST, +12:00)' },
                        { value: 'Pacific/Fiji', label: 'Fiji (FJT, +12:00)' },
                      ].map(tz => <option key={tz.value} value={tz.value}>{tz.label}</option>)}</select>{companyValidationErrors.timezone && <p className="mt-1 text-xs text-red-600">{companyValidationErrors.timezone}</p>}</div>
                  <div><label className="text-xs text-slate-400 mb-1 block">Language <span className="text-red-400">*</span></label><select value={company.language || 'en'} onChange={(e) => updateCompanyField('language', e.target.value)} aria-invalid={Boolean(companyValidationErrors.language)} className={`w-full px-3 py-2 bg-slate-50 border rounded-lg text-sm focus:outline-none focus:ring-2 ${companyValidationErrors.language ? 'border-red-300 focus:ring-red-100' : 'border-slate-200 focus:ring-blue-200'}`}>{[['en','English'],['es','Spanish'],['fr','French'],['de','German'],['pt','Portuguese'],['ar','Arabic'],['ja','Japanese'],['ko','Korean'],['zh','Chinese']].map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select>{companyValidationErrors.language && <p className="mt-1 text-xs text-red-600">{companyValidationErrors.language}</p>}</div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <button onClick={saveCompany} disabled={saving === 'company'} className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50 transition-colors"><Save size={14} /> {saving === 'company' ? 'Saving...' : 'Save Company Profile'}</button>
                {saving === 'company_done' && <span className="flex items-center gap-1 text-emerald-600 text-xs"><Check size={13} /> Saved</span>}
              </div>
            </div>
          )}

          {/* === AI CONFIG === */}
          {activeTab === 'ai' && company && (
            <AiSettingsTab
              company={company} setCompany={setCompany} saveCompany={saveCompany}
              saving={saving} setSaving={setSaving}
              llmEngines={llmEngines} selectedLlmEngine={selectedLlmEngine} refreshAiConfig={refreshAiConfig}
              aiConfigError={aiConfigError}
              aiAgents={aiAgents} setAiAgents={setAiAgents}
              editingLlmId={editingLlmId} setEditingLlmId={setEditingLlmId} llmDraft={llmDraft} setLlmDraft={setLlmDraft}
              showAddLlmForm={showAddLlmForm} setShowAddLlmForm={setShowAddLlmForm} addLlmForm={addLlmForm} setAddLlmForm={setAddLlmForm}
              editingAgentId={editingAgentId} setEditingAgentId={setEditingAgentId} agentDraft={agentDraft} setAgentDraft={setAgentDraft}
              showAddAgentForm={showAddAgentForm} setShowAddAgentForm={setShowAddAgentForm} addAgentForm={addAgentForm} setAddAgentForm={setAddAgentForm}
              isAdmin={isAdmin}
            />
          )}
          {/* === TEMPLATES & FAQs === */}
          {activeTab === 'templates' && (
            <div className="space-y-6">
              <h2 className="text-lg font-semibold text-slate-900">Templates &amp; FAQs</h2>

              {/* AI Response Style */}
              <div className="bg-white border border-slate-100 rounded-xl p-5">
                <AiResponseTemplatesSection isAdmin={isAdmin} />
              </div>

              {/* === FAQs === */}
              <div className="bg-white border border-slate-100 rounded-xl p-6">
                <div className="flex items-center justify-between mb-4"><div className="flex items-center gap-2"><HelpCircle size={16} className="text-blue-600" /><h3 className="text-sm font-semibold text-slate-900">FAQs ({faqs.length})</h3></div><button onClick={() => setShowFaqForm(true)} className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700"><Plus size={12} /> Add FAQ</button></div>
                <div className="space-y-2">{faqs.map(f => (<div key={f.id} className="flex items-start justify-between p-3 bg-slate-50 rounded-lg border border-slate-100"><div><h4 className="text-sm font-medium text-slate-800">Q: {f.question}</h4><p className="text-xs text-slate-500 mt-0.5">A: {f.answer}</p></div><button onClick={() => deleteFaq(f.id)} className="text-slate-400 hover:text-red-500 p-1"><Trash2 size={14} /></button></div>))}{faqs.length === 0 && <p className="text-center text-slate-400 text-sm py-4">No FAQs added yet.</p>}</div>
              </div>
              {showFaqForm && (<div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4"><div className="bg-white rounded-2xl w-full max-w-lg p-6"><div className="flex items-center justify-between mb-4"><h3 className="text-lg font-bold text-slate-900">Add FAQ</h3><button onClick={() => setShowFaqForm(false)} className="text-slate-400"><X size={20} /></button></div><div className="space-y-3"><input value={faqForm.question} onChange={(e) => setFaqForm({...faqForm, question: e.target.value})} placeholder="Question *" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm" /><textarea value={faqForm.answer} onChange={(e) => setFaqForm({...faqForm, answer: e.target.value})} placeholder="Answer *" rows={3} className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm resize-none" /><button onClick={createFaq} className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium">Add FAQ</button></div></div></div>)}
            </div>
          )}

          {/* === TEAM MEMBERS === */}
          {activeTab === 'users' && (
            <div className="space-y-6">
              {onboardingInviteMode && (
                <div className="rounded-2xl border border-blue-200 bg-blue-50 px-5 py-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-blue-900">Final onboarding step: invite your first teammate</p>
                      <p className="text-xs text-blue-700 mt-1">
                        We brought you straight to the add-user page after company setup. Send an invite now, or continue to billing and add teammates later from Settings.
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => navigate('/billing', { replace: true })}
                      className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
                    >
                      Continue to Billing
                    </button>
                  </div>
                </div>
              )}
              <div className="flex items-center justify-between">
                <div><h2 className="text-lg font-semibold text-slate-900">Team Members</h2>
                <p className="text-sm text-slate-400">{onboardingInviteMode ? 'Invite a teammate to finish setup, then continue to billing.' : isAdmin ? 'Full access: edit roles, change passwords, delete members' : 'View only'}</p></div>
                {isAdmin && (
                  <button
                    onClick={() => {
                      const firstRole = availableCreateRoles[0] || '';
                      setCreateUserForm((prev) => ({ ...prev, role: firstRole }));
                      setShowCreateUserModal(true);
                    }}
                    className="flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500"
                    data-testid="add-team-user-btn"
                  >
                    <Plus size={14} /> Add User
                  </button>
                )}
              </div>
              <div className="bg-white border border-slate-100 rounded-xl overflow-hidden">
                <table className="w-full">
                  <thead><tr className="border-b border-slate-100">
                    <th className="text-left text-xs text-slate-400 font-medium px-5 py-3">User</th>
                    <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Role</th>
                    <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Sub Role</th>
                    <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Status</th>
                    <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Last Login</th>
                    <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Actions</th>
                  </tr></thead>
                  <tbody>
                    {users.map(u => (
                      <tr key={u.id} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                        <td className="px-5 py-3"><div className="flex items-center gap-3"><div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center text-xs font-bold text-blue-600">{u.name?.charAt(0)}</div><div><p className="text-sm text-slate-700 font-medium">{u.name}</p><p className="text-[11px] text-slate-400">{u.email}</p></div></div></td>
                        <td className="px-4 py-3"><span className={`text-xs px-2 py-0.5 rounded font-medium ${u.role === 'admin' ? 'bg-red-50 text-red-600' : u.role === 'company_agent' ? 'bg-green-50 text-green-600' : 'bg-slate-100 text-slate-500'}`}>{ROLE_LABELS[u.role] || u.role}</span></td>
                        <td className="px-4 py-3 text-xs text-slate-500">{u.sub_role || '-'}</td>
                        <td className="px-4 py-3"><span className={`text-xs px-2 py-0.5 rounded ${u.status === 'active' ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-100 text-slate-400'}`}>{u.status}</span></td>
                        <td className="px-4 py-3 text-xs text-slate-400">{u.last_login ? new Date(u.last_login).toLocaleDateString() : 'Never'}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            {/* Edit Button - Admin only */}
                            {isAdmin && u.id !== user?.id && (
                              <button onClick={() => openEditUser(u)} className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="Edit user"><Edit size={13} /></button>
                            )}
                            {/* Password Change - Admin or self */}
                            {(isAdmin || u.id === user?.id) && u.auth_provider !== 'google' && (
                              <button onClick={() => openPasswordModal(u)} className="p-1.5 text-slate-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg transition-colors" title="Change password"><Lock size={13} /></button>
                            )}
                            {/* Delete - Admin only */}
                            {isAdmin && u.id !== user?.id && (
                              <button onClick={() => deleteUser(u)} className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors" title="Delete user"><UserMinus size={13} /></button>
                            )}
                            {u.auth_provider === 'google' && <span className="text-[10px] text-slate-300 italic ml-1">Google Auth</span>}
                          </div>
                        </td>
                      </tr>
                    ))}
                    {users.length === 0 && (
                      <tr>
                        <td colSpan={6} className="px-5 py-8 text-center text-sm text-slate-400">
                          {onboardingInviteMode ? 'No teammates invited yet. Use Add User to send the first invitation.' : 'No team members found.'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              {/* Permissions info */}
              <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
                <h4 className="text-xs font-semibold text-slate-600 mb-2">Role Permissions</h4>
                <div className="grid grid-cols-2 gap-3 text-[11px] text-slate-500">
                  <div><span className="font-medium text-red-600">Admin:</span> Full access - add, edit, delete users and configure everything</div>
                  <div><span className="font-medium text-green-600">Agent:</span> Inbox, leads, customers, tickets</div>
                </div>
              </div>
            </div>
          )}

          {/* === WEBHOOKS === */}
          {activeTab === 'webhooks' && (
            <div className="space-y-6">
              <div><h2 className="text-lg font-semibold text-slate-900 mb-1">Webhook Integration</h2><p className="text-sm text-slate-400">Connect your social media platforms using these webhook URLs</p></div>

              {webhookInfo ? (
                <>
                  {/* Webhook URLs */}
                  <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2"><Link2 size={16} className="text-blue-600" /> Webhook URLs</h3>
                    {Object.entries(webhookInfo.webhook_urls || {}).map(([key, url]) => (
                      <div key={key} className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
                        <span className="text-xs font-medium text-slate-600 w-24 capitalize">{key.replace('_', ' ')}</span>
                        <code className="flex-1 text-[11px] text-slate-500 font-mono bg-white px-3 py-1.5 rounded border border-slate-200 truncate">{url}</code>
                        <button onClick={() => copyToClipboard(url, 'Webhook URL')} className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="Copy"><Copy size={14} /></button>
                      </div>
                    ))}
                    <div className="flex items-center gap-2 p-3 bg-blue-50 rounded-lg border border-blue-200">
                      <Key size={14} className="text-blue-600" />
                      <span className="text-xs text-blue-700">Verify Token: <code className="font-mono bg-white px-2 py-0.5 rounded border border-blue-200">{webhookInfo.verify_token}</code></span>
                      <button onClick={() => copyToClipboard(webhookInfo.verify_token, 'Verify token')} className="ml-auto text-blue-400 hover:text-blue-600"><Copy size={12} /></button>
                    </div>
                  </div>

                  {/* Setup Guides */}
                  {Object.entries(webhookInfo.setup_guide || {}).map(([platform, guide]) => (
                    <div key={platform} className="bg-white border border-slate-100 rounded-xl p-6">
                      <h3 className="text-sm font-semibold text-slate-800 mb-3 capitalize flex items-center gap-2">
                        <MessageSquare size={16} className={platform === 'whatsapp' ? 'text-emerald-500' : platform === 'facebook' ? 'text-blue-500' : platform === 'instagram' ? 'text-pink-500' : platform === 'email' ? 'text-sky-500' : 'text-violet-500'} />
                        {platform.replace('_', ' ')} Setup
                      </h3>
                      <ol className="space-y-2">
                        {(guide.steps || []).map((step, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span className="text-[10px] w-5 h-5 rounded-full bg-slate-100 flex items-center justify-center flex-shrink-0 font-medium text-slate-500 mt-0.5">{i + 1}</span>
                            <span className="text-xs text-slate-600">{step.replace(/^\d+\.\s*/, '')}</span>
                          </li>
                        ))}
                      </ol>
                      {guide.fields_to_subscribe && (
                        <div className="mt-3 flex items-center gap-2">
                          <span className="text-[10px] text-slate-400">Subscribe to:</span>
                          {guide.fields_to_subscribe.map(f => (
                            <span key={f} className="text-[10px] px-2 py-0.5 bg-slate-100 text-slate-600 rounded font-mono">{f}</span>
                          ))}
                        </div>
                      )}
                      {guide.sample_payload && (
                        <div className="mt-3 bg-slate-50 rounded-lg p-3">
                          <p className="text-[10px] text-slate-400 mb-1">Sample Payload:</p>
                          <pre className="text-[10px] text-slate-600 font-mono whitespace-pre-wrap">{JSON.stringify(guide.sample_payload, null, 2)}</pre>
                        </div>
                      )}
                    </div>
                  ))}

                  <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
                    <h4 className="text-sm font-medium text-amber-800 mb-2">How Lead Capture Works</h4>
                    <ul className="space-y-1 text-xs text-amber-700">
                      <li>• When someone sends a message via WhatsApp/Facebook/Instagram/Email/Chat Widget, they're automatically added as a lead</li>
                      <li>• Social media ad form submissions are captured via the Lead Form webhook</li>
                      <li>• AI automatically scores each new lead and determines their nurturing phase</li>
                      <li>• Nurture messages are auto-generated based on lead score and phase</li>
                    </ul>
                  </div>
                </>
              ) : (
                <div className="text-center py-10 text-slate-400"><p className="text-sm">Loading webhook information...</p></div>
              )}
            </div>
          )}

          {/* === INTEGRATIONS === */}
          {activeTab === 'integrations' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Integrations</h2>
                <p className="text-xs text-slate-400 mt-1">Connect external tool servers via Model Context Protocol (MCP).</p>
              </div>

              {/* MCP Servers */}
              <div className="bg-white border border-slate-100 rounded-xl p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2"><Globe size={14} className="text-emerald-500" /> MCP Servers ({mcpServers.length})</h3>
                    <p className="text-[10px] text-slate-400 mt-0.5 max-w-3xl">
                      Register tool/automation endpoints here. In AI Config you can link an agent to a server; strict MCP tool routing may require <span className="font-mono">AI_PROVIDER=mcp</span> in deployment.
                    </p>
                  </div>
                  {isAdmin && <button onClick={() => { setShowAddMcpForm(true); setAddMcpForm({ endpoint: '', region: '', status: 'active', capabilities: {} }); }} className="flex items-center gap-1 px-2.5 py-1.5 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-500"><Plus size={12} /> Add Server</button>}
                </div>
                <div className="mb-5 grid grid-cols-1 lg:grid-cols-3 gap-3">
                  {MCP_PRESETS.map((preset) => {
                    const connected = mcpServers.some((server) => server.endpoint === preset.endpoint);
                    return (
                      <div key={preset.id} className="rounded-xl border border-emerald-100 bg-emerald-50/40 p-4 flex flex-col gap-3">
                        <div>
                          <p className="text-xs font-semibold text-emerald-800">{preset.name}</p>
                          <p className="text-[11px] text-slate-500 mt-1">{preset.description}</p>
                        </div>
                        <div className="space-y-1 text-[10px] text-slate-500">
                          <p><span className="font-semibold text-slate-600">Endpoint:</span> <span className="font-mono break-all">{preset.endpoint}</span></p>
                          <p><span className="font-semibold text-slate-600">Region:</span> {preset.region}</p>
                          <p><span className="font-semibold text-slate-600">Auth:</span> {preset.capabilities?.auth || 'Preset default'}</p>
                          <p>{preset.capabilities?.connection_details}</p>
                        </div>
                        <div className="mt-auto flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => connectMcpPreset(preset)}
                            disabled={!isAdmin || mcpPresetSaving === preset.id}
                            className="px-3 py-2 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-500 disabled:opacity-50"
                          >
                            {mcpPresetSaving === preset.id ? 'Connecting...' : connected ? 'Reconnect' : 'Connect preset'}
                          </button>
                          <button
                            type="button"
                            onClick={() => { setShowAddMcpForm(true); setAddMcpForm({ endpoint: preset.endpoint, region: preset.region, status: preset.status, capabilities: preset.capabilities }); }}
                            className="px-3 py-2 bg-white text-emerald-700 border border-emerald-200 rounded-lg text-xs font-medium hover:bg-emerald-50"
                          >
                            Prefill form
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
                {mcpServers.length === 0 ? (
                  <p className="text-xs text-slate-400 py-3">No MCP servers registered yet.</p>
                ) : (
                  <div className="space-y-3">{mcpServers.map(s => (
                    <div key={s.id} className="border border-slate-200 rounded-lg overflow-hidden">
                      <div className="flex items-center justify-between p-3 bg-slate-50">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-slate-700 font-mono truncate">{s.endpoint || '—'}</p>
                          <p className="text-[10px] text-slate-400">Region: {s.region || '—'} | Status: {s.status || 'active'}</p>
                        </div>
                        <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                          <button onClick={async () => { const newStatus = s.status === 'active' ? 'inactive' : 'active'; const r = await api.put(`/mcp/servers/${s.id}`, { status: newStatus }).catch(() => null); if (r) setMcpServers(prev => prev.map(x => x.id === s.id ? { ...x, status: newStatus } : x)); }} className={`relative w-9 h-5 rounded-full transition-colors ${s.status === 'active' ? 'bg-green-500' : 'bg-gray-300'}`}><span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow ${s.status === 'active' ? 'translate-x-4' : ''}`} /></button>
                          {isAdmin && <button onClick={() => { setEditingMcpId(editingMcpId === s.id ? null : s.id); setMcpDraft({ endpoint: s.endpoint || '', region: s.region || '', status: s.status || 'active' }); }} className="p-1.5 hover:bg-slate-200 rounded text-slate-500"><Edit size={13} /></button>}
                          {isAdmin && <button onClick={() => {
                            requestConfirmation({
                              title: 'Remove MCP Server',
                              description: `Remove ${s.endpoint || 'this MCP server'}. Connected agents will lose access to that MCP integration.`,
                              confirmLabel: 'Remove server',
                              onConfirm: async () => {
                                try {
                                  await api.delete(`/mcp/servers/${s.id}`);
                                  setMcpServers(prev => prev.filter(x => x.id !== s.id));
                                  showToast({
                                    type: 'success',
                                    title: 'Server Removed',
                                    message: `${s.endpoint || 'The MCP server'} was removed.`,
                                  });
                                } catch (err) {
                                  showToast({
                                    type: 'error',
                                    title: 'Remove Failed',
                                    message: getErrorMessage(err, 'We could not remove that MCP server.'),
                                  });
                                }
                              },
                            });
                          }} className="p-1.5 hover:bg-red-50 rounded text-slate-400 hover:text-red-500"><Trash2 size={13} /></button>}
                        </div>
                      </div>
                      {editingMcpId === s.id && (
                        <div className="p-4 bg-white border-t border-slate-100 space-y-3">
                          <div className="grid grid-cols-2 gap-3">
                            <div className="col-span-2"><label className="text-[10px] text-slate-400 font-medium mb-1 block">Endpoint URL *</label><input value={mcpDraft.endpoint} onChange={ev => setMcpDraft(p => ({...p, endpoint: ev.target.value}))} placeholder="https://mcp.example.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono" /></div>
                            <div><label className="text-[10px] text-slate-400 font-medium mb-1 block">Region</label><input value={mcpDraft.region} onChange={ev => setMcpDraft(p => ({...p, region: ev.target.value}))} placeholder="us-east-1" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" /></div>
                            <div><label className="text-[10px] text-slate-400 font-medium mb-1 block">Status</label><select value={mcpDraft.status} onChange={ev => setMcpDraft(p => ({...p, status: ev.target.value}))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm"><option value="active">Active</option><option value="inactive">Inactive</option><option value="maintenance">Maintenance</option></select></div>
                          </div>
                          <div className="flex gap-2">
                            <button onClick={async () => { const r = await api.put(`/mcp/servers/${s.id}`, mcpDraft).catch(() => null); if (r) { setMcpServers(prev => prev.map(x => x.id === s.id ? { ...x, ...r.data } : x)); setEditingMcpId(null); } }} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-500">Save Changes</button>
                            <button onClick={() => setEditingMcpId(null)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs">Cancel</button>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}</div>
                )}
                {showAddMcpForm && (
                  <div className="mt-4 border border-emerald-200 bg-emerald-50/40 rounded-xl p-4 space-y-3">
                    <p className="text-xs font-semibold text-emerald-700">Register MCP Server</p>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="col-span-2"><label className="text-[10px] text-slate-400 font-medium mb-1 block">Endpoint URL *</label><input value={addMcpForm.endpoint} onChange={ev => setAddMcpForm(p => ({...p, endpoint: ev.target.value}))} placeholder="https://mcp.example.com" className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm font-mono" /></div>
                      <div><label className="text-[10px] text-slate-400 font-medium mb-1 block">Region</label><input value={addMcpForm.region} onChange={ev => setAddMcpForm(p => ({...p, region: ev.target.value}))} placeholder="us-east-1" className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm" /></div>
                      <div><label className="text-[10px] text-slate-400 font-medium mb-1 block">Status</label><select value={addMcpForm.status} onChange={ev => setAddMcpForm(p => ({...p, status: ev.target.value}))} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm"><option value="active">Active</option><option value="inactive">Inactive</option></select></div>
                    </div>
                    {Object.keys(addMcpForm.capabilities || {}).length > 0 && (
                      <div className="rounded-lg border border-emerald-100 bg-white p-3">
                        <p className="text-[10px] uppercase tracking-wide text-emerald-700 font-semibold mb-2">Pre-filled connection details</p>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          {Object.entries(addMcpForm.capabilities || {}).map(([key, value]) => (
                            <div key={key} className="text-[10px] text-slate-500">
                              <span className="block font-semibold text-slate-600 capitalize">{key.replace(/_/g, ' ')}</span>
                              <span className="break-words">{String(value || '')}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="flex gap-2">
                      <button onClick={async () => { if (!addMcpForm.endpoint.trim()) return; const r = await api.post('/mcp/servers', addMcpForm).catch(() => null); if (r?.data) { setMcpServers(prev => [{ ...r.data, capabilities: r.data.capabilities || addMcpForm.capabilities || {} }, ...prev]); setShowAddMcpForm(false); } }} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-500">Register Server</button>
                      <button onClick={() => setShowAddMcpForm(false)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs">Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* === UNIFICATION === */}
          {activeTab === 'unification' && (
            <UnificationTab
              isAdmin={isAdmin}
              unificationLoading={unificationLoading} runAutoDetect={runAutoDetect}
              unifiedProfiles={unifiedProfiles} mergeSuggestions={mergeSuggestions}
              acceptSuggestion={acceptSuggestion} rejectSuggestion={rejectSuggestion}
              manualMergeIds={manualMergeIds} setManualMergeIds={setManualMergeIds} manualMerge={manualMerge}
              selectedUnifiedProfile={selectedUnifiedProfile} setSelectedUnifiedProfile={setSelectedUnifiedProfile}
              suggestionDetail={suggestionDetail} setSuggestionDetail={setSuggestionDetail}
              splitFromProfile={splitFromProfile}
              onIdentityEvent={handleUnificationIdentityEvent}
            />
          )}

          {/* === SYSTEM LOGS === */}
          {activeTab === 'logs' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-900">System Logs</h2>
                <button onClick={() => api.get('/system/logs?limit=50').then(r => setSystemLogs(r.data || [])).catch(() => {})} className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 rounded-lg text-xs text-slate-600 hover:bg-slate-200">
                  <Activity size={12} /> Refresh
                </button>
              </div>
              {systemLogs.length === 0 ? (
                <div className="text-center py-12 text-slate-400 text-sm">No system logs recorded yet.</div>
              ) : (
                <div className="bg-white border border-slate-100 rounded-xl overflow-hidden">
                  <table className="w-full">
                    <thead><tr className="border-b border-slate-100">
                      <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Time</th>
                      <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Level</th>
                      <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Action</th>
                      <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Details</th>
                    </tr></thead>
                    <tbody>
                      {systemLogs.map(l => (
                        <tr key={l.id} className="border-b border-slate-50">
                          <td className="px-4 py-2.5 text-xs text-slate-400 whitespace-nowrap">{new Date(l.created_at).toLocaleString()}</td>
                          <td className="px-4 py-2.5"><span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${l.level === 'error' ? 'bg-red-50 text-red-600' : l.level === 'warn' ? 'bg-amber-50 text-amber-600' : 'bg-slate-100 text-slate-500'}`}>{l.level || 'info'}</span></td>
                          <td className="px-4 py-2.5 text-xs text-slate-600">{l.action}</td>
                          <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[300px] truncate">{typeof l.details === 'object' ? JSON.stringify(l.details) : l.details || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* === NOTIFICATIONS === */}
          {activeTab === 'notifications' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 mb-1">Notification Preferences</h2>
                <p className="text-sm text-slate-400">Control which in-app and email notifications you receive.</p>
              </div>

              {!notifSettings ? (
                <div className="flex items-center justify-center py-16 text-slate-400 text-sm">Loading...</div>
              ) : (
                <>
                  {/* In-app notifications */}
                  <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-1">
                    <h3 className="text-sm font-semibold text-slate-800 mb-4 flex items-center gap-2">
                      <Bell size={15} className="text-blue-500" /> In-App Notifications
                    </h3>
                    {[
                      { key: 'notify_new_message',           label: 'New message received',           desc: 'Get notified when a customer sends a new message' },
                      { key: 'notify_new_lead',              label: 'New lead created',                desc: 'Get notified when a new lead is added' },
                      { key: 'notify_new_ticket',            label: 'New support ticket',              desc: 'Get notified when a new ticket is opened' },
                      { key: 'notify_ticket_updated',        label: 'Ticket status updated',           desc: 'Get notified when a ticket status changes' },
                      { key: 'notify_conversation_assigned', label: 'Conversation assigned to you',    desc: 'Get notified when a conversation is assigned to you' },
                      { key: 'notify_system_updates',        label: 'System & setup reminders',        desc: 'Setup tips, feature announcements, and system alerts' },
                    ].map(item => (
                      <div key={item.key} className="flex items-center justify-between py-3 border-b border-slate-50 last:border-0">
                        <div>
                          <p className="text-sm font-medium text-slate-700">{item.label}</p>
                          <p className="text-xs text-slate-400 mt-0.5">{item.desc}</p>
                        </div>
                        <button
                          onClick={() => setNotifSettings(prev => ({ ...prev, [item.key]: !prev[item.key] }))}
                          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200 focus:outline-none ${notifSettings[item.key] ? 'bg-blue-500' : 'bg-slate-200'}`}
                        >
                          <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform duration-200 ${notifSettings[item.key] ? 'translate-x-4' : 'translate-x-1'}`} />
                        </button>
                      </div>
                    ))}
                  </div>

                  {/* Email digest */}
                  <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                      <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-indigo-500"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                      Email Digest
                    </h3>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-slate-700">Receive email digest</p>
                        <p className="text-xs text-slate-400 mt-0.5">Get a summary of activity sent to your email</p>
                      </div>
                      <button
                        onClick={() => setNotifSettings(prev => ({ ...prev, email_digest: !prev.email_digest }))}
                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200 ${notifSettings.email_digest ? 'bg-blue-500' : 'bg-slate-200'}`}
                      >
                        <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform duration-200 ${notifSettings.email_digest ? 'translate-x-4' : 'translate-x-1'}`} />
                      </button>
                    </div>
                    {notifSettings.email_digest && (
                      <div>
                        <label className="text-xs text-slate-500 font-medium mb-1.5 block">Frequency</label>
                        <div className="flex gap-2">
                          {['daily', 'weekly', 'never'].map(freq => (
                            <button
                              key={freq}
                              onClick={() => setNotifSettings(prev => ({ ...prev, email_digest_frequency: freq }))}
                              className={`px-4 py-1.5 rounded-lg text-xs font-medium border transition-colors capitalize ${notifSettings.email_digest_frequency === freq ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'}`}
                            >
                              {freq}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    <button
                      onClick={saveNotifSettings}
                      disabled={notifSaving}
                      className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50 transition-colors"
                    >
                      {notifSaving ? 'Saving...' : notifSaved ? <><Check size={14} /> Saved</> : <><Save size={14} /> Save preferences</>}
                    </button>
                    {notifSaved && <span className="text-xs text-emerald-600 font-medium">Preferences updated!</span>}
                  </div>
                </>
              )}
            </div>
          )}

          {/* === SECURITY === */}
          {activeTab === 'security' && (
            <div className="space-y-6">
              <h2 className="text-lg font-semibold text-slate-900">Security Center</h2>

              {/* Overview Cards */}
              {securityOverview && (
                <div className="grid grid-cols-4 gap-4">
                  {[
                    { label: 'Total Users', value: securityOverview.total_users, bgClass: 'bg-blue-50 border-blue-200', textClass: 'text-blue-600' },
                    { label: 'Active Sessions', value: securityOverview.active_sessions, bgClass: 'bg-green-50 border-green-200', textClass: 'text-green-600' },
                    { label: 'API Keys', value: securityOverview.active_api_keys, bgClass: 'bg-purple-50 border-purple-200', textClass: 'text-purple-600' },
                    { label: 'Pending Resets', value: securityOverview.pending_password_resets, bgClass: 'bg-amber-50 border-amber-200', textClass: 'text-amber-600' },
                  ].map(s => (
                    <div key={s.label} className={`${s.bgClass} border rounded-xl p-4 text-center`}>
                      <p className={`text-2xl font-bold ${s.textClass}`}>{s.value}</p>
                      <p className="text-[11px] text-slate-500 mt-0.5">{s.label}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Password Policy */}
              <div className="bg-white border border-slate-100 rounded-xl p-6">
                <h3 className="text-sm font-semibold text-slate-800 mb-3 flex items-center gap-2"><Shield size={16} className="text-emerald-600" /> Password Policy</h3>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { label: 'Minimum Length', value: '8 characters', active: true },
                    { label: 'Requires Letters', value: 'Yes', active: true },
                    { label: 'Requires Numbers', value: 'Yes', active: true },
                    { label: 'Requires Special Characters', value: 'Yes', active: true },
                    { label: 'JWT Token Expiry', value: '8 hours', active: true },
                    { label: 'Session Expiry', value: '7 days', active: true },
                  ].map(p => (
                    <div key={p.label} className="flex items-center justify-between p-2.5 bg-slate-50 rounded-lg">
                      <span className="text-xs text-slate-600">{p.label}</span>
                      <span className="text-xs font-medium text-emerald-600 flex items-center gap-1"><CheckCircle size={12} /> {p.value}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Active Sessions */}
              <div className="bg-white border border-slate-100 rounded-xl p-6">
                <h3 className="text-sm font-semibold text-slate-800 mb-3 flex items-center gap-2"><Activity size={16} className="text-blue-600" /> Active Sessions ({sessions.length})</h3>
                <div className="space-y-2">
                  {sessions.slice(0, 10).map(s => (
                    <div key={s.session_token || s.id} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg border border-slate-100">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center text-xs font-bold text-blue-600">{(s.user_name || '?').charAt(0)}</div>
                        <div>
                          <p className="text-xs font-medium text-slate-700">{s.user_name || 'Unknown'}</p>
                          <p className="text-[10px] text-slate-400">{s.user_email} • {new Date(s.created_at).toLocaleDateString()}</p>
                        </div>
                      </div>
                      {isAdmin && (
                        <button onClick={() => revokeSession(s.session_token)} className="text-[10px] px-2 py-1 text-red-500 hover:bg-red-50 rounded border border-red-200">Revoke</button>
                      )}
                    </div>
                  ))}
                  {sessions.length === 0 && <p className="text-center text-slate-400 text-xs py-4">No active sessions</p>}
                </div>
              </div>

              {/* API Keys */}
              {isAdmin && (
                <div className="bg-white border border-slate-100 rounded-xl p-6">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2"><Key size={16} className="text-purple-600" /> API Keys ({apiKeys.length})</h3>
                    <button onClick={() => setShowApiKeyForm(true)} className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-600 text-white rounded-lg text-xs font-medium hover:bg-purple-500"><Plus size={12} /> Generate Key</button>
                  </div>

                  {newApiKey && (
                    <div className="mb-4 bg-emerald-50 border border-emerald-200 rounded-lg p-3">
                      <p className="text-xs text-emerald-700 mb-1 font-medium">New API Key (copy now - won't be shown again):</p>
                      <div className="flex items-center gap-2">
                        <code className="flex-1 text-[11px] font-mono bg-white px-2 py-1 rounded border border-emerald-200 break-all">{newApiKey}</code>
                        <button onClick={() => { copyToClipboard(newApiKey, 'API key'); }} className="p-1.5 text-emerald-600 hover:bg-emerald-100 rounded"><Copy size={14} /></button>
                      </div>
                      <button onClick={() => setNewApiKey('')} className="mt-2 text-[10px] text-slate-400 hover:text-slate-600">Dismiss</button>
                    </div>
                  )}

                  <div className="space-y-2">
                    {apiKeys.map(k => (
                      <div key={k.id} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg border border-slate-100">
                        <div>
                          <p className="text-xs font-medium text-slate-700">{k.name}</p>
                          <p className="text-[10px] text-slate-400 font-mono">{k.key_prefix} • Created by {k.created_by_name} • {new Date(k.created_at).toLocaleDateString()}</p>
                        </div>
                        <button onClick={() => revokeApiKey(k.id)} className="text-[10px] px-2 py-1 text-red-500 hover:bg-red-50 rounded border border-red-200">Revoke</button>
                      </div>
                    ))}
                    {apiKeys.length === 0 && <p className="text-center text-slate-400 text-xs py-4">No API keys generated</p>}
                  </div>

                  {showApiKeyForm && (
                    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                      <div className="bg-white rounded-2xl w-full max-w-sm p-6">
                        <h3 className="text-lg font-bold text-slate-900 mb-4">Generate API Key</h3>
                        <input value={apiKeyName} onChange={(e) => setApiKeyName(e.target.value)} placeholder="Key name (e.g., Production API)" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm mb-4" />
                        <div className="flex gap-3">
                          <button onClick={() => setShowApiKeyForm(false)} className="flex-1 py-2 text-sm text-slate-600 bg-slate-100 rounded-xl">Cancel</button>
                          <button onClick={createApiKey} disabled={!apiKeyName.trim()} className="flex-1 py-2 text-sm text-white bg-purple-600 rounded-xl disabled:opacity-50">Generate</button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Danger Zone */}
              <div className="bg-white border border-red-200 rounded-xl p-6 space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2"><Trash2 size={13} className="text-red-500" /><span className="text-[11px] font-semibold text-red-500 uppercase tracking-wider">Danger Zone</span></div>
                    <h3 className="text-sm font-semibold text-slate-900 mt-2">Delete Account</h3>
                    <p className="text-xs text-slate-500 mt-1">This permanently deletes your sign-in account. Verification is required through your current password or an emailed fingerprint before deletion is allowed.</p>
                  </div>
                  <button onClick={openDeleteAccountModal} className="inline-flex items-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-500 transition-colors shrink-0">
                    <Trash2 size={14} />
                    Delete Account
                  </button>
                </div>
              </div>

              {/* Security Status */}
              <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4">
                <div className="flex items-start gap-3">
                  <Shield size={16} className="text-emerald-600 mt-0.5" />
                  <div>
                    <h4 className="text-sm font-medium text-emerald-700">Security Status: Good</h4>
                    <p className="text-xs text-emerald-600 mt-1">All communications are encrypted with TLS 1.3. JWT tokens expire after 8 hours. Sessions expire after 7 days. Passwords require 8+ characters with letters, numbers, and special characters.</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* company-data tab removed — products managed via /products page */}
          {false && (
            <div className="space-y-6">
              <div><h2 className="text-lg font-semibold text-slate-900 mb-1">Company Products</h2><p className="text-sm text-slate-400">Product data feeds into the AI to provide accurate, personalized responses.</p></div>
              <div className="bg-white border border-slate-100 rounded-xl p-6">
                <div className="flex items-center justify-between mb-4"><div className="flex items-center gap-2"><Package size={16} className="text-blue-600" /><h3 className="text-sm font-semibold text-slate-900">Products & Services ({products.length})</h3></div><button onClick={() => setShowProductForm(true)} className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700"><Plus size={12} /> Add Product</button></div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {products.map(p => (
                    <div
                      key={p.id}
                      onClick={() => setSelectedProduct(p)}
                      className="bg-white border border-slate-100 rounded-xl overflow-hidden hover:border-blue-200 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 cursor-pointer group"
                    >
                      <div className="h-32 bg-slate-50 overflow-hidden relative">
                        {Array.isArray(p.images) && p.images.length > 0 ? (
                          p.images.length === 1 ? (
                            <img src={resolveMediaUrl(p.images[0])} alt={p.name} className="w-full h-full object-cover" />
                          ) : p.images.length === 2 ? (
                            <div className="grid grid-cols-2 h-full gap-0.5">
                              <img src={resolveMediaUrl(p.images[0])} alt={p.name} className="w-full h-full object-cover" />
                              <img src={resolveMediaUrl(p.images[1])} alt={p.name} className="w-full h-full object-cover" />
                            </div>
                          ) : (
                            <div className="grid grid-cols-2 h-full gap-0.5">
                              <img src={resolveMediaUrl(p.images[0])} alt={p.name} className="w-full h-full object-cover" />
                              <div className="grid grid-rows-2 gap-0.5">
                                <img src={resolveMediaUrl(p.images[1])} alt={p.name} className="w-full h-full object-cover" />
                                <div className="relative">
                                  <img src={resolveMediaUrl(p.images[2])} alt={p.name} className="w-full h-full object-cover" />
                                  {p.images.length > 3 && <div className="absolute inset-0 bg-black/40 flex items-center justify-center"><span className="text-white text-[11px] font-bold">+{p.images.length - 3}</span></div>}
                                </div>
                              </div>
                            </div>
                          )
                        ) : (
                          <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100">
                            <Package size={28} className="text-blue-200" />
                          </div>
                        )}
                      </div>
                      <div className="p-3">
                        <h4 className="text-sm font-semibold text-slate-800 truncate group-hover:text-blue-700 transition-colors">{p.name}</h4>
                        {p.product_title && <p className="text-xs text-slate-500 truncate mt-0.5">{p.product_title}</p>}
                        <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                          {p.price && <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-50 text-green-600 border border-green-200 font-medium">{p.price_currency ? `${p.price_currency} ${p.price}` : p.price}</span>}
                          {p.product_type && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 border border-blue-200">{p.product_type}</span>}
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">{p.category}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                  {products.length === 0 && <p className="col-span-3 text-center text-slate-400 text-sm py-8">No products added yet.</p>}
                </div>
              </div>
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4"><div className="flex items-start gap-3"><Bot size={16} className="text-blue-600 mt-0.5" /><div><h4 className="text-sm font-medium text-blue-800">How this works</h4><p className="text-xs text-blue-600 mt-1 leading-relaxed">Products and FAQs are automatically used by the selected AI engine when responding to messages and scoring leads.</p></div></div></div>
              {showProductForm && (
                <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                  <div className="bg-white rounded-2xl w-full max-w-2xl p-6">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="text-lg font-bold text-slate-900">{editingProduct ? 'Edit Product' : 'Add Product'}</h3>
                      <button onClick={resetProductForm} className="text-slate-400"><X size={20} /></button>
                    </div>
                    <div className="space-y-3">
                      <input value={productForm.name} onChange={(e) => setProductForm({ ...productForm, name: e.target.value })} placeholder="Product Name *" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm" />
                      <input value={productForm.product_title || ''} onChange={(e) => setProductForm({ ...productForm, product_title: e.target.value })} placeholder="Product Code" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm" />
                      <input value={productForm.product_type || ''} onChange={(e) => setProductForm({ ...productForm, product_type: e.target.value })} placeholder="Type Variety (exact product type)" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm" />
                      <div className="relative group/descwrap">
                        <textarea
                          value={productForm.description}
                          onChange={(e) => setProductForm({ ...productForm, description: e.target.value })}
                          placeholder="Describe this product — or let AI write it for you."
                          rows={3}
                          className="w-full px-3 py-2.5 pb-10 bg-white border border-slate-200 rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-200"
                        />
                        {/* AI Generate button area — always visible */}
                        <div className="absolute bottom-2.5 right-2.5 group/aibtn">

                          {/* Hover tooltip — content changes based on locked state */}
                          <div className="absolute bottom-full right-0 mb-2.5 w-64 bg-slate-900 rounded-xl p-3.5 shadow-2xl opacity-0 group-hover/aibtn:opacity-100 pointer-events-none transition-all duration-200 translate-y-1 group-hover/aibtn:translate-y-0 z-20">
                            {!productForm.name ? (
                              <>
                                <p className="text-[11px] font-semibold text-amber-400 uppercase tracking-wide mb-1.5"> One thing missing</p>
                                <p className="text-xs text-slate-300 leading-relaxed mb-3">AI needs at least a product name to craft a great description. Fill in what you can — more detail = better copy.</p>
                                <div className="space-y-2">
                                  <div className="flex items-center gap-2">
                                    <span className="w-4 h-4 rounded-full bg-red-500/20 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" /></span>
                                    <span className="text-xs text-red-300 font-medium">Product Name <span className="text-red-500/70 text-[10px]">start here</span></span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <span className="w-4 h-4 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-slate-500" /></span>
                                    <span className="text-xs text-slate-400">Type / Variety <span className="text-slate-600 text-[10px]">optional but helpful</span></span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <span className="w-4 h-4 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-slate-500" /></span>
                                    <span className="text-xs text-slate-400">Images <span className="text-slate-600 text-[10px]">optional — AI uses if present</span></span>
                                  </div>
                                </div>
                              </>
                            ) : (
                              <>
                                <p className="text-[11px] font-semibold text-emerald-400 uppercase tracking-wide mb-1.5">* Ready to generate</p>
                                <p className="text-xs text-slate-300 leading-relaxed mb-3">AI will use all filled fields to write a ~100-word marketing description. Images are included if uploaded.</p>
                                <div className="space-y-2">
                                  <div className="flex items-center gap-2">
                                    <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                                    <span className="text-xs text-emerald-300">Product Name</span>
                                  </div>
                                  {productForm.product_type && <div className="flex items-center gap-2">
                                    <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                                    <span className="text-xs text-emerald-300">Type / Variety</span>
                                  </div>}
                                  <div className="flex items-center gap-2">
                                    <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                                    <span className="text-xs text-emerald-300">Category <span className="text-slate-500 text-[10px]">({productForm.category})</span></span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    {productForm.images?.length > 0
                                      ? <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                                      : <span className="w-4 h-4 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-slate-500" /></span>}
                                    <span className={`text-xs ${productForm.images?.length > 0 ? 'text-emerald-300' : 'text-slate-400'}`}>Images <span className="text-slate-600 text-[10px]">{productForm.images?.length > 0 ? `${productForm.images.length} uploaded` : 'none — text only'}</span></span>
                                  </div>
                                </div>
                              </>
                            )}
                            <div className="absolute -bottom-[5px] right-5 w-2.5 h-2.5 bg-slate-900 rotate-45 rounded-sm" />
                          </div>

                          {/* Nudge message shown when clicking while locked */}
                          {genDescNudge && (
                            <div className="absolute bottom-full right-0 mb-10 w-56 bg-amber-950/95 border border-amber-500/30 rounded-xl px-3.5 py-2.5 shadow-2xl z-30 pointer-events-none">
                              <p className="text-xs text-amber-300 font-medium leading-snug">Give me a name first! </p>
                              <p className="text-[11px] text-amber-400/70 mt-0.5">Fill in <span className="text-amber-300 font-semibold">Product Name</span> above to unlock AI writing.</p>
                              <div className="absolute -bottom-[5px] right-5 w-2.5 h-2.5 bg-amber-950 rotate-45 rounded-sm border-r border-b border-amber-500/30" />
                            </div>
                          )}

                          {/* The button — always clickable, style reflects locked/unlocked */}
                          <button
                            type="button"
                            onClick={generateProductDescription}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 ${
                              generatingDesc
                                ? 'bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow-md shadow-indigo-200 cursor-wait'
                                : productForm.name
                                  ? 'bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow-md shadow-indigo-200 hover:from-indigo-600 hover:to-violet-600 hover:shadow-lg hover:shadow-indigo-300 hover:-translate-y-px active:translate-y-0 cursor-pointer'
                                  : `bg-slate-800/80 text-slate-400 border border-slate-700/60 cursor-pointer hover:bg-slate-700/80 hover:text-slate-300 ${genDescNudge ? 'animate-bounce' : ''}`
                            }`}
                          >
                            {generatingDesc ? (
                              <><div className="w-3 h-3 border-[1.5px] border-white/30 border-t-white rounded-full animate-spin" /><span>Writing...</span></>
                            ) : productForm.name ? (
                              <><Wand2 size={11} /><span>Generate with AI</span></>
                            ) : (
                              <><Lock size={10} /><span>Generate with AI</span></>
                            )}
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        <div className="flex rounded-lg border border-slate-200 overflow-hidden bg-white">
                          <input
                            value={productForm.price}
                            onChange={(e) => setProductForm({ ...productForm, price: e.target.value })}
                            placeholder="Price"
                            className="w-[75%] px-3 py-2.5 text-sm bg-white focus:outline-none"
                          />
                          <div className="w-px bg-slate-200 flex-shrink-0" />
                          <select
                            value={productForm.price_currency}
                            onChange={(e) => setProductForm({ ...productForm, price_currency: e.target.value })}
                            className="w-[25%] px-1 py-2.5 text-xs bg-slate-50 text-slate-600 focus:outline-none appearance-none text-center cursor-pointer"
                          >
                            <option value="USD">USD</option>
                            <option value="EUR">EUR</option>
                            <option value="GBP">GBP</option>
                            <option value="INR">INR</option>
                            <option value="JPY">JPY</option>
                            <option value="CAD">CAD</option>
                            <option value="AUD">AUD</option>
                            <option value="AED">AED</option>
                            <option value="SGD">SGD</option>
                            <option value="CHF">CHF</option>
                          </select>
                        </div>
                        <select value={productForm.category} onChange={(e) => setProductForm({ ...productForm, category: e.target.value })} className="px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm">
                          <option value="general">General</option><option value="software">Software</option><option value="service">Service</option><option value="hardware">Hardware</option><option value="subscription">Subscription</option><option value="consulting">Consulting</option><option value="support">Support</option><option value="training">Training</option><option value="integration">Integration</option>
                        </select>
                      </div>

                      <div
                        onDragOver={(e) => { e.preventDefault(); setProductDragActive(true); }}
                        onDragLeave={() => setProductDragActive(false)}
                        onDrop={(e) => { e.preventDefault(); setProductDragActive(false); handleProductFiles(e.dataTransfer.files); }}
                        className={`border-2 border-dashed rounded-lg p-4 transition-colors ${productDragActive ? 'border-blue-400 bg-blue-50' : 'border-slate-300 bg-slate-50'}`}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-xs text-slate-500">Drag & drop up to {MAX_PRODUCT_IMAGES} images (JPG, PNG, WEBP, max {MAX_PRODUCT_IMAGE_SIZE_MB}MB each)</p>
                          <label className="px-3 py-1.5 bg-white border border-slate-200 rounded-md text-xs font-medium text-slate-600 cursor-pointer hover:bg-slate-100">
                            Choose Files
                            <input
                              type="file"
                              accept="image/jpeg,image/png,image/webp"
                              multiple
                              className="hidden"
                              onChange={(e) => handleProductFiles(e.target.files)}
                            />
                          </label>
                        </div>
                      </div>

                      {productImageError && <p className="text-xs text-red-500">{productImageError}</p>}

                      {(productForm.images || []).length > 0 && (
                        <div className="grid grid-cols-3 gap-3">
                          {productForm.images.map((img) => (
                            <div key={img.id} className="relative rounded-lg overflow-hidden border border-slate-200 bg-white">
                              <img src={resolveMediaUrl(img.dataUrl)} alt={img.name} className="w-full h-24 object-cover" />
                              <button onClick={() => removeProductImage(img.id)} className="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/60 text-white text-xs flex items-center justify-center">×</button>
                            </div>
                          ))}
                        </div>
                      )}

                      <button onClick={saveProduct} className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium">{editingProduct ? 'Save Changes' : 'Add Product'}</button>
                    </div>
                  </div>
                </div>
              )}
              {selectedProduct && (
                <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setSelectedProduct(null)}>
                  <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg max-h-[82vh] overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
                    {Array.isArray(selectedProduct.images) && selectedProduct.images.length > 0 && (
                      <div className="h-52 overflow-hidden rounded-t-2xl bg-slate-100 relative">
                        {selectedProduct.images.length === 1 ? (
                          <img
                            src={resolveMediaUrl(selectedProduct.images[0])} alt={selectedProduct.name}
                            className="w-full h-full object-cover cursor-zoom-in"
                            onClick={(e) => { e.stopPropagation(); openLightbox(selectedProduct.images, 0); }}
                          />
                        ) : selectedProduct.images.length === 2 ? (
                          <div className="grid grid-cols-2 h-full gap-0.5">
                            {selectedProduct.images.map((img, idx) => (
                              <img key={idx} src={resolveMediaUrl(img)} alt={selectedProduct.name} className="w-full h-full object-cover cursor-zoom-in"
                                onClick={(e) => { e.stopPropagation(); openLightbox(selectedProduct.images, idx); }} />
                            ))}
                          </div>
                        ) : (
                          <div className="grid grid-cols-2 h-full gap-0.5">
                            <img src={resolveMediaUrl(selectedProduct.images[0])} alt={selectedProduct.name} className="w-full h-full object-cover cursor-zoom-in"
                              onClick={(e) => { e.stopPropagation(); openLightbox(selectedProduct.images, 0); }} />
                            <div className="grid grid-rows-2 gap-0.5">
                              <img src={resolveMediaUrl(selectedProduct.images[1])} alt={selectedProduct.name} className="w-full h-full object-cover cursor-zoom-in"
                                onClick={(e) => { e.stopPropagation(); openLightbox(selectedProduct.images, 1); }} />
                              <div className="relative cursor-zoom-in" onClick={(e) => { e.stopPropagation(); openLightbox(selectedProduct.images, 2); }}>
                                <img src={resolveMediaUrl(selectedProduct.images[2])} alt={selectedProduct.name} className="w-full h-full object-cover" />
                                {selectedProduct.images.length > 3 && (
                                  <div className="absolute inset-0 bg-black/50 flex items-center justify-center rounded-br-2xl">
                                    <span className="text-white text-sm font-bold">+{selectedProduct.images.length - 3}</span>
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        )}
                        <span className="absolute bottom-2 right-2 text-[10px] bg-black/50 text-white rounded-full px-2 py-0.5">{selectedProduct.images.length} photo{selectedProduct.images.length > 1 ? 's' : ''} · tap to expand</span>
                      </div>
                    )}
                    <div className="p-6">
                      <div className="flex items-start justify-between mb-5">
                        <div className="min-w-0 pr-3">
                          <h3 className="text-xl font-bold text-slate-900 truncate">{selectedProduct.name}</h3>
                          {selectedProduct.product_title && <p className="text-sm text-slate-500 mt-0.5">{selectedProduct.product_title}</p>}
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <button
                            onClick={() => { setSelectedProduct(null); openEditProduct(selectedProduct); }}
                            className="px-2.5 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 rounded-lg text-xs font-medium hover:bg-slate-100 transition-colors inline-flex items-center gap-1"
                          >
                            <Edit size={12} /> Edit
                          </button>
                          <button onClick={() => setSelectedProduct(null)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
                        </div>
                      </div>
                      <div className="space-y-3">
                        {selectedProduct.description && (
                          <div className="bg-slate-50 rounded-xl p-3">
                            <p className="text-xs text-slate-400 font-medium mb-1">Description</p>
                            <p className="text-sm text-slate-700 leading-relaxed">{selectedProduct.description}</p>
                          </div>
                        )}
                        <div className="grid grid-cols-2 gap-3">
                          {selectedProduct.price && (
                            <div className="bg-green-50 border border-green-100 rounded-xl p-3">
                              <p className="text-[10px] text-green-500 font-medium mb-0.5">Price</p>
                              <p className="text-sm font-semibold text-green-700">{selectedProduct.price_currency ? `${selectedProduct.price_currency} ${selectedProduct.price}` : selectedProduct.price}</p>
                            </div>
                          )}
                          {selectedProduct.product_type && (
                            <div className="bg-blue-50 border border-blue-100 rounded-xl p-3">
                              <p className="text-[10px] text-blue-500 font-medium mb-0.5">Type</p>
                              <p className="text-sm font-semibold text-blue-700">{selectedProduct.product_type}</p>
                            </div>
                          )}
                          <div className="bg-slate-50 border border-slate-100 rounded-xl p-3">
                            <p className="text-[10px] text-slate-400 font-medium mb-0.5">Category</p>
                            <p className="text-sm font-semibold text-slate-700 capitalize">{selectedProduct.category}</p>
                          </div>
                          {Array.isArray(selectedProduct.features) && selectedProduct.features.length > 0 && (
                            <div className="bg-slate-50 border border-slate-100 rounded-xl p-3">
                              <p className="text-[10px] text-slate-400 font-medium mb-1">Features</p>
                              <ul className="space-y-0.5">{selectedProduct.features.slice(0, 4).map((f, i) => <li key={i} className="text-xs text-slate-600 flex items-start gap-1"><span className="text-blue-400 mt-0.5">•</span>{f}</li>)}</ul>
                            </div>
                          )}
                        </div>
                        <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              requestConfirmation({
                                title: 'Delete Product',
                                description: `Delete "${selectedProduct.name}". This removes it from the catalog and cannot be undone.`,
                                confirmLabel: 'Delete product',
                                onConfirm: async () => {
                                  await deleteProduct(selectedProduct.id);
                                },
                              });
                            }}
                            className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1"
                          >
                            <Trash2 size={13} /> Delete product
                          </button>
                          <button onClick={() => setSelectedProduct(null)} className="text-xs text-slate-400 hover:text-slate-600">Close</button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* === LIGHTBOX === */}
              {lightboxImages.length > 0 && (
                <div className="fixed inset-0 z-[60] bg-black/90 flex items-center justify-center" onClick={closeLightbox}>
                  <button onClick={(e) => { e.stopPropagation(); setLightboxIndex(i => (i - 1 + lightboxImages.length) % lightboxImages.length); }}
                    className="absolute left-3 sm:left-6 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10">
                    <ChevronLeft size={22} />
                  </button>
                  <img
                    src={resolveMediaUrl(lightboxImages[lightboxIndex])}
                    alt={`Attachment ${lightboxIndex + 1}`}
                    className="max-h-[88vh] max-w-[90vw] object-contain rounded-lg shadow-2xl"
                    onClick={(e) => e.stopPropagation()}
                  />
                  <button onClick={(e) => { e.stopPropagation(); setLightboxIndex(i => (i + 1) % lightboxImages.length); }}
                    className="absolute right-3 sm:right-6 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10">
                    <ChevronRight size={22} />
                  </button>
                  <button onClick={closeLightbox} className="absolute top-4 right-4 w-9 h-9 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center">
                    <X size={18} />
                  </button>
                  <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex gap-1.5">
                    {lightboxImages.map((_, idx) => (
                      <button key={idx} onClick={(e) => { e.stopPropagation(); setLightboxIndex(idx); }}
                        className={`w-1.5 h-1.5 rounded-full transition-colors ${idx === lightboxIndex ? 'bg-white' : 'bg-white/40'}`} />
                    ))}
                  </div>
                  <span className="absolute top-4 left-1/2 -translate-x-1/2 text-white/70 text-xs">{lightboxIndex + 1} / {lightboxImages.length}</span>
                </div>
              )}

            </div>
          )}

        </div>
      </div>

      {deleteAccountModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-lg p-6 shadow-xl">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center">
                  <Trash2 size={18} className="text-red-600" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-slate-900">Delete Account</h3>
                  <p className="text-xs text-slate-400">This action is permanent and cannot be undone.</p>
                </div>
              </div>
              <button onClick={closeDeleteAccountModal} className="text-slate-400 p-1" disabled={deleteAccountSending || deleteAccountSubmitting}>
                <X size={20} />
              </button>
            </div>

            <div className="space-y-4">
              <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3">
                <p className="text-sm font-medium text-red-700">Deleting this account removes your login access immediately after verification.</p>
                <p className="text-xs text-red-600 mt-1">Use your current password or request a unique fingerprint by email.</p>
              </div>

              <div>
                <label className="text-xs text-slate-500 font-medium mb-2 block">Verification method</label>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    disabled={user?.auth_provider !== 'email'}
                    onClick={() => { setDeleteAccountMethod('password'); setDeleteAccountError(''); setDeleteAccountInfo(''); }}
                    className={`rounded-xl border px-4 py-3 text-left transition-colors ${deleteAccountMethod === 'password' ? 'border-blue-300 bg-blue-50' : 'border-slate-200 bg-slate-50'} ${user?.auth_provider !== 'email' ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-slate-900"><Lock size={14} /> Password</div>
                    <p className="text-xs text-slate-500 mt-1">Confirm with your current password.</p>
                  </button>
                  <button
                    type="button"
                    onClick={() => { setDeleteAccountMethod('email'); setDeleteAccountError(''); setDeleteAccountInfo(''); }}
                    className={`rounded-xl border px-4 py-3 text-left transition-colors ${deleteAccountMethod === 'email' ? 'border-blue-300 bg-blue-50' : 'border-slate-200 bg-slate-50'}`}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-slate-900"><Mail size={14} /> Email fingerprint</div>
                    <p className="text-xs text-slate-500 mt-1">Send a one-time fingerprint to {personalSettings?.email || user?.email}.</p>
                  </button>
                </div>
              </div>

              {deleteAccountMethod === 'password' ? (
                <div>
                  <label className="text-xs text-slate-500 font-medium mb-1.5 block">Current Password</label>
                  <input
                    type="password"
                    value={deleteAccountPassword}
                    onChange={(e) => setDeleteAccountPassword(e.target.value)}
                    placeholder="Enter your current password"
                    className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm"
                  />
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex gap-3">
                    <button onClick={sendDeleteVerificationFingerprint} disabled={deleteAccountSending} className="px-4 py-2.5 bg-slate-900 text-white rounded-xl text-sm font-medium disabled:opacity-50">
                      {deleteAccountSending ? 'Sending...' : 'Send Fingerprint'}
                    </button>
                    <div className="flex-1">
                      <label className="text-xs text-slate-500 font-medium mb-1.5 block">Verification Fingerprint</label>
                      <input
                        value={deleteAccountCode}
                        onChange={(e) => setDeleteAccountCode(e.target.value.toUpperCase())}
                        placeholder="Enter the fingerprint from your email"
                        className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm uppercase tracking-[0.2em]"
                      />
                    </div>
                  </div>
                </div>
              )}

              <div>
                <label className="text-xs text-slate-500 font-medium mb-1.5 block">Type DELETE to confirm</label>
                <input
                  value={deleteAccountConfirmText}
                  onChange={(e) => setDeleteAccountConfirmText(e.target.value)}
                  placeholder="DELETE"
                  className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm"
                />
              </div>

              {deleteAccountError && <div className="flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-200 rounded-lg"><AlertCircle size={14} className="text-red-500" /><span className="text-xs text-red-600">{deleteAccountError}</span></div>}
              {deleteAccountInfo && <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-200 rounded-lg"><CheckCircle size={14} className="text-blue-500" /><span className="text-xs text-blue-700">{deleteAccountInfo}</span></div>}

              <div className="flex gap-3 pt-1">
                <button onClick={closeDeleteAccountModal} disabled={deleteAccountSending || deleteAccountSubmitting} className="flex-1 py-2.5 text-sm font-medium text-slate-600 bg-slate-100 rounded-xl hover:bg-slate-200 disabled:opacity-50">Cancel</button>
                <button onClick={handleDeleteAccount} disabled={deleteAccountSubmitting || deleteAccountSending} className="flex-1 py-2.5 text-sm font-medium text-white bg-red-600 rounded-xl hover:bg-red-500 disabled:opacity-50 flex items-center justify-center gap-2">
                  {deleteAccountSubmitting ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Trash2 size={14} />}
                  Delete Permanently
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* === PASSWORD MODAL === */}
      {passwordModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-md p-6 shadow-xl">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-3"><div className="w-10 h-10 rounded-full bg-blue-50 flex items-center justify-center"><Lock size={18} className="text-blue-600" /></div><div><h3 className="text-lg font-bold text-slate-900">Change Password</h3><p className="text-xs text-slate-400">{passwordModal.name} ({passwordModal.role})</p></div></div>
              <button onClick={closePasswordModal} className="text-slate-400 p-1"><X size={20} /></button>
            </div>
            <div className="space-y-4">
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">New Password</label><div className="relative"><input type={showPassword ? 'text' : 'password'} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="Enter new password" className="w-full px-4 py-2.5 pr-10 bg-slate-50 border border-slate-200 rounded-xl text-sm" /><button onClick={() => setShowPassword(!showPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" type="button">{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></div>
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Confirm Password</label><input type={showPassword ? 'text' : 'password'} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Confirm new password" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              {newPassword.length > 0 && (<div className="bg-slate-50 rounded-xl p-3.5 space-y-1.5"><p className="text-[11px] font-semibold text-slate-500 mb-2">Password Requirements</p>{[{ label: 'At least 8 characters', met: newPassword.length >= 8 },{ label: 'Contains a letter', met: /[a-zA-Z]/.test(newPassword) },{ label: 'Contains a number', met: /[0-9]/.test(newPassword) },{ label: 'Contains a special character', met: PASSWORD_SPECIAL_CHAR_RE.test(newPassword) },{ label: 'Passwords match', met: confirmPassword.length > 0 && newPassword === confirmPassword },].map((r, i) => (<div key={i} className="flex items-center gap-2">{r.met ? <CheckCircle size={13} className="text-emerald-500" /> : <AlertCircle size={13} className="text-slate-300" />}<span className={`text-[11px] ${r.met ? 'text-emerald-600 font-medium' : 'text-slate-400'}`}>{r.label}</span></div>))}</div>)}
              {passwordError && <div className="flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-200 rounded-lg"><AlertCircle size={14} className="text-red-500" /><span className="text-xs text-red-600">{passwordError}</span></div>}
              {passwordSuccess && <div className="flex items-center gap-2 px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg"><CheckCircle size={14} className="text-emerald-500" /><span className="text-xs text-emerald-600">{passwordSuccess}</span></div>}
              <div className="flex gap-3 pt-1"><button onClick={closePasswordModal} className="flex-1 py-2.5 text-sm font-medium text-slate-600 bg-slate-100 rounded-xl hover:bg-slate-200">Cancel</button><button onClick={handlePasswordChange} disabled={passwordSaving || !passwordValidation.isValid || newPassword !== confirmPassword} className="flex-1 py-2.5 text-sm font-medium text-white bg-blue-600 rounded-xl disabled:opacity-50 flex items-center justify-center gap-2">{passwordSaving ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Lock size={14} />} Update Password</button></div>
            </div>
          </div>
        </div>
      )}

      {/* === EDIT USER MODAL === */}
      {showCreateUserModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-md p-6 shadow-xl">
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-bold text-slate-900">Add Team User</h3>
              <button onClick={() => setShowCreateUserModal(false)} className="text-slate-400"><X size={20} /></button>
            </div>
            <div className="space-y-4">
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Name</label><input value={createUserForm.name} onChange={(e) => setCreateUserForm({ ...createUserForm, name: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Email</label><input value={createUserForm.email} onChange={(e) => setCreateUserForm({ ...createUserForm, email: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              <div>
                <label className="text-xs text-slate-500 font-medium mb-1.5 block">Role</label>
                <select value={createUserForm.role} onChange={(e) => setCreateUserForm({ ...createUserForm, role: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm">
                  {availableCreateRoles.map((role) => (
                    <option key={role} value={role}>{ROLE_LABELS[role] || (role.charAt(0).toUpperCase() + role.slice(1))}</option>
                  ))}
                </select>
              </div>
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Sub Role</label><input value={createUserForm.sub_role} onChange={(e) => setCreateUserForm({ ...createUserForm, sub_role: e.target.value })} placeholder="e.g., Sales Lead, QA Specialist" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Status</label><select value={createUserForm.status} onChange={(e) => setCreateUserForm({ ...createUserForm, status: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm"><option value="active">Active</option><option value="inactive">Inactive</option></select></div>
              <p className="text-[11px] text-slate-500">A secure invitation email will be sent to this user. They will set their password when accepting the invite.</p>
              <div className="flex gap-3 pt-1"><button onClick={() => setShowCreateUserModal(false)} className="flex-1 py-2.5 text-sm font-medium text-slate-600 bg-slate-100 rounded-xl">Cancel</button><button onClick={createUser} className="flex-1 py-2.5 text-sm font-medium text-white bg-blue-600 rounded-xl">Send Invite</button></div>
            </div>
          </div>
        </div>
      )}

      {editUserModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-md p-6 shadow-xl">
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-bold text-slate-900">Edit User</h3>
              <button onClick={() => setEditUserModal(null)} className="text-slate-400"><X size={20} /></button>
            </div>
            <div className="space-y-4">
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Name</label><input value={editUserForm.name || ''} onChange={(e) => setEditUserForm({...editUserForm, name: e.target.value})} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Email</label><input value={editUserForm.email || ''} onChange={(e) => setEditUserForm({...editUserForm, email: e.target.value})} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              {isAdmin && (
                <div>
                  <label className="text-xs text-slate-500 font-medium mb-1.5 block">Role</label>
                  <select value={editUserForm.role || ''} onChange={(e) => setEditUserForm({...editUserForm, role: e.target.value})} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm">
                    {getAvailableEditRoles(editUserModal).map((role) => (
                      <option key={role} value={role}>{ROLE_LABELS[role] || (role.charAt(0).toUpperCase() + role.slice(1))}</option>
                    ))}
                  </select>
                </div>
              )}
              {isAdmin && (
                <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Sub Role</label><input value={editUserForm.sub_role || ''} onChange={(e) => setEditUserForm({...editUserForm, sub_role: e.target.value})} placeholder="Custom sub role" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" /></div>
              )}
              <div><label className="text-xs text-slate-500 font-medium mb-1.5 block">Status</label><select value={editUserForm.status || ''} onChange={(e) => setEditUserForm({...editUserForm, status: e.target.value})} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm"><option value="active">Active</option><option value="inactive">Inactive</option></select></div>
              <div className="flex gap-3 pt-1"><button onClick={() => setEditUserModal(null)} className="flex-1 py-2.5 text-sm font-medium text-slate-600 bg-slate-100 rounded-xl">Cancel</button><button onClick={saveUserEdit} className="flex-1 py-2.5 text-sm font-medium text-white bg-blue-600 rounded-xl">Save Changes</button></div>
            </div>
          </div>
        </div>
      )}
    </div>
    {confirmDialog}
    </>
  );
}
