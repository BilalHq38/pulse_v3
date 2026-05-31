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

const ACCESS_TOKEN_STORAGE_KEY = 'pe_token';
const LEGACY_REFRESH_STORAGE_KEY = 'pe_refresh';

function safeStorage(storage) {
  try {
    const key = '__pe_storage_probe__';
    storage.setItem(key, '1');
    storage.removeItem(key);
    return storage;
  } catch {
    return null;
  }
}

const localTokenStorage = typeof window !== 'undefined' ? safeStorage(window.localStorage) : null;
const sessionTokenStorage = typeof window !== 'undefined' ? safeStorage(window.sessionStorage) : null;

function parseJwtPayload(token) {
  try {
    const [, payload] = String(token || '').split('.');
    if (!payload) return {};
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), '=');
    return JSON.parse(window.atob(padded));
  } catch {
    return {};
  }
}

function storedAccessToken() {
  const sessionToken = sessionTokenStorage?.getItem(ACCESS_TOKEN_STORAGE_KEY) || '';
  if (sessionToken) return sessionToken;

  const localToken = localTokenStorage?.getItem(ACCESS_TOKEN_STORAGE_KEY) || '';
  if (String(parseJwtPayload(localToken).role || '').toLowerCase() === 'super_admin') {
    localTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    return '';
  }
  return localToken;
}

let accessToken = storedAccessToken();
let refreshPromise = null;
const MIN_ERROR_DISPLAY_DELAY_MS = 3000;
const RETRYABLE_GET_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);

function delay(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function isRetryableGetError(error) {
  const config = error?.config || {};
  const method = String(config.method || 'get').toLowerCase();
  if (method !== 'get' || config.skipGuardedRetry || config._guardedRetry) return false;
  const status = Number(error?.response?.status || 0);
  return !status || RETRYABLE_GET_STATUSES.has(status);
}

async function retryGetAfterMinimumLoadWindow(error) {
  const config = error.config;
  config._guardedRetry = true;
  const startedAt = Number(config.metadata?.startedAt || Date.now());
  const elapsed = Date.now() - startedAt;
  const retryDelayMs = 500;
  const waitMs = Math.max(retryDelayMs, MIN_ERROR_DISPLAY_DELAY_MS - elapsed);
  await delay(waitMs);
  config.metadata = { ...(config.metadata || {}), startedAt: Date.now() };
  return api.request(config);
}

function isFormDataPayload(data) {
  return typeof FormData !== 'undefined' && data instanceof FormData;
}

function removeContentTypeHeader(headers) {
  if (!headers) return;
  if (typeof headers.delete === 'function') {
    headers.delete('Content-Type');
    headers.delete('content-type');
    return;
  }
  Object.keys(headers).forEach((key) => {
    if (key.toLowerCase() === 'content-type') delete headers[key];
  });
}

function clearCachedUser() {
  localTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  localTokenStorage?.removeItem(LEGACY_REFRESH_STORAGE_KEY);
  sessionTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  sessionTokenStorage?.removeItem(LEGACY_REFRESH_STORAGE_KEY);
  localStorage.removeItem('pe_user');
  localStorage.removeItem('pe_avatar');
  localStorage.removeItem('pe_company_name');
  localStorage.removeItem('pe_account_status');
}

export function applyAuthResponse(data = {}) {
  if (data?.token) {
    setAccessToken(data.token, { user: data.user });
  }
  if (data?.user) {
    localStorage.removeItem('pe_account_status');
    localStorage.setItem('pe_user', JSON.stringify(data.user));
  }
  return data;
}

export function setAccessToken(token, options = {}) {
  accessToken = token || '';
  localTokenStorage?.removeItem(LEGACY_REFRESH_STORAGE_KEY);
  sessionTokenStorage?.removeItem(LEGACY_REFRESH_STORAGE_KEY);
  if (!accessToken) {
    localTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    sessionTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    window.dispatchEvent(new CustomEvent('pe-access-token-updated', { detail: { token: accessToken } }));
    return;
  }

  const payloadRole = String(parseJwtPayload(accessToken).role || '').toLowerCase();
  const userRole = String(options.user?.role || options.role || '').toLowerCase();
  const storageScope = options.storage || (userRole === 'super_admin' || payloadRole === 'super_admin' ? 'session' : 'local');

  if (storageScope === 'session') {
    sessionTokenStorage?.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken);
    localTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  } else {
    localTokenStorage?.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken);
    sessionTokenStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  }
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
  config.metadata = { ...(config.metadata || {}), startedAt: Date.now() };
  if (isFormDataPayload(config.data)) {
    removeContentTypeHeader(config.headers);
  }
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
    const code = error.response?.data?.code;
    if (error.response?.status === 403 && typeof d === 'string') {
      if (
        ['ACCOUNT_PENDING_APPROVAL', 'ACCOUNT_REJECTED', 'ACCOUNT_BLOCKED', 'ACCOUNT_PAUSED', 'ACCOUNT_INACTIVE'].includes(code)
        || /account (is pending|request was not approved|has been blocked|has been paused|is inactive)/i.test(d)
      ) {
        const statusByCode = {
          ACCOUNT_PENDING_APPROVAL: 'pending_approval',
          ACCOUNT_REJECTED: 'rejected',
          ACCOUNT_BLOCKED: 'blocked',
          ACCOUNT_PAUSED: 'paused',
          ACCOUNT_INACTIVE: 'inactive',
        };
        if (statusByCode[code]) {
          try {
            const cached = JSON.parse(localStorage.getItem('pe_user') || '{}');
            localStorage.setItem('pe_user', JSON.stringify({ ...cached, status: statusByCode[code], account_status: statusByCode[code] }));
            localStorage.setItem('pe_account_status', statusByCode[code]);
          } catch {
            // Ignore malformed local cache.
          }
        }
        if (!window.location.pathname.startsWith('/account-status')) window.location.assign('/account-status');
        return Promise.reject(error);
      }
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
    if (isRetryableGetError(error)) {
      return retryGetAfterMinimumLoadWindow(error);
    }
    return Promise.reject(error);
  }
);

export default api;
