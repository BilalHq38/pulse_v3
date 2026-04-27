import axios from 'axios';
import { API_BASE_URL } from '@/lib/backend-url';

const REQUEST_TIMEOUT_MS = 20000;

const authClient = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
  timeout: REQUEST_TIMEOUT_MS,
});

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
  timeout: REQUEST_TIMEOUT_MS,
});

let accessToken = '';
let refreshPromise = null;

function clearCachedUser() {
  localStorage.removeItem('pe_token');
  localStorage.removeItem('pe_refresh');
  localStorage.removeItem('pe_user');
  localStorage.removeItem('pe_avatar');
  localStorage.removeItem('pe_company_name');
}

export function applyAuthResponse(data = {}) {
  if (data?.token) {
    setAccessToken(data.token);
  }
  if (data?.user) {
    localStorage.setItem('pe_user', JSON.stringify(data.user));
  }
  return data;
}

export function setAccessToken(token) {
  accessToken = token || '';
  localStorage.removeItem('pe_token');
  localStorage.removeItem('pe_refresh');
  window.dispatchEvent(new CustomEvent('pe-access-token-updated', { detail: { token: accessToken } }));
}

export function getAccessToken() {
  return accessToken;
}

export function clearAccessToken() {
  setAccessToken('');
}

export async function refreshAuthSession() {
  if (!refreshPromise) {
    refreshPromise = authClient
      .post('/auth/refresh', {})
      .then((res) => applyAuthResponse(res.data || {}))
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export async function clearAuthSession() {
  try {
    await api.post('/auth/logout', {}, { skipAuthRefresh: true });
  } catch {
    // Ignore logout failures; the local session state still needs clearing.
  }
  clearAccessToken();
  clearCachedUser();
}

api.interceptors.request.use((config) => {
  const token = accessToken;
  if (token) {
    config.headers = config.headers || {};
    if (!config.headers.Authorization) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const d = error.response?.data?.detail ?? error.response?.data?.error;
    if (error.response?.status === 403 && typeof d === 'string') {
      if (d === 'EMAIL_VERIFICATION_REQUIRED' && !window.location.pathname.startsWith('/verify-email')) {
        window.location.assign('/verify-email');
        return Promise.reject(error);
      }
      if (d === 'ENTERPRISE_INVITE_REQUIRED' && !window.location.pathname.startsWith('/onboarding')) {
        window.location.assign('/onboarding');
        return Promise.reject(error);
      }
      if (d === 'ONBOARDING_REQUIRED' && !window.location.pathname.startsWith('/onboarding')) {
        window.location.assign('/onboarding');
        return Promise.reject(error);
      }
      if (d === 'PLAN_SELECTION_REQUIRED' && !window.location.pathname.startsWith('/billing')) {
        window.location.assign('/billing');
        return Promise.reject(error);
      }
    }
    if (error.response?.status === 401) {
      const requestUrl = String(error.config?.url || '');
      const skipRedirect = [
        '/admin/login',
        '/auth/login',
        '/auth/register',
        '/auth/register/status',
        '/auth/session',
        '/auth/refresh',
        '/auth/logout',
        '/auth/google/callback',
        '/auth/facebook/callback',
        '/auth/forgot-password',
        '/auth/reset-password',
        '/auth/verify-email',
        '/auth/invitations/accept',
      ].some((prefix) => requestUrl.startsWith(prefix));

      if (!error.config?._retry && !error.config?.skipAuthRefresh && !skipRedirect) {
        error.config._retry = true;
        try {
          const refreshed = await refreshAuthSession();
          if (refreshed?.token) {
            error.config.headers = error.config.headers || {};
            error.config.headers.Authorization = `Bearer ${refreshed.token}`;
            return api.request(error.config);
          }
        } catch {
          await clearAuthSession();
          if (!window.location.pathname.startsWith('/sign') && !window.location.pathname.startsWith('/auth/callback')) {
            window.location.href = '/signin';
          }
        }
      } else {
        clearAccessToken();
        clearCachedUser();
      }
    }
    return Promise.reject(error);
  }
);

export default api;
