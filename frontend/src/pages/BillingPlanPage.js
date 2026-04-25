import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { CreditCard, Sparkles } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import PlatformLogo from '@/components/PlatformLogo';
import { postAuthDestination } from '@/lib/auth-gates';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function BillingPlanPage() {
  const { user, setAuthFromOAuth, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState('');

  useEffect(() => {
    if (!user) navigate('/signin', { replace: true });
    else if (user.role === 'super_admin') navigate('/super-admin', { replace: true });
    else if (user.onboarding_completed === false) navigate('/onboarding', { replace: true });
    else if (user.plan_selected !== false) navigate(postAuthDestination(user), { replace: true });
  }, [user, navigate]);

  useEffect(() => {
    if (!user) return undefined;
    const checkoutState = (searchParams.get('checkout') || '').trim().toLowerCase();
    if (checkoutState === 'cancelled') {
      setError('Checkout was canceled. Choose a plan whenever you are ready.');
      return undefined;
    }
    if (checkoutState !== 'success') return undefined;

    let cancelled = false;
    let timeoutId;
    let attempts = 0;
    setError('');
    setLoading('paid');

    const syncCheckoutState = async () => {
      attempts += 1;
      const refreshedUser = await refreshUser();
      if (cancelled) return;
      if (refreshedUser?.plan_selected !== false) {
        setLoading('');
        navigate(postAuthDestination(refreshedUser), { replace: true });
        return;
      }
      if (attempts >= 5) {
        setLoading('');
        setError('Payment was received, but we are still confirming the subscription. Please refresh in a moment.');
        return;
      }
      timeoutId = window.setTimeout(syncCheckoutState, 1500);
    };

    syncCheckoutState();
    return () => {
      cancelled = true;
      if (timeoutId) window.clearTimeout(timeoutId);
    };
  }, [navigate, refreshUser, searchParams, user]);

  const selectPlan = async (mode) => {
    setError('');
    setLoading(mode);
    try {
      const res = await api.post('/auth/billing/plan', { mode });
      if (res.data?.checkout_url) {
        window.location.assign(res.data.checkout_url);
        return;
      }
      const { token, user: u } = res.data || {};
      if (token && u) setAuthFromOAuth(u, token);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Could not save plan');
    } finally {
      setLoading('');
    }
  };

  if (!user) return null;

  return (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(180deg, #f8fafc 0%, #eff6ff 100%)', fontFamily: F }}>
      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9' }}>
        <div style={{ maxWidth: 960, margin: '0 auto', padding: '0 24px', height: 58, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '48px 24px 80px' }}>
        <h1 style={{ fontSize: 28, fontWeight: 800, color: '#0f172a', marginBottom: 8 }}>Choose your plan</h1>
        <p style={{ fontSize: 15, color: '#64748b', marginBottom: 28, lineHeight: 1.6 }}>
          Complete this step to unlock your workspace. Start with a 30-day free trial or continue to a paid subscription when you are ready for full production access.
        </p>
        {error && (
          <div style={{ marginBottom: 16, padding: 12, borderRadius: 12, background: '#fef2f2', color: '#b91c1c', fontSize: 14 }}>
            {error}
          </div>
        )}
        <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
          <button
            type="button"
            disabled={!!loading}
            onClick={() => selectPlan('free_trial')}
            style={{
              textAlign: 'left',
              padding: 22,
              borderRadius: 18,
              border: '1.5px solid rgba(37,99,235,0.35)',
              background: '#fff',
              cursor: loading ? 'wait' : 'pointer',
              boxShadow: '0 12px 32px rgba(37,99,235,0.12)',
            }}
          >
            <Sparkles size={22} color="#2563eb" style={{ marginBottom: 10 }} />
            <div style={{ fontSize: 18, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>30-day free trial</div>
            <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.55, marginBottom: 14 }}>
              Explore and test the platform for one month with no payment required. Use this trial to evaluate the system before committing to a paid plan.
            </p>
            <span style={{ fontSize: 13, fontWeight: 700, color: '#2563eb' }}>{loading === 'free_trial' ? 'Starting trial...' : 'Start 30-day trial ->'}</span>
          </button>
          <button
            type="button"
            disabled={!!loading}
            onClick={() => selectPlan('paid')}
            style={{
              textAlign: 'left',
              padding: 22,
              borderRadius: 18,
              border: '1px solid rgba(148,163,184,0.35)',
              background: '#fff',
              cursor: loading ? 'wait' : 'pointer',
            }}
          >
            <CreditCard size={22} color="#0f172a" style={{ marginBottom: 10 }} />
            <div style={{ fontSize: 18, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>Paid plan</div>
            <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.55, marginBottom: 14 }}>
              Get full access to all platform features with active billing, production support, and no trial-period restrictions.
            </p>
            <span style={{ fontSize: 13, fontWeight: 700, color: '#334155' }}>{loading === 'paid' ? 'Opening checkout...' : 'Continue to Payment ->'}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
