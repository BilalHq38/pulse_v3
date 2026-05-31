import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { CreditCard, Sparkles, RefreshCw } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import PlatformLogo from '@/components/PlatformLogo';
import { postAuthDestination, postAuthDestinationFromURL, shouldRedirectToBilling } from '@/lib/auth-gates';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useSocket } from '@/lib/useSocket';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

// Polling intervals: fast first (1.5s × 7), then medium (3s × 7), then slow (5s × 6)
function nextPollDelay(attempt) {
  if (attempt < 7) return 1500;
  if (attempt < 14) return 3000;
  return 5000;
}
const MAX_ATTEMPTS = 20; // ~45 seconds total coverage

export default function BillingPlanPage() {
  const { user, setAuthFromOAuth, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState('');
  const [pollingAttempt, setPollingAttempt] = useState(0);
  const [pollingActive, setPollingActive] = useState(false);

  // Socket listener — instant plan confirmation when backend emits plan_updated
  const handleSocketEvent = useCallback(async (eventName) => {
    if (eventName === 'plan_updated' && pollingActive) {
      const refreshedUser = await refreshUser();
      if (refreshedUser && !shouldRedirectToBilling(refreshedUser)) {
        setLoading('');
        setPollingActive(false);
        navigate(postAuthDestination(refreshedUser), { replace: true });
      }
    }
  }, [pollingActive, refreshUser, navigate]);

  useSocket(handleSocketEvent);

  useEffect(() => {
    if (!user) navigate('/signin', { replace: true });
    else if (user.role === 'super_admin') navigate('/super-admin', { replace: true });
    else if (user.onboarding_completed === false) navigate('/onboarding', { replace: true });
    // Only redirect away if plan_selected and NOT in checkout=success flow
    else if (!shouldRedirectToBilling(user) && !searchParams.get('checkout')) {
      navigate(postAuthDestination(user), { replace: true });
    }
  }, [user, navigate, searchParams]);

  const runPolling = useCallback(() => {
    let cancelled = false;
    let timeoutId;
    let attempts = 0;
    setError('');
    setLoading('paid');
    setPollingActive(true);
    setPollingAttempt(0);

    const syncCheckoutState = async () => {
      attempts += 1;
      setPollingAttempt(attempts);
      const refreshedUser = await refreshUser();
      if (cancelled) return;
      if (refreshedUser && !shouldRedirectToBilling(refreshedUser)) {
        setLoading('');
        setPollingActive(false);
        navigate(postAuthDestinationFromURL(refreshedUser), { replace: true });
        return;
      }
      if (attempts >= MAX_ATTEMPTS) {
        setLoading('');
        setPollingActive(false);
        setError('Payment was received, but we are still confirming your subscription. Click "Check again" to retry.');
        return;
      }
      timeoutId = window.setTimeout(syncCheckoutState, nextPollDelay(attempts));
    };

    syncCheckoutState();
    return () => {
      cancelled = true;
      setPollingActive(false);
      if (timeoutId) window.clearTimeout(timeoutId);
    };
  }, [navigate, refreshUser]);

  useEffect(() => {
    if (!user) return undefined;
    const checkoutState = (searchParams.get('checkout') || '').trim().toLowerCase();
    if (checkoutState === 'cancelled') {
      setError('Checkout was canceled. Choose a plan whenever you are ready.');
      return undefined;
    }
    if (checkoutState !== 'success') return undefined;
    return runPolling();
  }, [navigate, refreshUser, searchParams, user, runPolling]);

  const selectPlan = async (mode) => {
    setError('');
    setLoading(mode);
    try {
      const res = await api.post('/auth/billing/plan', { mode });
      if (res.data?.checkout_url) {
        window.location.assign(res.data.checkout_url);
        return;
      }
      const { token, user: updatedUser } = res.data || {};
      if (token && updatedUser) {
        setAuthFromOAuth(updatedUser, token);
        navigate(postAuthDestination(updatedUser), { replace: true });
        return;
      }
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Could not save plan');
    } finally {
      setLoading('');
    }
  };

  if (!user) return null;

  const isConfirming = loading === 'paid';

  return (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(180deg, #f8fafc 0%, #eff6ff 100%)', fontFamily: F }}>
      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9' }}>
        <div style={{ maxWidth: 960, margin: '0 auto', padding: '0 24px', height: 58, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '48px 24px 80px' }}>

        {/* Stripe payment confirmation state */}
        {isConfirming ? (
          <div style={{ textAlign: 'center', padding: '48px 24px' }}>
            <div style={{
              width: 64, height: 64, borderRadius: '50%',
              background: 'linear-gradient(135deg, #2563eb, #7c3aed)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              margin: '0 auto 24px',
              animation: 'spin 2s linear infinite',
            }}>
              <RefreshCw size={28} color="white" />
            </div>
            <h1 style={{ fontSize: 24, fontWeight: 800, color: '#0f172a', marginBottom: 10 }}>Confirming your payment</h1>
            <p style={{ fontSize: 15, color: '#64748b', lineHeight: 1.6, marginBottom: 12 }}>
              We're confirming your subscription with Stripe. This usually takes a few seconds.
            </p>
            <div style={{ display: 'flex', justifyContent: 'center', gap: 6, marginTop: 16 }}>
              {[0, 1, 2].map(i => (
                <div key={i} style={{
                  width: 8, height: 8, borderRadius: '50%', background: '#2563eb',
                  animation: `dotBounce 1.2s ease-in-out ${i * 0.2}s infinite`,
                }} />
              ))}
            </div>
            {pollingAttempt > 3 && (
              <p style={{ marginTop: 16, fontSize: 13, color: '#94a3b8' }}>
                Still working... ({pollingAttempt}/{MAX_ATTEMPTS})
              </p>
            )}
            <style>{`
              @keyframes spin { to { transform: rotate(360deg); } }
              @keyframes dotBounce {
                0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
                40% { transform: translateY(-8px); opacity: 1; }
              }
            `}</style>
          </div>
        ) : (
          <>
            <h1 style={{ fontSize: 28, fontWeight: 800, color: '#0f172a', marginBottom: 8 }}>Choose your plan</h1>
            <p style={{ fontSize: 15, color: '#64748b', marginBottom: 28, lineHeight: 1.6 }}>
              Complete this step to unlock your workspace. Start with a 30-day free trial or continue to a paid subscription when you are ready for full production access.
            </p>

            {error && (
              <Alert variant="destructive" className="mb-4 border-red-200 bg-red-50 text-red-700">
                <AlertDescription>
                  {error}
                  {error.includes('Check again') && (
                    <button
                      type="button"
                      onClick={runPolling}
                      style={{ marginLeft: 12, fontWeight: 700, color: '#2563eb', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', fontSize: 13 }}
                    >
                      Check again
                    </button>
                  )}
                </AlertDescription>
              </Alert>
            )}

            <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
              <button
                type="button"
                disabled={!!loading}
                onClick={() => selectPlan('free_trial')}
                style={{
                  textAlign: 'left', padding: 22, borderRadius: 18,
                  border: '1.5px solid rgba(37,99,235,0.35)',
                  background: '#fff', cursor: loading ? 'wait' : 'pointer',
                  boxShadow: '0 12px 32px rgba(37,99,235,0.12)',
                }}
              >
                <Sparkles size={22} color="#2563eb" style={{ marginBottom: 10 }} />
                <div style={{ fontSize: 18, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>30-day free trial</div>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.55, marginBottom: 14 }}>
                  Explore and test the platform for one month with no payment required. Use this trial to evaluate the system before committing to a paid plan.
                </p>
                <span style={{ fontSize: 13, fontWeight: 700, color: '#2563eb' }}>
                  {loading === 'free_trial' ? 'Starting trial...' : 'Start 30-day trial ->'}
                </span>
              </button>

              <button
                type="button"
                disabled={!!loading}
                onClick={() => selectPlan('paid')}
                style={{
                  textAlign: 'left', padding: 22, borderRadius: 18,
                  border: '1px solid rgba(148,163,184,0.35)',
                  background: '#fff', cursor: loading ? 'wait' : 'pointer',
                }}
              >
                <CreditCard size={22} color="#0f172a" style={{ marginBottom: 10 }} />
                <div style={{ fontSize: 18, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>Paid plan</div>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.55, marginBottom: 14 }}>
                  Get full access to all platform features with active billing, production support, and no trial-period restrictions.
                </p>
                <span style={{ fontSize: 13, fontWeight: 700, color: '#334155' }}>
                  {loading === 'paid' ? 'Opening checkout...' : 'Continue to Payment ->'}
                </span>
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
