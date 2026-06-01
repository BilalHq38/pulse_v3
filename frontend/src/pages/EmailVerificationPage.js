import { useState, useEffect } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import { CheckCircle, XCircle, Loader, Mail, ArrowRight } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { postAuthDestination } from '@/lib/auth-gates';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function EmailVerificationPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setAuthFromOAuth, logout } = useAuth();
  const initialToken = (() => {
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
    return (hashParams.get('token') || searchParams.get('token') || '').trim();
  })();
  const [token, setToken] = useState(initialToken);
  const email = searchParams.get('email') || '';
  const initialCooldown = Number(searchParams.get('cooldown') || 120);
  const initialAttempts = Number(searchParams.get('attempts') || 2);
  const initialLock = Number(searchParams.get('lock') || 0);
  const initiallyLocked = searchParams.get('locked') === '1';
  const [status, setStatus] = useState(initialToken ? 'verifying' : 'pending'); // pending | verifying | success | error
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [cooldownSeconds, setCooldownSeconds] = useState(initiallyLocked ? 0 : initialCooldown);
  const [lockSeconds, setLockSeconds] = useState(initiallyLocked ? initialLock : 0);
  const [remainingAttempts, setRemainingAttempts] = useState(initialAttempts);
  const [redirectTarget, setRedirectTarget] = useState('/signin');
  const [autoRedirect, setAutoRedirect] = useState(false);

  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
    const nextToken = (hashParams.get('token') || searchParams.get('token') || '').trim();
    if (hashParams.has('token')) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
    }
    setToken((prev) => nextToken || prev);
  }, [searchParams]);

  useEffect(() => {
    if (!token) {
      setStatus('pending');
      setRedirectTarget('/signin');
      setAutoRedirect(false);
      setMessage(email ? `A verification email has been sent to ${email}. Open the link in that email to activate your account.` : 'Check your inbox for the verification email.');
      return;
    }
    let cancelled = false;
    setStatus('verifying');
    (async () => {
      try {
        const res = await api.get(`/auth/verify-email?token=${encodeURIComponent(token)}`);
        if (cancelled) return;
        const verifiedUser = res.data?.user || null;
        const verifiedToken = res.data?.token || '';
        const nextPath = res.data?.next_path || (verifiedUser ? postAuthDestination(verifiedUser) : '/signin');
        if (verifiedUser && verifiedToken) {
          setAuthFromOAuth(verifiedUser, verifiedToken);
          setRedirectTarget(nextPath);
          setAutoRedirect(true);
        } else {
          setRedirectTarget(nextPath || '/signin');
          setAutoRedirect(false);
        }
        setStatus('success');
        setMessage(
          res.data?.message ||
            (verifiedUser
              ? 'Email verified successfully. Continue with the next onboarding step.'
              : 'Email verified successfully!')
        );
      } catch (err) {
        if (!cancelled) {
          setRedirectTarget('/signin');
          setAutoRedirect(false);
          setStatus('error');
          setMessage(err.response?.data?.detail || 'Verification failed. The token may be invalid or expired.');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [token, email, setAuthFromOAuth]);

  useEffect(() => {
    if (status !== 'success' || !autoRedirect || !redirectTarget) return undefined;
    const timer = window.setTimeout(() => {
      navigate(redirectTarget, { replace: true });
    }, 1600);
    return () => window.clearTimeout(timer);
  }, [status, autoRedirect, redirectTarget, navigate]);

  useEffect(() => {
    if (lockSeconds <= 0 && cooldownSeconds <= 0) return undefined;
    const timer = window.setInterval(() => {
      setLockSeconds(prev => (prev > 0 ? prev - 1 : 0));
      setCooldownSeconds(prev => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldownSeconds, lockSeconds]);

  const formatSeconds = (value) => {
    const mins = Math.floor(value / 60);
    const secs = value % 60;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  };

  const resendVerification = async () => {
    if (!email) return;
    setSending(true);
    try {
      const res = await api.post('/auth/resend-verification', { email });
      setStatus('pending');
      setMessage(res.data?.message || `A new verification email has been sent to ${email}.`);
      setRemainingAttempts(res.data?.remaining_attempts ?? remainingAttempts);
      setLockSeconds(res.data?.lock_remaining_seconds || 0);
      setCooldownSeconds(res.data?.locked ? 0 : (res.data?.resend_available_in_seconds || 120));
    } catch (err) {
      const data = err.response?.data || {};
      setStatus('pending');
      setRemainingAttempts(data?.remaining_attempts ?? remainingAttempts);
      setLockSeconds(data?.locked ? (data?.retry_after_seconds || 0) : 0);
      setCooldownSeconds(!data?.locked ? (data?.retry_after_seconds || cooldownSeconds) : 0);
      setMessage(typeof data?.detail === 'string' ? data.detail : 'Failed to resend verification email.');
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', flexDirection: 'column' }} data-testid="email-verification-page">
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -120, left: '40%', width: 420, height: 420, background: 'radial-gradient(circle, rgba(99,102,241,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>

      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', zIndex: 1, padding: 24 }}>
        <div style={{ textAlign: 'center', maxWidth: 460 }}>
          <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', padding: '40px 32px', boxShadow: '0 8px 30px rgba(0,0,0,0.06)' }}>
            {status === 'pending' && (
              <>
                <div style={{ width: 72, height: 72, borderRadius: 18, background: 'linear-gradient(135deg,#eff6ff,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
                  <Mail size={32} color="#2563eb" />
                </div>
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Check your inbox</h2>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.6, marginBottom: 24 }}>{message}</p>
                <p style={{ fontSize: 12, color: '#64748b', lineHeight: 1.6, marginBottom: 18 }}>
                  {lockSeconds > 0
                    ? `Resend locked for ${formatSeconds(lockSeconds)} after 2 attempts.`
                    : cooldownSeconds > 0
                      ? `You can resend the verification email in ${formatSeconds(cooldownSeconds)}.`
                      : remainingAttempts > 0
                        ? `${remainingAttempts} resend attempt${remainingAttempts === 1 ? '' : 's'} remaining.`
                        : 'No resend attempts remaining right now.'}
                </p>
                <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
                  {email && (
                    <button onClick={resendVerification} disabled={sending || cooldownSeconds > 0 || lockSeconds > 0 || remainingAttempts <= 0} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, border: 'none', cursor: sending ? 'wait' : 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)', opacity: (sending || cooldownSeconds > 0 || lockSeconds > 0 || remainingAttempts <= 0) ? 0.7 : 1 }}>
                      <Mail size={16} />
                      {sending
                        ? 'Sending...'
                        : lockSeconds > 0
                          ? `Try again in ${formatSeconds(lockSeconds)}`
                          : cooldownSeconds > 0
                            ? `Resend in ${formatSeconds(cooldownSeconds)}`
                            : 'Resend Email'}
                    </button>
                  )}
                  <button onClick={async () => { await logout(); navigate('/signin', { replace: true }); }} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F }}>
                    Back to Sign In
                  </button>
                </div>
              </>
            )}

            {status === 'verifying' && (
              <>
                <div style={{ width: 72, height: 72, borderRadius: 18, background: 'linear-gradient(135deg,#eff6ff,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px', animation: 'spinSlow 2s linear infinite' }}>
                  <Loader size={32} color="#2563eb" />
                </div>
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Verifying your email</h2>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.6 }}>Please wait while we verify your email address...</p>
                <div style={{ marginTop: 20, height: 3, background: '#f1f5f9', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{ height: '100%', background: 'linear-gradient(135deg,#2563eb,#6366f1)', borderRadius: 4, animation: 'verifyBar 1.5s ease-in-out infinite' }} />
                </div>
              </>
            )}

            {status === 'success' && (
              <>
                <div style={{ width: 72, height: 72, borderRadius: 18, background: 'linear-gradient(135deg,#ecfdf5,#d1fae5)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
                  <CheckCircle size={32} color="#22c55e" />
                </div>
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Email Verified! 🎉</h2>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.6, marginBottom: 24 }}>{message}</p>
                {autoRedirect && (
                  <p style={{ fontSize: 12, color: '#94a3b8', marginBottom: 12 }}>
                    Redirecting you to the next setup step...
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => navigate(redirectTarget, { replace: true })}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 28px', borderRadius: 10, background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, textDecoration: 'none', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)', border: 'none', cursor: 'pointer' }}
                >
                  {redirectTarget === '/signin' ? 'Sign In' : 'Continue Setup'} <ArrowRight size={16} />
                </button>
              </>
            )}

            {status === 'error' && (
              <>
                <div style={{ width: 72, height: 72, borderRadius: 18, background: 'linear-gradient(135deg,#fef2f2,#fee2e2)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
                  <XCircle size={32} color="#ef4444" />
                </div>
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Verification Failed</h2>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.6, marginBottom: 24 }}>{message}</p>
                <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
                  <Link to="/signup" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, textDecoration: 'none', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)' }}>
                    <Mail size={16} /> Try Again
                  </Link>
                  <Link to="/" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, textDecoration: 'none', fontFamily: F }}>
                    Go Home
                  </Link>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      <style>{`
        @keyframes spinSlow{to{transform:rotate(360deg)}}
        @keyframes verifyBar{0%{width:0}50%{width:80%}100%{width:100%}}
      `}</style>
    </div>
  );
}
