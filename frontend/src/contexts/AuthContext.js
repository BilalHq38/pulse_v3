import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import api, { clearAuthSession, clearAccessToken, refreshAuthSession, setAccessToken } from '@/lib/api';
import { normalizeAvatarUrl } from '@/lib/avatar';
import { startActivityTracking, stopActivityTracking, isSessionExpiredByInactivity, resetActivity } from '@/lib/activityTracker';

const AuthContext = createContext(null);

// Statuses that should NOT retain an authenticated session after refresh
const BLOCKED_STATUSES = ['pending_approval', 'rejected', 'blocked'];

// Refresh at 80% of access-token TTL so the default 15-minute token refreshes at 12 minutes.
const DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 15 * 60;
const ACCESS_TOKEN_TTL_SECONDS = Number(process.env.REACT_APP_ACCESS_TOKEN_TTL_SECONDS || DEFAULT_ACCESS_TOKEN_TTL_SECONDS);
const REFRESH_INTERVAL_MS = Math.max(60 * 1000, Math.floor(ACCESS_TOKEN_TTL_SECONDS * 1000 * 0.8));

function safeUserForStorage(user) {
  if (!user) return null;
  // Only store display-safe fields — sensitive fields (company_id, role, billing_status,
  // token/access_token, id fields) are kept in memory only and must be re-fetched from
  // the server on each session via refreshAuthSession / /auth/session.
  return {
    name: user.name,
    email: user.email,
    avatar: user.avatar || user.profile_picture || null,
  };
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const refreshTimerRef = useRef(null);

  const saveAvatar = useCallback((rawAvatar) => {
    const avatar = normalizeAvatarUrl(rawAvatar || '');
    if (avatar) localStorage.setItem('pe_avatar', avatar);
    else localStorage.removeItem('pe_avatar');
    return avatar;
  }, []);

  const _clearLocalAuth = useCallback(() => {
    clearAccessToken();
    localStorage.removeItem('pe_user');
    localStorage.removeItem('pe_avatar');
    localStorage.removeItem('pe_company_name');
    localStorage.removeItem('pe_account_status');
    stopActivityTracking();
    if (refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const _setAuthenticatedUser = useCallback((userData, token) => {
    if (token) setAccessToken(token, { user: userData });
    localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(userData)));
    setUser(userData);
    saveAvatar(userData?.avatar || '');
  }, [saveAvatar]);

  const _startSilentRefreshTimer = useCallback(() => {
    if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    refreshTimerRef.current = setInterval(async () => {
      // Do not refresh if user has been inactive for 6 hours
      if (isSessionExpiredByInactivity()) return;
      try {
        const refreshed = await refreshAuthSession();
        if (refreshed?.token) setAccessToken(refreshed.token, { user: refreshed.user });
        if (refreshed?.user) {
          localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(refreshed.user)));
          setUser(refreshed.user);
          saveAvatar(refreshed.user.avatar || '');
        }
      } catch {
        // Silently ignore — interceptor will handle 401 on next request
      }
    }, REFRESH_INTERVAL_MS);
  }, [saveAvatar]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Full user state is fetched from the server here — the localStorage copy
        // stores only display-safe fields and must NOT be used for sensitive decisions.
        const refreshed = await refreshAuthSession();
        if (cancelled) return;

        // Guard: if the account is pending approval, blocked, or rejected,
        // sign out immediately — do not persist the session.
        const accountStatus = refreshed?.user?.account_status || refreshed?.user?.status || '';
        if (refreshed?.user && BLOCKED_STATUSES.includes(accountStatus)) {
          _clearLocalAuth();
          localStorage.setItem('pe_account_status', accountStatus);
          setUser(null);
          setLoading(false);
          if (!window.location.pathname.startsWith('/account-status')) {
            window.location.replace('/account-status');
          }
          return;
        }

        if (refreshed?.token) setAccessToken(refreshed.token, { user: refreshed.user });
        if (refreshed?.user) {
          localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(refreshed.user)));
          setUser(refreshed.user);
          saveAvatar(refreshed.user.avatar || '');
          // Start inactivity tracking and silent background refresh
          startActivityTracking(() => {
            // 6-hour inactivity: sign out
            _clearLocalAuth();
            setUser(null);
          });
          _startSilentRefreshTimer();
        }
        if (refreshed?.user && !localStorage.getItem('pe_avatar')) {
          api.get('/settings/personal').then(res => {
            const av = res.data?.avatar;
            saveAvatar(av || '');
          }).catch(() => {});
        }
      } catch {
        if (cancelled) return;
        _clearLocalAuth();
        setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [saveAvatar, _clearLocalAuth, _startSilentRefreshTimer]);

  // While authenticated, wire resetActivity() to interaction events so the
  // 6-hour idle clock resets on any real user action (mousemove included here
  // in addition to the events tracked inside activityTracker itself).
  useEffect(() => {
    if (!user) return;
    const handler = () => resetActivity();
    const events = ['mousemove', 'keydown', 'click'];
    events.forEach(evt => window.addEventListener(evt, handler, { passive: true }));
    return () => events.forEach(evt => window.removeEventListener(evt, handler));
  }, [user]);

  // Cleanup refresh timer on unmount
  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    };
  }, []);

  const login = useCallback(async (email, password, workspace = '') => {
    const res = await api.post('/auth/login', { email, password, workspace });
    const { token, user: userData } = res.data;
    _setAuthenticatedUser(userData, token);
    startActivityTracking(() => {
      _clearLocalAuth();
      setUser(null);
    });
    _startSilentRefreshTimer();
    return userData;
  }, [_setAuthenticatedUser, _clearLocalAuth, _startSilentRefreshTimer]);

  const adminLogin = useCallback(async (email, password) => {
    const res = await api.post('/admin/login', { email, password });
    const { token, user: userData } = res.data;
    setAccessToken(token, { user: userData, storage: 'session' });
    localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(userData)));
    setUser(userData);
    localStorage.removeItem('pe_avatar');
    return userData;
  }, []);

  const register = useCallback(async (data) => {
    const res = await api.post('/auth/register', data);
    const { token, user: userData, email_verification } = res.data;

    // Accounts requiring email verification should not be treated as signed-in yet.
    if (!email_verification?.required && token && userData) {
      _setAuthenticatedUser(userData, token);
    }

    return res.data;
  }, [_setAuthenticatedUser]);

  const setAuthFromOAuth = useCallback((userData, token) => {
    _setAuthenticatedUser(userData, token);
    startActivityTracking(() => {
      _clearLocalAuth();
      setUser(null);
    });
    _startSilentRefreshTimer();
  }, [_setAuthenticatedUser, _clearLocalAuth, _startSilentRefreshTimer]);

  const setAuthFromGoogle = useCallback((userData, token) => {
    setAuthFromOAuth(userData, token);
  }, [setAuthFromOAuth]);

  const setOnboardingComplete = useCallback(() => {
    setUser(prev => {
      if (!prev) return prev;
      const updated = { ...prev, onboarding_completed: true };
      localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(updated)));
      return updated;
    });
  }, []);

  const logout = useCallback(async () => {
    try {
      await clearAuthSession();
    } catch {
      /* clearAuthSession already clears local state */
    }
    _clearLocalAuth();
    setUser(null);
  }, [_clearLocalAuth]);

  const refreshUser = useCallback(async () => {
    try {
      const res = await api.post('/auth/session', {});
      const data = res.data || {};
      if (data.token) setAccessToken(data.token, { user: data.user });
      if (data.user) {
        localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(data.user)));
        setUser(data.user);
        saveAvatar(data.user.avatar || '');
      }
      return data.user || null;
    } catch {
      return null;
    }
  }, [saveAvatar]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        adminLogin,
        register,
        logout,
        setAuthFromOAuth,
        setAuthFromGoogle,
        setOnboardingComplete,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
