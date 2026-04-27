import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api, { refreshAuthSession } from '@/lib/api';
import { postAuthDestination } from '@/lib/auth-gates';
import { normalizeAvatarUrl, displayNameInitial } from '@/lib/avatar';
import {
  PHONE_COUNTRIES,
  buildInternationalPhoneNumber,
  getDefaultPhoneRegion,
} from '@/lib/phoneCountries';
import PlatformLogo from '@/components/PlatformLogo';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Building2,
  Globe,
  Phone,
  Briefcase,
  CheckCircle,
  AlertCircle,
  ArrowRight,
  ArrowLeft,
  MessageSquare,
  UserPlus,
} from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
const ONBOARDING_DRAFT_KEY = 'pe_onboarding_draft';

const INDUSTRIES = [
  'Technology', 'E-commerce', 'Finance & Banking', 'Healthcare',
  'Education', 'Real Estate', 'Marketing & Advertising', 'Logistics',
  'Hospitality', 'Retail', 'Manufacturing', 'Consulting', 'Other',
];

const inputStyle = {
  width: '100%',
  padding: '10px 14px 10px 38px',
  fontSize: 14,
  border: '1.5px solid #e2e8f0',
  borderRadius: 10,
  outline: 'none',
  background: '#fff',
  color: '#0f172a',
  transition: 'all 0.2s',
  boxSizing: 'border-box',
  fontFamily: F,
};
const inputStyleNoIcon = { ...inputStyle, paddingLeft: 14 };
const secondaryButtonStyle = {
  flex: 1,
  padding: '11px 14px',
  borderRadius: 10,
  border: '1.5px solid #d7e0ea',
  background: '#fff',
  color: '#475569',
  fontSize: 14,
  fontWeight: 600,
  cursor: 'pointer',
  fontFamily: F,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
};
const primaryButtonStyle = {
  flex: 2,
  padding: '11px 14px',
  borderRadius: 10,
  border: 'none',
  color: '#fff',
  fontSize: 14,
  fontWeight: 600,
  cursor: 'pointer',
  fontFamily: F,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
};

const focusIn = (event) => {
  event.target.style.borderColor = '#2563eb';
  event.target.style.boxShadow = '0 0 0 3px rgba(37,99,235,0.1)';
};

const focusOut = (event) => {
  event.target.style.borderColor = '#e2e8f0';
  event.target.style.boxShadow = 'none';
};

function createDefaultCompanyForm() {
  return {
    company_name: '',
    industry: '',
    website_address: '',
    phone_country: getDefaultPhoneRegion(),
    phone_local: '',
    description: '',
    tagline: '',
  };
}

function normalizeCompanyForm(raw = {}) {
  const fallback = createDefaultCompanyForm();
  const next = {
    ...fallback,
    ...raw,
  };
  const regionCode = String(next.phone_country || fallback.phone_country).toUpperCase();
  next.phone_country = PHONE_COUNTRIES.some((item) => item.code === regionCode)
    ? regionCode
    : fallback.phone_country;
  next.phone_local = String(next.phone_local || '');
  next.description = String(next.description || '');
  next.tagline = String(next.tagline || '');
  next.company_name = String(next.company_name || '');
  next.website_address = String(next.website_address || '');
  next.industry = String(next.industry || '');
  return next;
}

function readOnboardingDraft() {
  if (typeof window === 'undefined') return null;
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(ONBOARDING_DRAFT_KEY) || 'null');
    if (!parsed || typeof parsed !== 'object') return null;
    const step = Number(parsed.step || 1);
    return {
      step: step >= 1 && step <= 3 ? step : 1,
      channels: Array.isArray(parsed.channels) ? parsed.channels : [],
      companyForm: normalizeCompanyForm(parsed.companyForm),
    };
  } catch {
    return null;
  }
}

function clearOnboardingDraft() {
  if (typeof window !== 'undefined') {
    window.sessionStorage.removeItem(ONBOARDING_DRAFT_KEY);
  }
}

