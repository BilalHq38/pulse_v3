import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api, { refreshAuthSession } from '@/lib/api';
import { postAuthDestination } from '@/lib/auth-gates';
import { normalizeAvatarUrl, displayNameInitial } from '@/lib/avatar';
import PlatformLogo from '@/components/PlatformLogo';
import {
  Building2, Globe, Phone, Briefcase,
  CheckCircle, AlertCircle,
  ArrowRight, ArrowLeft, MessageSquare, UserPlus,
} from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const INDUSTRIES = [
  'Technology', 'E-commerce', 'Finance & Banking', 'Healthcare',
  'Education', 'Real Estate', 'Marketing & Advertising', 'Logistics',
  'Hospitality', 'Retail', 'Manufacturing', 'Consulting', 'Other',
];

const inputStyle = {
  width: '100%', padding: '10px 14px 10px 38px', fontSize: 14,
  border: '1.5px solid #e2e8f0', borderRadius: 10, outline: 'none',
  background: '#fff', color: '#0f172a', transition: 'all 0.2s',
  boxSizing: 'border-box', fontFamily: F,
};
const inputStyleNoIcon = { ...inputStyle, paddingLeft: 14 };
const focusIn  = e => { e.target.style.borderColor = '#2563eb'; e.target.style.boxShadow = '0 0 0 3px rgba(37,99,235,0.1)'; };
const focusOut = e => { e.target.style.borderColor = '#e2e8f0'; e.target.style.boxShadow = 'none'; };

function IconInput({ icon: Icon, type = 'text', value, onChange, placeholder, required, right, ...rest }) {
  return (
    <div style={{ position: 'relative' }}>
      <Icon size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
      <input
        type={type} value={value} onChange={onChange}
        placeholder={placeholder} style={{ ...inputStyle, paddingRight: right ? 40 : 14 }}
        onFocus={focusIn} onBlur={focusOut} required={required} {...rest}
      />
      {right}
    </div>
  );
}

function Label({ children }) {
  return <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{children}</label>;
}

