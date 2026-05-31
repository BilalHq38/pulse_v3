import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { postAuthDestination } from '@/lib/auth-gates';
import PlatformLogo from '@/components/PlatformLogo';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
const defaultRouteForUser = (user) => postAuthDestination(user);

export default function AuthCallback() {
  const { setAuthFromOAuth } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState('');

  const clearCallbackHash = () => {
    if (window.location.hash) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
    }
  };

  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
    const getParam = (key) => hashParams.get(key) || searchParams.get(key) || '';
    const sessionId = getParam('session_id');
    const code = getParam('code');
    const state = getParam('state');
    const provider = (getParam('provider') || 'google').toLowerCase();
    const directToken = getParam('token');
    const callbackError = getParam('error');
    let cancelled = false;

    (async () => {
      try {
        if (callbackError) {
          if (!cancelled) setError(callbackError);
          clearCallbackHash();
          return;
        }

        if (directToken) {
          let resolvedToken = directToken;
          let user = {
            id: getParam('id') || '',
            email: getParam('email') || '',
            name: getParam('name') || '',
            role: getParam('role') || 'admin',
            avatar: getParam('avatar') || '',
            company_id: getParam('company_id') || '',
            onboarding_completed: getParam('onboarding_completed') !== 'false',
            plan_selected: getParam('plan_selected') !== 'false',
            auth_provider: getParam('auth_provider') || 'email',
          };
          if (!user.email || !user.company_id) {
            const sessionRes = await api.post('/auth/session', {}, {
              headers: { Authorization: `Bearer ${directToken}` },
            });
            resolvedToken = sessionRes.data?.token || resolvedToken;
            if (sessionRes.data?.user) {
              user = sessionRes.data.user;
            }
          }
          if (!user.email || !user.company_id) {
            if (!cancelled) setError('Authentication response missing user data.');
            return;
          }
          setAuthFromOAuth(user, resolvedToken);
          const dest = defaultRouteForUser(user);
          const nextPath = (getParam('next') || '').trim();
          if (
            nextPath.startsWith('/') &&
            !nextPath.startsWith('//') &&
            dest === '/dashboard' &&
            nextPath !== '/dashboard'
          ) {
            navigate(nextPath, { replace: true });
          } else {
            navigate(dest, { replace: true });
          }
          clearCallbackHash();
          return;
        }

        let res;
        if (sessionId) {
          res = await api.post('/auth/session', { session_id: sessionId });
        } else if (code) {
          const endpoint = provider === 'facebook' ? '/auth/facebook/callback' : '/auth/google/callback';
          res = await api.post(endpoint, { code, state });
        } else {
          if (!cancelled) setError('No authentication token provided.');
          return;
        }

        const { user, token } = res.data || {};
        if (user && token) {
          setAuthFromOAuth(user, token);
          const dest = defaultRouteForUser(user);
          const nextPath = (getParam('next') || '').trim();
          if (
            nextPath.startsWith('/') &&
            !nextPath.startsWith('//') &&
            dest === '/dashboard' &&
            nextPath !== '/dashboard'
          ) {
            navigate(nextPath, { replace: true });
          } else {
            navigate(dest, { replace: true });
          }
          clearCallbackHash();
        } else if (!cancelled) {
          setError('Authentication response missing user data.');
        }
      } catch (err) {
        if (cancelled) return;
        const status = err.response?.status;
        const d = err.response?.data?.detail;
        const detailStr =
          typeof d === 'string'
            ? d
            : Array.isArray(d)
              ? d.map((e) => e?.msg || JSON.stringify(e)).join(', ')
              : d != null
                ? JSON.stringify(d)
                : '';
        const gatewayErr = String(err.response?.data?.error || '');
        if (
          status === 403 &&
          (d === 'OAUTH_REQUIRES_SIGNUP' ||
            detailStr.includes('OAUTH_REQUIRES_SIGNUP') ||
            gatewayErr.includes('OAUTH_REQUIRES_SIGNUP'))
        ) {
          navigate('/signup?oauth_error=no_account', { replace: true });
          clearCallbackHash();
          return;
        }
        if (
          status === 409 &&
          (d === 'OAUTH_ACCOUNT_LINK_REQUIRED' || detailStr === 'OAUTH_ACCOUNT_LINK_REQUIRED')
        ) {
          navigate('/signin?oauth_error=link_required', { replace: true });
          clearCallbackHash();
          return;
        }
        const msg =
          gatewayErr ||
          (typeof d === 'string' ? d : Array.isArray(d) ? d.map((e) => e?.msg || e).join(', ') : '') ||
          'Authentication failed. Please try again.';
        setError(msg);
      }
    })();

    return () => { cancelled = true; };
  }, [location.key, location.pathname, location.hash, searchParams, navigate, setAuthFromOAuth]);

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center' }} data-testid="auth-callback-page">
      <div style={{ textAlign: 'center', maxWidth: 400, padding: 24 }}>
        <div style={{ marginBottom: 20 }}>
          <PlatformLogo to={null} />
        </div>
        {error ? (
          <>
            <h2 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Authentication Failed</h2>
            <p style={{ fontSize: 14, color: '#ef4444', lineHeight: 1.6, marginBottom: 20 }}>{error}</p>
            <button onClick={() => navigate('/signin', { replace: true })} style={{ padding: '12px 28px', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)' }}>
              Back to Sign In
            </button>
          </>
        ) : (
          <>
            <div style={{ width: 32, height: 32, border: '3px solid #e2e8f0', borderTopColor: '#2563eb', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 16px' }} />
            <h2 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', marginBottom: 6 }}>Authenticating...</h2>
            <p style={{ fontSize: 14, color: '#64748b' }}>Please wait while we verify your identity</p>
          </>
        )}
      </div>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );
}