function IconInput({ icon: Icon, type = 'text', value, onChange, placeholder, required, right, ...rest }) {
  return (
    <div style={{ position: 'relative' }}>
      <Icon
        size={15}
        color="#94a3b8"
        style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}
      />
      <input
        type={type}
        value={value ?? ''}
        onChange={onChange}
        placeholder={placeholder}
        style={{ ...inputStyle, paddingRight: right ? 40 : 14 }}
        onFocus={focusIn}
        onBlur={focusOut}
        required={required}
        {...rest}
      />
      {right}
    </div>
  );
}

function Label({ children }) {
  return (
    <label
      style={{
        fontSize: 12,
        fontWeight: 600,
        color: '#475569',
        display: 'block',
        marginBottom: 6,
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
      }}
    >
      {children}
    </label>
  );
}

function ProgressBar({ step }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 36 }}>
      {[1, 2, 3].map((item) => (
        <div key={item} style={{ display: 'flex', alignItems: 'center', flex: item < 3 ? 1 : 0 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              fontSize: 13,
              flexShrink: 0,
              background: step >= item ? 'linear-gradient(135deg,#2563eb,#6366f1)' : '#f1f5f9',
              color: step >= item ? '#fff' : '#94a3b8',
              boxShadow: step >= item ? '0 4px 12px rgba(37,99,235,0.3)' : 'none',
              transition: 'all 0.3s',
            }}
          >
            {step > item ? <CheckCircle size={16} /> : item}
          </div>
          {item < 3 && (
            <div
              style={{
                flex: 1,
                height: 3,
                background: step > item ? 'linear-gradient(90deg,#2563eb,#6366f1)' : '#d7e0ea',
                margin: '0 8px',
                borderRadius: 4,
                transition: 'background 0.3s',
              }}
            />
          )}
        </div>
      ))}
    </div>
  );
}

