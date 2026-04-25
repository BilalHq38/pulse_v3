import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  CheckCircle,
  CreditCard,
  Eye,
  EyeOff,
  Lock,
  Mail,
  ShieldCheck,
  User,
  XCircle,
} from 'lucide-react';

import PlatformLogo from '@/components/PlatformLogo';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { startOAuthLoginRedirect } from '@/lib/oauthLoginRedirect';
import { getSignupStripePlanOptions, getSignupTrialPlanOptions } from '@/data/publicPricingPlans';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const GoogleIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" aria-hidden>
    <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" fill="#4285F4" />
    <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853" />
    <path d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332Z" fill="#FBBC05" />
    <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.163 6.656 3.58 9 3.58Z" fill="#EA4335" />
  </svg>
);

const FacebookIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden>
    <path d="M24 12.073C24 5.404 18.627 0 12 0S0 5.404 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.41c0-3.025 1.792-4.697 4.532-4.697 1.312 0 2.686.236 2.686.236v2.97h-1.513c-1.491 0-1.956.93-1.956 1.886v2.268h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073Z" fill="#ffffff" />
  </svg>
);

const PLAN_OPTIONS_STRIPE = getSignupStripePlanOptions();

const inputStyle = {
  width: '100%',
  padding: '10px 14px 10px 40px',
  fontSize: 14,
  border: '1.5px solid #e2e8f0',
  borderRadius: 12,
  outline: 'none',
  background: '#fff',
  color: '#0f172a',
  transition: 'all 0.2s',
  boxSizing: 'border-box',
  fontFamily: F,
};

const focusIn = (e) => {
  e.target.style.borderColor = '#2563eb';
  e.target.style.boxShadow = '0 0 0 3px rgba(37,99,235,0.12)';
};

const focusOut = (e) => {
  e.target.style.borderColor = '#e2e8f0';
  e.target.style.boxShadow = 'none';
};