function ProgressBar({ step }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 36 }}>
      {[1, 2, 3].map((s) => (
        <div key={s} style={{ display: 'flex', alignItems: 'center', flex: s < 3 ? 1 : 0 }}>
          <div style={{
            width: 32, height: 32, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontWeight: 700, fontSize: 13, flexShrink: 0,
            background: step >= s ? 'linear-gradient(135deg,#2563eb,#6366f1)' : '#f1f5f9',
            color: step >= s ? '#fff' : '#94a3b8',
            boxShadow: step >= s ? '0 4px 12px rgba(37,99,235,0.3)' : 'none',
            transition: 'all 0.3s',
          }}>
            {step > s ? <CheckCircle size={16} /> : s}
          </div>
          {s < 3 && (
            <div style={{ flex: 1, height: 3, background: step > s ? 'linear-gradient(90deg,#2563eb,#6366f1)' : '#e2e8f0', margin: '0 8px', borderRadius: 4, transition: 'background 0.3s' }} />
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Step 1: Company Details ────────────────────────────────────────────────
function CompanyStep({ user, onNext, onSkip }) {
  const [form, setForm] = useState({
    company_name: '', industry: '', website_address: '', phone: '', description: '', tagline: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = (k, v) => setForm(p => ({ ...p, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!form.company_name.trim()) { setError('Company name is required'); return; }
    if (!form.industry.trim()) { setError('Business type / industry is required'); return; }
    setSaving(true);
    try {
      await api.put('/settings/company', {
        company_name: form.company_name.trim(),
        industry: form.industry,
        website_address: form.website_address.trim(),
        phone: form.phone.trim(),
        description: form.description.trim(),
        tagline: form.tagline.trim(),
        support_email: user?.email || '',
      });
      onNext();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save company details');
    } finally { setSaving(false); }
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div style={{ width: 56, height: 56, borderRadius: 16, background: 'linear-gradient(135deg,#eff6ff,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
          <Building2 size={26} color="#2563eb" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Set up your company</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>Tell us about your business so we can personalise your experience</p>
      </div>

      {error && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, fontSize: 13, color: '#dc2626', display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertCircle size={14} /> {error}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
        <div style={{ gridColumn: '1/-1' }}>
          <Label>Company name *</Label>
          <IconInput icon={Building2} value={form.company_name} onChange={e => set('company_name', e.target.value)} placeholder="Acme Corp" required />
        </div>

        <div>
          <Label>Industry</Label>
          <div style={{ position: 'relative' }}>
            <Briefcase size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <select
              value={form.industry} onChange={e => set('industry', e.target.value)}
              style={{ ...inputStyle, appearance: 'none', cursor: 'pointer' }}
              onFocus={focusIn} onBlur={focusOut}
            >
              <option value="">Select industry…</option>
              {INDUSTRIES.map(i => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
        </div>

        <div>
          <Label>Phone</Label>
          <IconInput icon={Phone} value={form.phone} onChange={e => set('phone', e.target.value)} placeholder="+1 555 000 0000" />
        </div>

        <div style={{ gridColumn: '1/-1' }}>
          <Label>Website</Label>
          <IconInput icon={Globe} value={form.website_address} onChange={e => set('website_address', e.target.value)} placeholder="https://yourcompany.com" />
        </div>

        <div style={{ gridColumn: '1/-1' }}>
          <Label>Tagline</Label>
          <input
            type="text" value={form.tagline} onChange={e => set('tagline', e.target.value)}
            placeholder="A short, catchy company tagline"
            style={inputStyleNoIcon} onFocus={focusIn} onBlur={focusOut}
          />
        </div>

        <div style={{ gridColumn: '1/-1' }}>
          <Label>Description</Label>
          <textarea
            value={form.description} onChange={e => set('description', e.target.value)}
            placeholder="What does your company do?"
            rows={3}
            style={{ ...inputStyleNoIcon, resize: 'vertical', minHeight: 80, lineHeight: 1.6 }}
            onFocus={focusIn} onBlur={focusOut}
          />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
        <button
          type="button" onClick={onSkip}
          style={{ flex: 1, padding: '11px 0', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#64748b', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F }}
        >
          Skip for now
        </button>
        <button
          type="submit" disabled={saving}
          style={{ flex: 2, padding: '11px 0', borderRadius: 10, border: 'none', background: saving ? '#94a3b8' : 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: saving ? 'wait' : 'pointer', fontFamily: F, boxShadow: saving ? 'none' : '0 4px 14px rgba(37,99,235,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
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

// ─── Step 2: Preferred channels ─────────────────────────────────────────────
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
        <div style={{ width: 56, height: 56, borderRadius: 16, background: 'linear-gradient(135deg,#eff6ff,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
          <MessageSquare size={26} color="#2563eb" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Preferred channels</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>Choose where you want to engage customers first.</p>
      </div>
      {error && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, fontSize: 13, color: '#dc2626', display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertCircle size={14} /> {error}
        </div>
      )}
      <div style={{ display: 'grid', gap: 10 }}>
        {CHANNEL_OPTIONS.map((ch) => {
          const on = selected.includes(ch.id);
          return (
            <button
              key={ch.id}
              type="button"
              onClick={() => onToggle(ch.id)}
              style={{
                textAlign: 'left',
                padding: '14px 16px',
                borderRadius: 12,
                border: on ? '2px solid #2563eb' : '1.5px solid #e2e8f0',
                background: on ? 'rgba(37,99,235,0.06)' : '#fff',
                cursor: 'pointer',
                fontFamily: F,
                fontSize: 15,
                fontWeight: 600,
                color: '#0f172a',
              }}
            >
              {ch.label}
            </button>
          );
        })}
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
        <button
          type="button"
          onClick={onBack}
          style={{ flex: 1, padding: '11px 0', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#64748b', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
        >
          <ArrowLeft size={15} /> Back
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={handleNext}
          style={{ flex: 2, padding: '11px 0', borderRadius: 10, border: 'none', background: saving ? '#94a3b8' : 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: saving ? 'wait' : 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
        >
          {saving ? <div style={{ width: 16, height: 16, border: '2px solid rgba(255,255,255,0.5)', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} /> : <><span>Continue</span><ArrowRight size={16} /></>}
        </button>
      </div>
    </div>
  );
}

// ─── Step 3: Confirm ────────────────────────────────────────────────────────
function ConfirmStep({ onBack, onFinish, busy }) {
  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div style={{ width: 56, height: 56, borderRadius: 16, background: 'linear-gradient(135deg,#f0fdf4,#dcfce7)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
          <CheckCircle size={28} color="#16a34a" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Confirm setup</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>You can change channels and company details later in Settings.</p>
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
        <button
          type="button"
          onClick={onBack}
          style={{ flex: 1, padding: '11px 0', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#64748b', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F }}
        >
          <ArrowLeft size={15} style={{ verticalAlign: 'middle', marginRight: 6 }} /> Back
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onFinish}
          style={{ flex: 2, padding: '11px 0', borderRadius: 10, border: 'none', background: busy ? '#94a3b8' : 'linear-gradient(135deg,#059669,#10b981)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: busy ? 'wait' : 'pointer', fontFamily: F }}
        >
          {busy ? 'Saving…' : 'Complete setup'}
        </button>
      </div>
    </div>
  );
}

// ─── Enterprise: invite at least one teammate before full app access ─────────
function EnterpriseInviteStep({ onDone }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    const em = email.trim().toLowerCase();
    if (!em) {
      setErr('Email is required');
      return;
    }
    setBusy(true);
    try {
      await api.post('/auth/onboarding/invite', {
        email: em,
        name: name.trim() || undefined,
        role: 'company_agent',
      });
      const refreshed = await refreshAuthSession();
      if (refreshed?.token && refreshed?.user) {
        onDone(refreshed.user, refreshed.token);
      }
    } catch (er) {
      setErr(er.response?.data?.detail || er.response?.data?.error || 'Could not send invitation');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div style={{ width: 56, height: 56, borderRadius: 16, background: 'linear-gradient(135deg,#eff6ff,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
          <UserPlus size={26} color="#2563eb" />
        </div>
        <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Invite a teammate</h2>
        <p style={{ fontSize: 14, color: '#64748b' }}>
          Enterprise workspaces need at least one invitation sent before you continue. You can invite more people later from Settings.
        </p>
      </div>
      {err && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, fontSize: 13, color: '#dc2626', display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertCircle size={14} /> {err}
        </div>
      )}
      <div style={{ marginBottom: 14 }}>
        <Label>Teammate email</Label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
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
          onChange={(e) => setName(e.target.value)}
          placeholder="How they should appear in the invite"
          style={{ ...inputStyleNoIcon, marginTop: 6 }}
          onFocus={focusIn}
          onBlur={focusOut}
        />
      </div>
      <button
        type="submit"
        disabled={busy}
        style={{ width: '100%', padding: '12px 0', borderRadius: 10, border: 'none', background: busy ? '#94a3b8' : 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 15, fontWeight: 600, cursor: busy ? 'wait' : 'pointer', fontFamily: F }}
      >
        {busy ? 'Sending…' : 'Send invitation'}
      </button>
    </form>
  );
}

// ─── Main Onboarding Page ────────────────────────────────────────────────────
export default function OnboardingPage() {
  const { user, setOnboardingComplete, setAuthFromOAuth } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [completing, setCompleting] = useState(false);
  const [channels, setChannels] = useState([]);
  const [headerAvatar, setHeaderAvatar] = useState(() => normalizeAvatarUrl(localStorage.getItem('pe_avatar') || ''));

  useEffect(() => {
    if (!user) navigate('/signin', { replace: true });
    else if (user.role === 'super_admin') navigate('/super-admin', { replace: true });
    else if (user.email_verified === false) {
      const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
      navigate(`/verify-email${q}`, { replace: true });
    } else if (user.enterprise_invite_gate_pending) {
      setStep(4);
    } else if (user.onboarding_completed === true && user.plan_selected === false) navigate('/billing', { replace: true });
    else if (user.onboarding_completed === true && user.plan_selected !== false) navigate('/dashboard', { replace: true });
  }, [user, navigate]);

  useEffect(() => {
    setHeaderAvatar(normalizeAvatarUrl(user?.avatar || localStorage.getItem('pe_avatar') || ''));
  }, [user?.avatar]);

  const toggleChannel = (id) => {
    setChannels((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const markComplete = async () => {
    setCompleting(true);
    try {
      const res = await api.put('/auth/onboarding/complete');
      const refreshedUser = res.data?.user || user;
      if (res.data?.token && res.data?.user) {
        setAuthFromOAuth(res.data.user, res.data.token);
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
      /* keep user on step 3 */
    } finally {
      setCompleting(false);
    }
  };

  if (completing) {
    return (
      <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ margin: '0 auto 20px', display: 'flex', justifyContent: 'center' }}>
            <PlatformLogo to={null} />
          </div>
          <div style={{ width: 32, height: 32, border: '3px solid #e2e8f0', borderTopColor: '#2563eb', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 16px' }} />
          <p style={{ fontSize: 16, color: '#64748b' }}>Setting up your workspace…</p>
        </div>
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }}>
      {/* Background blobs */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -150, right: -80, width: 480, height: 480, background: 'radial-gradient(circle,rgba(99,102,241,0.08) 0%,transparent 70%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -80, left: -40, width: 360, height: 360, background: 'radial-gradient(circle,rgba(37,99,235,0.06) 0%,transparent 70%)', borderRadius: '50%' }} />
      </div>

      {/* Top bar */}
      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo />

          {/* User avatar + name */}
          {user && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {headerAvatar
                ? <img src={headerAvatar} alt={user.name} referrerPolicy="no-referrer" style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', border: '2px solid #e2e8f0' }} onError={() => setHeaderAvatar('')} />
                : (
                  <div style={{ width: 34, height: 34, borderRadius: '50%', background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontWeight: 700, fontSize: 13 }}>
                    {displayNameInitial(user.name || user.email)}
                  </div>
                )
              }
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#0f172a', lineHeight: 1.2 }}>{user.name || user.email}</div>
                <div style={{ fontSize: 11, color: '#94a3b8' }}>{user.email}</div>
              </div>
            </div>
          )}
        </div>
      </header>

      {/* Card */}
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 520, margin: '0 auto', padding: '48px 24px 80px' }}>
        <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', padding: '36px 32px', boxShadow: '0 8px 40px rgba(0,0,0,0.07)' }}>

          {/* Step labels */}
          {step < 4 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, gap: 8 }}>
              {['Company', 'Channels', 'Confirm'].map((label, i) => (
                <span key={label} style={{ fontSize: 11, fontWeight: step === i + 1 ? 700 : 400, color: step === i + 1 ? '#2563eb' : '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {label}
                </span>
              ))}
            </div>
          )}
          {step < 4 ? <ProgressBar step={step} /> : null}

          {step === 4 && (
            <EnterpriseInviteStep
              onDone={(u, token) => {
                setAuthFromOAuth(u, token);
                navigate(postAuthDestination(u), { replace: true });
              }}
            />
          )}

          {step === 1 && (
            <CompanyStep user={user} onNext={() => setStep(2)} onSkip={() => setStep(2)} />
          )}

          {step === 2 && (
            <>
              <button
                type="button"
                onClick={() => setStep(1)}
                style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', cursor: 'pointer', color: '#64748b', fontSize: 13, fontWeight: 500, padding: 0, marginBottom: 20, fontFamily: F }}
              >
                <ArrowLeft size={14} /> Back
              </button>
              <ChannelsStep
                selected={channels}
                onToggle={toggleChannel}
                onNext={() => setStep(3)}
                onBack={() => setStep(1)}
              />
            </>
          )}

          {step === 3 && (
            <>
              <button
                type="button"
                onClick={() => setStep(2)}
                style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', cursor: 'pointer', color: '#64748b', fontSize: 13, fontWeight: 500, padding: 0, marginBottom: 20, fontFamily: F }}
              >
                <ArrowLeft size={14} /> Back
              </button>
              <ConfirmStep onBack={() => setStep(2)} onFinish={markComplete} busy={completing} />
            </>
          )}
        </div>

        <p style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8', marginTop: 20 }}>
          You can always update these later in <strong>Settings</strong>.
        </p>
      </div>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );
}