function CompanyStep({ user, form, onChange, onNext, onSkip }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const phonePreview = buildInternationalPhoneNumber(form.phone_country, form.phone_local);

  // #region agent log
  useEffect(() => {
    fetch('http://127.0.0.1:7649/ingest/e979188b-c6e0-4acc-966c-d0f2bc7abd6b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'3ceefc'},body:JSON.stringify({sessionId:'3ceefc',runId:'pre-fix',hypothesisId:'H1',location:'OnboardingPage.js:CompanyStep',message:'CompanyStep render phone state',data:{phone_country:String(form?.phone_country||''),phone_local:String(form?.phone_local??''),phone_local_len:String(form?.phone_local??'').length,phonePreview:String(phonePreview||'')},timestamp:Date.now()})}).catch(()=>{});
  }, [form?.phone_country, form?.phone_local, phonePreview]);
  // #endregion agent log

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    if (!form.company_name.trim()) {
      setError('Company name is required');
      return;
    }
    if (!form.industry.trim()) {
      setError('Business type / industry is required');
      return;
    }
    setSaving(true);
    try {
      await api.put('/settings/company', {
        company_name: form.company_name.trim(),
        industry: form.industry,
        website_address: form.website_address.trim(),
        phone: phonePreview,
        default_phone_region: form.phone_country,
        description: form.description.trim(),
        tagline: form.tagline.trim(),
        support_email: user?.email || '',
      });
      onNext();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save company details');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 16,
            background: 'linear-gradient(135deg,#eff6ff,#dbeafe)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 14px',
          }}
        >
          <Building2 size={26} color="#2563eb" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Set up your company</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>
          Tell us about your business so we can personalise your experience
        </p>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4 border-red-200 bg-red-50 text-red-700">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
        <div style={{ gridColumn: '1/-1' }}>
          <Label>Company name *</Label>
          <IconInput
            icon={Building2}
            value={form.company_name}
            onChange={(event) => onChange('company_name', event.target.value)}
            placeholder="Acme Corp"
            required
          />
        </div>

        <div>
          <Label>Industry</Label>
          <div style={{ position: 'relative' }}>
            <Briefcase
              size={15}
              color="#94a3b8"
              style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}
            />
            <select
              value={form.industry}
              onChange={(event) => onChange('industry', event.target.value)}
              style={{ ...inputStyle, appearance: 'none', cursor: 'pointer' }}
              onFocus={focusIn}
              onBlur={focusOut}
            >
              <option value="">Select industry...</option>
              {INDUSTRIES.map((industry) => (
                <option key={industry} value={industry}>
                  {industry}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <Label>Phone</Label>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 190px) 1fr', gap: 10 }}>
            <select
              value={form.phone_country}
              onChange={(event) => onChange('phone_country', event.target.value)}
              style={{ ...inputStyleNoIcon, appearance: 'none', cursor: 'pointer' }}
              onFocus={focusIn}
              onBlur={focusOut}
            >
              {PHONE_COUNTRIES.map((country) => (
                <option key={country.code} value={country.code}>
                  {country.label}
                </option>
              ))}
            </select>
            <IconInput
              icon={Phone}
              value={form.phone_local}
              onChange={(event) => {
                const nextValue = event.target.value;
                // #region agent log
                fetch('http://127.0.0.1:7649/ingest/e979188b-c6e0-4acc-966c-d0f2bc7abd6b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'3ceefc'},body:JSON.stringify({sessionId:'3ceefc',runId:'pre-fix',hypothesisId:'H2',location:'OnboardingPage.js:phone_local_onChange',message:'phone_local changed',data:{nextValue:String(nextValue||''),len:String(nextValue||'').length},timestamp:Date.now()})}).catch(()=>{});
                // #endregion agent log
                onChange('phone_local', nextValue);
              }}
              placeholder="555 000 0000"
            />
          </div>
          <p style={{ margin: '8px 2px 0', fontSize: 11.5, color: '#64748b' }}>
            {phonePreview ? `Saved as ${phonePreview}` : 'Pick a country code, then enter the local phone number.'}
          </p>
        </div>

        <div style={{ gridColumn: '1/-1' }}>
          <Label>Website</Label>
          <IconInput
            icon={Globe}
            value={form.website_address}
            onChange={(event) => onChange('website_address', event.target.value)}
            placeholder="https://yourcompany.com"
          />
        </div>

        <div style={{ gridColumn: '1/-1' }}>
          <Label>Tagline</Label>
          <input
            type="text"
            value={form.tagline}
            onChange={(event) => onChange('tagline', event.target.value)}
            placeholder="A short, catchy company tagline"
            style={inputStyleNoIcon}
            onFocus={focusIn}
            onBlur={focusOut}
          />
        </div>

        <div style={{ gridColumn: '1/-1' }}>
          <Label>Description</Label>
          <textarea
            value={form.description}
            onChange={(event) => onChange('description', event.target.value)}
            placeholder="What does your company do?"
            rows={4}
            maxLength={5000}
            style={{ ...inputStyleNoIcon, resize: 'vertical', minHeight: 96, lineHeight: 1.6 }}
            onFocus={focusIn}
            onBlur={focusOut}
          />
          <div style={{ marginTop: 6, textAlign: 'right', fontSize: 11, color: '#94a3b8' }}>
            {(form.description || '').length} / 5000
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
        <button type="button" onClick={onSkip} style={secondaryButtonStyle}>
          Skip for now
        </button>
        <button
          type="submit"
          disabled={saving}
          style={{
            ...primaryButtonStyle,
            background: saving ? '#94a3b8' : 'linear-gradient(135deg,#2563eb,#6366f1)',
            cursor: saving ? 'wait' : 'pointer',
            boxShadow: saving ? 'none' : '0 4px 14px rgba(37,99,235,0.35)',
          }}
        >
          {saving
            ? <div style={{ width: 16, height: 16, border: '2px solid rgba(255,255,255,0.5)', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
            : <><span>Continue</span><ArrowRight size={16} /></>}
        </button>
      </div>
    </form>
  );
}

const CHANNEL_OPTIONS = [
  { id: 'whatsapp', label: 'WhatsApp' },
  { id: 'email', label: 'Email' },
  { id: 'facebook', label: 'Facebook' },
  { id: 'instagram', label: 'Instagram' },
  { id: 'web_chat', label: 'Website chat' },
];

function ChannelsStep({ selected, onToggle, onNext, onBack }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleNext = async () => {
    setError('');
    if (!selected.length) {
      setError('Pick at least one channel');
      return;
    }
    setSaving(true);
    try {
      await api.patch('/auth/onboarding/profile', { preferred_channels: selected });
      onNext();
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not save channels');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 16,
            background: 'linear-gradient(135deg,#eff6ff,#dbeafe)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 14px',
          }}
        >
          <MessageSquare size={26} color="#2563eb" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Preferred channels</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>Choose where you want to engage customers first.</p>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4 border-red-200 bg-red-50 text-red-700">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div style={{ display: 'grid', gap: 10 }}>
        {CHANNEL_OPTIONS.map((channel) => {
          const selectedState = selected.includes(channel.id);
          return (
            <button
              key={channel.id}
              type="button"
              onClick={() => onToggle(channel.id)}
              style={{
                textAlign: 'left',
                padding: '14px 16px',
                borderRadius: 12,
                border: selectedState ? '2px solid #2563eb' : '1.5px solid #e2e8f0',
                background: selectedState ? 'rgba(37,99,235,0.06)' : '#fff',
                cursor: 'pointer',
                fontFamily: F,
                fontSize: 15,
                fontWeight: 600,
                color: '#0f172a',
              }}
            >
              {channel.label}
            </button>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
        <button type="button" onClick={onBack} style={secondaryButtonStyle}>
          <ArrowLeft size={15} /> Back
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={handleNext}
          style={{
            ...primaryButtonStyle,
            background: saving ? '#94a3b8' : 'linear-gradient(135deg,#2563eb,#6366f1)',
            cursor: saving ? 'wait' : 'pointer',
          }}
        >
          {saving
            ? <div style={{ width: 16, height: 16, border: '2px solid rgba(255,255,255,0.5)', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
            : <><span>Continue</span><ArrowRight size={16} /></>}
        </button>
      </div>
    </div>
  );
}

function ConfirmStep({ onBack, onFinish, busy }) {
  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 16,
            background: 'linear-gradient(135deg,#f0fdf4,#dcfce7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 14px',
          }}
        >
          <CheckCircle size={28} color="#16a34a" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Confirm setup</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>
          You can change channels and company details later in Settings.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
        <button type="button" onClick={onBack} style={secondaryButtonStyle}>
          <ArrowLeft size={15} style={{ verticalAlign: 'middle', marginRight: 6 }} /> Back
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onFinish}
          style={{
            ...primaryButtonStyle,
            background: busy ? '#94a3b8' : 'linear-gradient(135deg,#059669,#10b981)',
            cursor: busy ? 'wait' : 'pointer',
          }}
        >
          {busy ? 'Saving...' : 'Complete setup'}
        </button>
      </div>
    </div>
  );
}

function EnterpriseInviteStep({ onDone }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setErr('');
    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail) {
      setErr('Email is required');
      return;
    }
    setBusy(true);
    try {
      await api.post('/auth/onboarding/invite', {
        email: normalizedEmail,
        name: name.trim() || undefined,
        role: 'company_agent',
      });
      const refreshed = await refreshAuthSession();
      if (refreshed?.token && refreshed?.user) {
        onDone(refreshed.user, refreshed.token);
      }
    } catch (error) {
      setErr(error.response?.data?.detail || error.response?.data?.error || 'Could not send invitation');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 16,
            background: 'linear-gradient(135deg,#eff6ff,#dbeafe)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 14px',
          }}
        >
          <UserPlus size={26} color="#2563eb" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Invite a teammate</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>
          Enterprise workspaces need at least one invitation sent before you continue. You can invite more people later from Settings.
        </p>
      </div>

      {err && (
        <Alert variant="destructive" className="mb-4 border-red-200 bg-red-50 text-red-700">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}

      <div style={{ marginBottom: 14 }}>
        <Label>Teammate email</Label>
        <input
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="colleague@company.com"
          style={{ ...inputStyleNoIcon, marginTop: 6 }}
          onFocus={focusIn}
          onBlur={focusOut}
          required
        />
      </div>

      <div style={{ marginBottom: 22 }}>
        <Label>Name (optional)</Label>
        <input
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="How they should appear in the invite"
          style={{ ...inputStyleNoIcon, marginTop: 6 }}
          onFocus={focusIn}
          onBlur={focusOut}
        />
      </div>

      <button
        type="submit"
        disabled={busy}
        style={{
          width: '100%',
          padding: '12px 0',
          borderRadius: 10,
          border: 'none',
          background: busy ? '#94a3b8' : 'linear-gradient(135deg,#2563eb,#6366f1)',
          color: '#fff',
          fontSize: 15,
          fontWeight: 600,
          cursor: busy ? 'wait' : 'pointer',
          fontFamily: F,
        }}
      >
        {busy ? 'Sending...' : 'Send invitation'}
      </button>
    </form>
  );
}

export default function OnboardingPage() {
  const { user, setOnboardingComplete, setAuthFromOAuth } = useAuth();
  const navigate = useNavigate();
  const [draftState] = useState(() => readOnboardingDraft());
  const [step, setStep] = useState(draftState?.step || 1);
  const [completing, setCompleting] = useState(false);
  const [channels, setChannels] = useState(draftState?.channels || []);
  const [companyForm, setCompanyForm] = useState(draftState?.companyForm || createDefaultCompanyForm());
  const [headerAvatar, setHeaderAvatar] = useState(() => normalizeAvatarUrl(localStorage.getItem('pe_avatar') || ''));

  useEffect(() => {
    if (!user) {
      navigate('/signin', { replace: true });
      return;
    }
    if (user.role === 'super_admin') {
      clearOnboardingDraft();
      navigate('/super-admin', { replace: true });
      return;
    }
    if (user.email_verified === false) {
      const query = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
      navigate(`/verify-email${query}`, { replace: true });
      return;
    }
    if (user.enterprise_invite_gate_pending) {
      clearOnboardingDraft();
      setStep(4);
      return;
    }
    if (user.onboarding_completed === true && user.plan_selected === false) {
      clearOnboardingDraft();
      navigate('/billing', { replace: true });
      return;
    }
    if (user.onboarding_completed === true && user.plan_selected !== false) {
      clearOnboardingDraft();
      navigate('/dashboard', { replace: true });
    }
  }, [navigate, user]);

  useEffect(() => {
    setHeaderAvatar(normalizeAvatarUrl(user?.avatar || localStorage.getItem('pe_avatar') || ''));
  }, [user?.avatar]);

  useEffect(() => {
    if (typeof window === 'undefined' || step === 4) return;
    window.sessionStorage.setItem(
      ONBOARDING_DRAFT_KEY,
      JSON.stringify({
        step,
        channels,
        companyForm,
      }),
    );
  }, [channels, companyForm, step]);

  const toggleChannel = (id) => {
    setChannels((prev) => (prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]));
  };

  const markComplete = async () => {
    setCompleting(true);
    try {
      const response = await api.put('/auth/onboarding/complete');
      clearOnboardingDraft();
      const refreshedUser = response.data?.user || user;
      if (response.data?.token && response.data?.user) {
        setAuthFromOAuth(response.data.user, response.data.token);
      } else {
        setOnboardingComplete();
      }
      if ((refreshedUser?.role || user?.role) === 'admin') {
        navigate('/settings?tab=users&invite=1&onboarding=1', { replace: true });
      } else {
        const destination = postAuthDestination({
          ...(refreshedUser || {}),
          onboarding_completed: true,
        });
        navigate(destination, { replace: true });
      }
    } catch {
      // Keep the user on the confirm step so they can retry.
    } finally {
      setCompleting(false);
    }
  };

  if (completing) {
    return (
      <div
        style={{
          minHeight: '100vh',
          background: 'linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%)',
          fontFamily: F,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div style={{ textAlign: 'center' }}>
          <div style={{ margin: '0 auto 20px', display: 'flex', justifyContent: 'center' }}>
            <PlatformLogo to={null} />
          </div>
          <div
            style={{
              width: 32,
              height: 32,
              border: '3px solid #d7e0ea',
              borderTopColor: '#2563eb',
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
              margin: '0 auto 16px',
            }}
          />
          <p style={{ fontSize: 16, color: '#64748b' }}>Setting up your workspace...</p>
        </div>
        <style>{'@keyframes spin{to{transform:rotate(360deg)}}'}</style>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(180deg,#eef2f7 0%,#e7edf5 100%)', fontFamily: F }}>
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div
          style={{
            position: 'absolute',
            top: -150,
            right: -80,
            width: 480,
            height: 480,
            background: 'radial-gradient(circle,rgba(99,102,241,0.12) 0%,transparent 70%)',
            borderRadius: '50%',
          }}
        />
        <div
          style={{
            position: 'absolute',
            bottom: -80,
            left: -40,
            width: 360,
            height: 360,
            background: 'radial-gradient(circle,rgba(37,99,235,0.1) 0%,transparent 70%)',
            borderRadius: '50%',
          }}
        />
      </div>

      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 50,
          background: 'rgba(255,255,255,0.94)',
          backdropFilter: 'blur(14px)',
          borderBottom: '1px solid #dbe4ee',
          boxShadow: '0 1px 12px rgba(15,23,42,0.05)',
        }}
      >
        <div
          style={{
            maxWidth: 1200,
            margin: '0 auto',
            padding: '0 28px',
            height: 62,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <PlatformLogo />

          {user && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {headerAvatar ? (
                <img
                  src={headerAvatar}
                  alt={user.name}
                  referrerPolicy="no-referrer"
                  style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', border: '2px solid #e2e8f0' }}
                  onError={() => setHeaderAvatar('')}
                />
              ) : (
                <div
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: '50%',
                    background: 'linear-gradient(135deg,#2563eb,#6366f1)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#fff',
                    fontWeight: 700,
                    fontSize: 13,
                  }}
                >
                  {displayNameInitial(user.name || user.email)}
                </div>
              )}
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#0f172a', lineHeight: 1.2 }}>
                  {user.name || user.email}
                </div>
                <div style={{ fontSize: 11, color: '#94a3b8' }}>{user.email}</div>
              </div>
            </div>
          )}
        </div>
      </header>

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 520, margin: '0 auto', padding: '48px 24px 80px' }}>
        <div
          style={{
            background: '#fff',
            borderRadius: 20,
            border: '1px solid #d7e0ea',
            padding: '36px 32px',
            boxShadow: '0 14px 44px rgba(15,23,42,0.08)',
          }}
        >
          {step < 4 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, gap: 8 }}>
              {['Company', 'Channels', 'Confirm'].map((label, index) => (
                <span
                  key={label}
                  style={{
                    fontSize: 11,
                    fontWeight: step === index + 1 ? 700 : 400,
                    color: step === index + 1 ? '#2563eb' : '#94a3b8',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                  }}
                >
                  {label}
                </span>
              ))}
            </div>
          )}

          {step < 4 ? <ProgressBar step={step} /> : null}

          {step === 4 && (
            <EnterpriseInviteStep
              onDone={(nextUser, token) => {
                setAuthFromOAuth(nextUser, token);
                navigate(postAuthDestination(nextUser), { replace: true });
              }}
            />
          )}

          {step === 1 && (
            <CompanyStep
              user={user}
              form={companyForm}
              onChange={(key, value) => setCompanyForm((prev) => ({ ...prev, [key]: value }))}
              onNext={() => setStep(2)}
              onSkip={() => setStep(2)}
            />
          )}

          {step === 2 && (
            <ChannelsStep
              selected={channels}
              onToggle={toggleChannel}
              onNext={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          )}

          {step === 3 && (
            <ConfirmStep
              onBack={() => setStep(2)}
              onFinish={markComplete}
              busy={completing}
            />
          )}
        </div>

        <p style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8', marginTop: 20 }}>
          You can always update these later in <strong>Settings</strong>.
        </p>
      </div>
      <style>{'@keyframes spin{to{transform:rotate(360deg)}}'}</style>
    </div>
  );
}