export default function SignUpPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [billingInfo, setBillingInfo] = useState(null);
  const backendBaseUrl = (process.env.REACT_APP_BACKEND_URL || window.location.origin).replace(/\/$/, '');
  const [form, setForm] = useState({
    name: '',
    email: '',
    password: '',
    plan_code: 'pro',
  });

  const checkoutCancelled = searchParams.get('checkout') === 'cancelled';

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/auth/signup-billing-info');
        if (!cancelled) setBillingInfo(res.data || null);
      } catch {
        if (!cancelled) setBillingInfo(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const oauth = searchParams.get('oauth');
    const oauthErr = searchParams.get('oauth_error');
    if (oauthErr === '1') {
      setError('Google or Facebook sign-in could not be completed. Try again or continue with email below.');
      navigate('/signup', { replace: true });
      return;
    }
    if (oauthErr === 'workspace') {
      setError(
        'Multiple workspaces use this email. Sign in from the sign-in page with your workspace, or use a different email to register.',
      );
      navigate('/signup', { replace: true });
      return;
    }
    if (oauthErr === 'no_account') {
      setError(
        'No workspace is linked to that Google or Facebook account yet. Use Continue with Google or Facebook above (same as Sign in) if your team already uses the platform, or complete the form below to register a new workspace. Paid checkout only runs when Stripe is configured.',
      );
      navigate('/signup', { replace: true });
      return;
    }
    if (oauthErr === 'link_required') {
      setError(
        'That email already has an account without this Google or Facebook profile linked. Sign in with your email and password, then open Settings → Security → Linked sign-in to link Google or Facebook.',
      );
      navigate('/signup', { replace: true });
      return;
    }
    if (oauth === '1') {
      const email = (searchParams.get('email') || '').trim().slice(0, 254);
      const name = (searchParams.get('name') || '').trim().slice(0, 200);
      setError('');
      if (email || name) {
        setForm((prev) => ({
          ...prev,
          ...(email ? { email } : {}),
          ...(name ? { name } : {}),
        }));
      }
      navigate('/signup', { replace: true });
    }
  }, [searchParams, navigate]);

  useEffect(() => {
    const p = (searchParams.get('plan') || '').trim().toLowerCase();
    if (p !== 'enterprise' && p !== 'pro') return;
    setForm((prev) => (prev.plan_code === p ? prev : { ...prev, plan_code: p }));
  }, [searchParams]);

  const startOAuth = (provider) => startOAuthLoginRedirect(backendBaseUrl, provider);

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const passwordValidation = useMemo(() => {
    const password = form.password;
    return {
      minLength: password.length >= 8,
      hasLetter: /[a-zA-Z]/.test(password),
      hasNumber: /[0-9]/.test(password),
      hasSpecial: /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(password),
    };
  }, [form.password]);

  const isPasswordValid = Object.values(passwordValidation).every(Boolean);
  const showPasswordRules = form.password.length > 0;

  const offlineTrialSignup =
    billingInfo?.billing_mode === 'trial_no_payment' || billingInfo?.billing_mode === 'trial_relaxed';
  const planOptions = offlineTrialSignup ? getSignupTrialPlanOptions() : PLAN_OPTIONS_STRIPE;
  const trialDays = billingInfo?.trial_days ?? 30;

  const handleSubmit = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!form.name.trim()) {
      setError('Please enter your full name.');
      return;
    }
    if (!form.email.trim()) {
      setError('Please enter your work email.');
      return;
    }
    if (!isPasswordValid) {
      setError('Please meet all password requirements.');
      return;
    }

    setError('');
    setLoading(true);
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
      const response = await register({ ...form, timezone });
      if (response?.checkout_url) {
        window.location.assign(response.checkout_url);
        return;
      }
      if (response?.email_verification?.required) {
        const params = new URLSearchParams({
          email: form.email,
          cooldown: String(response.email_verification.resend_available_in_seconds || 120),
          attempts: String(response.email_verification.remaining_attempts ?? 2),
          locked: response.email_verification.locked ? '1' : '0',
          lock: String(response.email_verification.lock_remaining_seconds || 0),
        });
        navigate(`/verify-email?${params.toString()}`, { replace: true });
        return;
      }
      if (
        response?.status === 'account_created' &&
        (response?.billing_mode === 'trial_no_payment' || response?.billing_mode === 'trial_relaxed') &&
        !response?.email_verification?.required
      ) {
        setError(
          response?.verification_error ||
            'Your workspace was created, but we could not send the verification email. Contact support or try signing in.',
        );
        return;
      }
      setError('Signup started, but checkout could not be opened. Please try again.');
    } catch (err) {
      const status = err.response?.status;
      const raw = err.response?.data?.detail ?? err.response?.data?.error;
      const detail =
        typeof raw === 'string'
          ? raw
          : Array.isArray(raw)
            ? raw.map((e) => (e && typeof e === 'object' ? e.msg || e.type : String(e))).join('; ')
            : raw && typeof raw === 'object' && raw.message
              ? String(raw.message)
              : typeof raw === 'object'
                ? JSON.stringify(raw)
                : String(raw || '');
      if (status === 409 || detail === 'Email already registered') {
        setError('An account with this email already exists. Please sign in instead.');
      } else if (status === 503) {
        setError(
          'Billing is not ready (Stripe). Set STRIPE_SECRET_KEY and STRIPE_PRICE_PRO / STRIPE_PRICE_ENTERPRISE in the server environment, then retry.',
        );
      } else if (status === 502) {
        setError('Could not start Stripe checkout. Verify Stripe keys and price IDs, then try again.');
      } else if (status === 501) {
        setError('Stripe SDK or billing is not available on the server. Contact your administrator.');
      } else {
        setError(detail || err.message || 'Registration failed');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(180deg, #f8fafc 0%, #eff6ff 100%)', fontFamily: F }}>
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -220, right: -80, width: 480, height: 480, background: 'radial-gradient(circle, rgba(37,99,235,0.08) 0%, transparent 70%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -120, left: -40, width: 420, height: 420, background: 'radial-gradient(circle, rgba(14,165,233,0.08) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>

      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 13, color: '#64748b' }}>Already have an account?</span>
            <Link to="/signin" style={{ fontSize: 13.5, fontWeight: 600, color: '#2563eb', textDecoration: 'none', padding: '7px 16px', borderRadius: 999, border: '1px solid rgba(37,99,235,0.2)', background: 'rgba(37,99,235,0.04)' }}>
              Sign In
            </Link>
          </div>
        </div>
      </header>

      <div className="signup-layout" style={{ position: 'relative', zIndex: 1, maxWidth: 1180, margin: '0 auto', padding: '42px 24px 72px', gap: 28 }}>
        <section className="signup-col-marketing" style={{ alignSelf: 'start' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 999, background: 'rgba(37,99,235,0.08)', color: '#1d4ed8', fontSize: 12, fontWeight: 700, letterSpacing: 0.3, textTransform: 'uppercase' }}>
            <ShieldCheck size={14} />
            {offlineTrialSignup ? 'Free trial onboarding' : 'Secure Paid Onboarding'}
          </div>
          <h1 style={{ fontSize: 42, lineHeight: 1.08, fontWeight: 800, color: '#0f172a', margin: '18px 0 14px' }}>
            {offlineTrialSignup
              ? `Start your workspace with a ${trialDays}-day trial—no payment.`
              : 'Create your workspace only after verified payment.'}
          </h1>
          <p style={{ fontSize: 16, lineHeight: 1.75, color: '#475569', maxWidth: 680 }}>
            {offlineTrialSignup
              ? `We create your admin account immediately. You get a ${trialDays}-day trial on the plan you choose, then sign in and complete onboarding.`
              : 'Enter your admin details here, then complete secure checkout. After payment succeeds, your workspace is provisioned and we guide you through admin activation.'}
          </p>
          {offlineTrialSignup && billingInfo?.trial_mode_message && (
            <p style={{ fontSize: 14, lineHeight: 1.65, color: '#2563eb', maxWidth: 680, marginTop: 4 }}>
              {billingInfo.trial_mode_message}
            </p>
          )}

          <div style={{ marginTop: 24, display: 'grid', gap: 16 }}>
            {planOptions.map((plan) => {
              const selected = !plan.contactSales && form.plan_code === plan.code;
              const cardShell = {
                width: '100%',
                textAlign: 'left',
                background: selected ? 'linear-gradient(135deg, rgba(37,99,235,0.09), rgba(14,165,233,0.08))' : '#fff',
                border: selected ? '1.5px solid rgba(37,99,235,0.45)' : '1px solid rgba(148,163,184,0.18)',
                borderRadius: 20,
                padding: 20,
                boxShadow: selected ? '0 16px 30px rgba(37,99,235,0.08)' : '0 8px 22px rgba(15,23,42,0.04)',
              };
              const body = (
                <div style={{ display: 'flex', alignItems: 'start', justifyContent: 'space-between', gap: 16 }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                      <span style={{ fontSize: 20, fontWeight: 800, color: '#0f172a' }}>{plan.name}</span>
                      {selected && (
                        <span style={{ padding: '4px 8px', borderRadius: 999, background: '#dbeafe', color: '#1d4ed8', fontSize: 11, fontWeight: 700 }}>Selected</span>
                      )}
                      {plan.contactSales && (
                        <span style={{ padding: '4px 8px', borderRadius: 999, background: '#f1f5f9', color: '#475569', fontSize: 11, fontWeight: 700 }}>Sales</span>
                      )}
                    </div>
                    <p style={{ fontSize: 14, lineHeight: 1.6, color: '#475569', marginBottom: 12 }}>{plan.description}</p>
                    <div style={{ display: 'grid', gap: 8 }}>
                      {plan.bullets.map((bullet) => (
                        <div key={bullet} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 12.75, lineHeight: 1.45, color: '#334155' }}>
                          <CheckCircle size={14} color="#16a34a" style={{ flexShrink: 0, marginTop: 2 }} />
                          <span>{bullet}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{ fontSize: 20, fontWeight: 800, color: '#0f172a', whiteSpace: 'nowrap' }}>{plan.price}</div>
                </div>
              );
              if (plan.contactSales) {
                return (
                  <Link key={plan.code} to="/contact" style={{ ...cardShell, cursor: 'pointer', textDecoration: 'none', color: 'inherit', display: 'block' }}>
                    {body}
                  </Link>
                );
              }
              return (
                <button key={plan.code} type="button" onClick={() => set('plan_code', plan.code)} style={{ ...cardShell, cursor: 'pointer' }}>
                  {body}
                </button>
              );
            })}
          </div>

          <div style={{ marginTop: 20, padding: '16px 18px', borderRadius: 18, background: '#fff', border: '1px solid rgba(148,163,184,0.16)', boxShadow: '0 10px 26px rgba(15,23,42,0.04)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <CreditCard size={16} color="#2563eb" />
              <span style={{ fontSize: 13, fontWeight: 800, color: '#0f172a', textTransform: 'uppercase', letterSpacing: 0.2 }}>What happens next</span>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {(offlineTrialSignup
                ? [
                    '1. You submit this form—we create your tenant workspace and trial subscription right away.',
                    `2. Your plan runs for ${trialDays} days with no payment while hosted checkout is disabled.`,
                    '3. You complete admin activation steps from your inbox.',
                    '4. You sign in and use the product; when billing is enabled later, you can align to Pro or Enterprise.',
                  ]
                : [
                    '1. You submit your details—we prepare your paid registration.',
                    '2. You finish checkout through our payment provider.',
                    '3. After confirmation, your workspace is provisioned.',
                    '4. You activate your admin access and sign in.',
                  ]
              ).map((step) => (
                <div key={step} style={{ fontSize: 13.5, lineHeight: 1.65, color: '#475569' }}>{step}</div>
              ))}
            </div>
          </div>
        </section>

        <section className="signup-col-form">
          {checkoutCancelled && (
            <div style={{ marginBottom: 14, padding: '12px 14px', borderRadius: 12, border: '1px solid #fde68a', background: '#fffbeb', color: '#92400e', fontSize: 13 }}>
              Checkout was cancelled. Your workspace has not been created yet.
            </div>
          )}
          {error && (
            <div style={{ marginBottom: 14, padding: '12px 14px', borderRadius: 12, border: '1px solid #fecaca', background: '#fef2f2', display: 'flex', alignItems: 'center', gap: 8 }}>
              <XCircle size={15} color="#dc2626" />
              <span style={{ fontSize: 13, color: '#dc2626' }}>{error}</span>
            </div>
          )}

          <div style={{ background: '#fff', borderRadius: 24, border: '1px solid rgba(148,163,184,0.18)', padding: 20, boxShadow: '0 18px 42px rgba(15,23,42,0.06)', marginBottom: 14 }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: '#475569', marginBottom: 12 }}>Continue with Google or Facebook</p>
            <button
              type="button"
              onClick={() => startOAuth('google')}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = '#f8fafc';
                e.currentTarget.style.borderColor = '#adb5bd';
                e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.10)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = '#fff';
                e.currentTarget.style.borderColor = '#d1d5db';
                e.currentTarget.style.boxShadow = 'none';
              }}
              style={{ width: '100%', padding: '11px 16px', borderRadius: 10, border: '1.5px solid #d1d5db', background: '#fff', color: '#111827', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, transition: 'all 0.18s' }}
              data-testid="google-signup-prefill-btn"
            >
              <GoogleIcon />
              Continue with Google
            </button>
            <button
              type="button"
              onClick={() => startOAuth('facebook')}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = '#1558c0';
                e.currentTarget.style.boxShadow = '0 4px 14px rgba(22,87,209,0.45)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = '#1877F2';
                e.currentTarget.style.boxShadow = '0 4px 14px rgba(22,87,209,0.30)';
              }}
              style={{ width: '100%', padding: '11px 16px', borderRadius: 10, border: 'none', marginTop: 10, background: '#1877F2', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, transition: 'all 0.18s', boxShadow: '0 4px 14px rgba(22,87,209,0.30)' }}
              data-testid="facebook-signup-prefill-btn"
            >
              <FacebookIcon />
              Continue with Facebook
            </button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <div style={{ flex: 1, height: 1, background: '#e2e8f0' }} />
            <span style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.8 }}>or continue with email</span>
            <div style={{ flex: 1, height: 1, background: '#e2e8f0' }} />
          </div>

          <form noValidate onSubmit={handleSubmit} style={{ background: '#fff', borderRadius: 24, border: '1px solid rgba(148,163,184,0.18)', padding: 26, boxShadow: '0 18px 42px rgba(15,23,42,0.08)' }} data-testid="signup-form">
            <h2 style={{ fontSize: 26, fontWeight: 800, color: '#0f172a', marginBottom: 8 }}>Start Secure Signup</h2>
            <p style={{ fontSize: 14, lineHeight: 1.65, color: '#64748b', marginBottom: 20 }}>
              {offlineTrialSignup
                ? 'Enter your admin profile. We create the workspace first, then continue with verification and onboarding.'
                : 'Enter your admin profile. Payment is required before the account becomes active.'}
            </p>

            <div style={{ display: 'grid', gap: 16 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Full Name *</label>
                <div style={{ position: 'relative' }}>
                  <User size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                  <input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="John Doe" style={inputStyle} onFocus={focusIn} onBlur={focusOut} required />
                </div>
              </div>

              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Work Email *</label>
                <div style={{ position: 'relative' }}>
                  <Mail size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                  <input type="email" value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="you@company.com" style={inputStyle} onFocus={focusIn} onBlur={focusOut} required />
                </div>
              </div>

              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Password *</label>
                <div style={{ position: 'relative' }}>
                  <Lock size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                  <input
                    type={showPw ? 'text' : 'password'}
                    value={form.password}
                    onChange={(e) => set('password', e.target.value)}
                    placeholder="Create a secure password"
                    style={{ ...inputStyle, paddingRight: 42, borderColor: showPasswordRules && !isPasswordValid ? '#f59e0b' : '#e2e8f0' }}
                    onFocus={focusIn}
                    onBlur={focusOut}
                    required
                  />
                  <button type="button" onClick={() => setShowPw((value) => !value)} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', border: 'none', background: 'none', padding: 0, cursor: 'pointer' }}>
                    {showPw ? <EyeOff size={15} color="#94a3b8" /> : <Eye size={15} color="#94a3b8" />}
                  </button>
                </div>
                {showPasswordRules && (
                  <div style={{ marginTop: 10, padding: '12px 14px', background: '#f8fafc', borderRadius: 12 }}>
                    <p style={{ fontSize: 11, fontWeight: 700, color: '#64748b', marginBottom: 8 }}>Password must contain:</p>
                    {[
                      { key: 'minLength', label: 'At least 8 characters', met: passwordValidation.minLength },
                      { key: 'hasLetter', label: 'At least one letter', met: passwordValidation.hasLetter },
                      { key: 'hasNumber', label: 'At least one number', met: passwordValidation.hasNumber },
                      { key: 'hasSpecial', label: 'At least one special character', met: passwordValidation.hasSpecial },
                    ].map((rule) => (
                      <div key={rule.key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        {rule.met ? <CheckCircle size={14} color="#16a34a" /> : <XCircle size={14} color="#cbd5e1" />}
                        <span style={{ fontSize: 12, color: rule.met ? '#16a34a' : '#64748b' }}>{rule.label}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div style={{ marginTop: 16, padding: '12px 14px', background: '#eff6ff', borderRadius: 12, border: '1px solid rgba(37,99,235,0.14)' }}>
              <p style={{ fontSize: 12.5, lineHeight: 1.65, color: '#1e40af' }}>
                By continuing, you agree to our <Link to="/terms" style={{ color: '#2563eb', fontWeight: 700, textDecoration: 'none' }}>Terms of Service</Link> and <Link to="/privacy" style={{ color: '#2563eb', fontWeight: 700, textDecoration: 'none' }}>Privacy Policy</Link>. Card data is handled securely by Stripe and is never stored directly in this platform.
              </p>
            </div>

            <button
              type="submit"
              disabled={loading || !isPasswordValid}
              style={{ width: '100%', padding: '13px 18px', marginTop: 16, borderRadius: 12, border: 'none', background: 'linear-gradient(135deg,#2563eb,#0ea5e9)', color: '#fff', fontSize: 14, fontWeight: 700, cursor: loading ? 'wait' : 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, boxShadow: '0 16px 26px rgba(37,99,235,0.22)', opacity: loading ? 0.72 : 1 }}
            >
              {loading ? (
                <>
                  <div style={{ width: 16, height: 16, border: '2px solid #fff', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
                  {offlineTrialSignup ? 'Creating workspace...' : 'Redirecting to Stripe...'}
                </>
              ) : (
                <>
                  {offlineTrialSignup ? 'Create Workspace' : 'Continue to Secure Checkout'} <ArrowRight size={16} />
                </>
              )}
            </button>
          </form>
        </section>
      </div>

      <style>
        {`
          .signup-layout {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          }
          @media (max-width: 900px) {
            .signup-layout {
              display: flex;
              flex-direction: column;
            }
            .signup-col-form {
              order: -1;
            }
          }
          @keyframes spin {
            to {
              transform: rotate(360deg);
            }
          }
        `}
      </style>
    </div>
  );
}
