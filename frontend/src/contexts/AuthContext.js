import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api, { clearAuthSession, clearAccessToken, refreshAuthSession, setAccessToken } from '@/lib/api';
import { normalizeAvatarUrl } from '@/lib/avatar';

const AuthContext = createContext(null);

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
  const saveAvatar = useCallback((rawAvatar) => {
    const avatar = normalizeAvatarUrl(rawAvatar || '');
    if (avatar) localStorage.setItem('pe_avatar', avatar);
    else localStorage.removeItem('pe_avatar');
    return avatar;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Full user state is fetched from the server here — the localStorage copy
        // stores only display-safe fields and must NOT be used for sensitive decisions.
        // TODO: Fetch full user state from /auth/me on init if refreshAuthSession is unavailable.
        const refreshed = await refreshAuthSession();
        if (cancelled) return;
        if (refreshed?.token) {
          setAccessToken(refreshed.token);
        }
        if (refreshed?.user) {
          localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(refreshed.user)));
          setUser(refreshed.user);
          saveAvatar(refreshed.user.avatar || '');
        }
        if (refreshed?.user && !localStorage.getItem('pe_avatar')) {
          api.get('/settings/personal').then(res => {
            const av = res.data?.avatar;
            saveAvatar(av || '');
          }).catch(() => {});
        }
      } catch {
        if (cancelled) return;
        clearAccessToken();
        localStorage.removeItem('pe_user');
        localStorage.removeItem('pe_avatar');
        localStorage.removeItem('pe_company_name');
        setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [saveAvatar]);

  const login = useCallback(async (email, password, workspace = '') => {
    const res = await api.post('/auth/login', { email, password, workspace });
    const { token, user: userData } = res.data;
    setAccessToken(token);
    localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(userData)));
    setUser(userData);
    saveAvatar(userData?.avatar || '');
    return userData;
  }, [saveAvatar]);

  const adminLogin = useCallback(async (email, password) => {
    const res = await api.post('/admin/login', { email, password });
    const { token, user: userData } = res.data;
    setAccessToken(token);
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
      setAccessToken(token);
      localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(userData)));
      setUser(userData);
    }

    return res.data;
  }, []);

  const setAuthFromOAuth = useCallback((userData, token) => {
    setAccessToken(token);
    localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(userData)));
    setUser(userData);
    saveAvatar(userData?.avatar || '');
  }, [saveAvatar]);

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
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const res = await api.post('/auth/session', {});
      const data = res.data || {};
      if (data.token) {
        setAccessToken(data.token);
      }
      if (data.user) {
        localStorage.setItem('pe_user', JSON.stringify(safeUserForStorage(data.user)));
        setUser(data.user);
        saveAvatar(data.user.avatar || '');
      }
      return data.user || null;
    } catch {
      return null;
      /* ignore */
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
