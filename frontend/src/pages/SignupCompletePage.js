import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { CheckCircle, CreditCard, Loader, Mail, ShieldAlert } from 'lucide-react';

import PlatformLogo from '@/components/PlatformLogo';
import api from '@/lib/api';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
const MAX_STATUS_ATTEMPTS = 40;
const STATUS_POLL_INTERVAL_MS = 3000;
const DEFAULT_SUPPORT_EMAIL = 'hello@pulseengine.io';

function buildVerificationParams(email, verificationMeta = {}) {
  const params = new URLSearchParams({
    email: email || verificationMeta.email || '',
    cooldown: String(verificationMeta.resend_available_in_seconds || 120),
    attempts: String(verificationMeta.remaining_attempts ?? 2),
    locked: verificationMeta.locked ? '1' : '0',
    lock: String(verificationMeta.lock_remaining_seconds || 0),
  });
  return params.toString();
}

export default function SignupCompletePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const sessionId = (searchParams.get('session_id') || '').trim();
  const [status, setStatus] = useState(sessionId ? 'processing' : 'missing');
  const [message, setMessage] = useState(
    sessionId
      ? 'We are confirming your payment and provisioning your workspace.'
      : 'This signup confirmation link is missing a Stripe session ID.'
  );
  const [details, setDetails] = useState(null);

  useEffect(() => {
    if (!sessionId) return undefined;

    let cancelled = false;
    let attempts = 0;
    let timerId = null;

    const checkStatus = async () => {
      attempts += 1;
      try {
        const response = await api.get(`/auth/register/status?session_id=${encodeURIComponent(sessionId)}`);
        if (cancelled) return;
        const data = response.data || {};
        setDetails(data);

        if (data.setup_failed) {
          const supportEmail = data.support_email || DEFAULT_SUPPORT_EMAIL;
          setStatus('error');
          setMessage(
            data.support_message ||
              data.verification_error ||
              `Payment was confirmed, but workspace setup could not finish automatically. Contact support at ${supportEmail} with your signup email and Stripe session ID.`,
          );
          return;
        }

        if (data.account_created) {
          if (data.email_verified) {
            setStatus('success');
            setMessage('Your workspace is ready. Redirecting you to sign in.');
            window.setTimeout(() => navigate('/signin', { replace: true }), 1200);
            return;
          }
          setStatus('verifying');
          setMessage('Payment succeeded and your workspace has been created. One last step: verify your email to activate the account.');
          const verificationQuery = buildVerificationParams(data.email, data.email_verification || {});
          window.setTimeout(() => navigate(`/verify-email?${verificationQuery}`, { replace: true }), 900);
          return;
        }

        if (data.status === 'expired' || data.payment_status === 'failed') {
          setStatus('error');
          setMessage(data.verification_error || 'We could not complete your signup. Please start again.');
          return;
        }

        setStatus('processing');
        setMessage(
          data.payment_status === 'paid'
            ? 'Payment is confirmed. We are finishing workspace setup now.'
            : 'Payment confirmation is still in progress. This page will refresh automatically.'
        );
        if (attempts < MAX_STATUS_ATTEMPTS) {
          timerId = window.setTimeout(checkStatus, STATUS_POLL_INTERVAL_MS);
        } else {
          const supportEmail = data.support_email || DEFAULT_SUPPORT_EMAIL;
          setStatus('pending');
          setMessage(`Payment looks successful, but setup is taking longer than expected. Contact support at ${supportEmail} with your signup email and Stripe session ID.`);
        }
      } catch (err) {
        if (cancelled) return;
        setStatus('error');
        setMessage(err.response?.data?.detail || 'Unable to confirm your signup status.');
      }
    };

    checkStatus();
    return () => {
      cancelled = true;
      if (timerId) window.clearTimeout(timerId);
    };
  }, [navigate, sessionId]);

  const cardStyle = {
    background: '#fff',
    borderRadius: 24,
    border: '1px solid rgba(148,163,184,0.2)',
    padding: '34px 30px',
    boxShadow: '0 20px 42px rgba(15,23,42,0.08)',
    maxWidth: 520,
    width: '100%',
    textAlign: 'center',
  };

  const iconMap = {
    processing: <Loader size={30} color="#2563eb" style={{ animation: 'spin 1s linear infinite' }} />,
    pending: <CreditCard size={30} color="#2563eb" />,
    verifying: <Mail size={30} color="#2563eb" />,
    success: <CheckCircle size={30} color="#16a34a" />,
    error: <ShieldAlert size={30} color="#dc2626" />,
    missing: <ShieldAlert size={30} color="#dc2626" />,
  };

  const accentMap = {
    processing: 'linear-gradient(135deg,#dbeafe,#eff6ff)',
    pending: 'linear-gradient(135deg,#dbeafe,#eff6ff)',
    verifying: 'linear-gradient(135deg,#e0f2fe,#eff6ff)',
    success: 'linear-gradient(135deg,#dcfce7,#ecfdf5)',
    error: 'linear-gradient(135deg,#fee2e2,#fef2f2)',
    missing: 'linear-gradient(135deg,#fee2e2,#fef2f2)',
  };

  return (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)', fontFamily: F }}>
      <header style={{ position: 'sticky', top: 0, zIndex: 10, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #e2e8f0' }}>
        <div style={{ maxWidth: 1120, margin: '0 auto', padding: '0 24px', height: 64, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>

      <div style={{ minHeight: 'calc(100vh - 64px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <div style={cardStyle} data-testid="signup-complete-page">
          <div style={{ width: 72, height: 72, borderRadius: 22, background: accentMap[status], display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            {iconMap[status]}
          </div>
          <h1 style={{ fontSize: 28, lineHeight: 1.15, fontWeight: 800, color: '#0f172a', marginBottom: 10 }}>
            {status === 'success' && 'Workspace Ready'}
            {status === 'verifying' && 'Verify Your Email'}
            {status === 'processing' && 'Finalizing Signup'}
            {status === 'pending' && 'Still Working'}
            {(status === 'error' || status === 'missing') && 'Signup Confirmation Problem'}
          </h1>
          <p style={{ fontSize: 15, lineHeight: 1.7, color: '#475569', marginBottom: 18 }}>
            {message}
          </p>

          {details?.email && (
            <p style={{ marginBottom: 18, fontSize: 13, color: '#64748b' }}>
              Email: <strong style={{ color: '#0f172a' }}>{details.email}</strong>
            </p>
          )}

          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link to="/signin" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 20px', borderRadius: 12, background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', textDecoration: 'none', fontSize: 14, fontWeight: 700 }}>
              Go to Sign In
            </Link>
            <Link to="/signup" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 20px', borderRadius: 12, border: '1px solid #d7dce5', background: '#fff', color: '#475569', textDecoration: 'none', fontSize: 14, fontWeight: 600 }}>
              Start Over
            </Link>
            {(status === 'error' || status === 'pending') && (
              <Link to="/contact" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 20px', borderRadius: 12, border: '1px solid #bfdbfe', background: '#eff6ff', color: '#1d4ed8', textDecoration: 'none', fontSize: 14, fontWeight: 700 }}>
                Contact Support
              </Link>
            )}
          </div>
        </div>
      </div>

      <style>{'@keyframes spin{to{transform:rotate(360deg)}}'}</style>
    </div>
  );
}
