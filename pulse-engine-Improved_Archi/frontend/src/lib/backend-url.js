function stripTrailingSlash(url) {
  return String(url || '').replace(/\/$/, '');
}

/** Browsers treat localhost and 127.0.0.1 as different sites; SameSite=Lax cookies won't cross them. */
function alignLoopbackApiOrigin(originStr) {
  const pageHost = window.location.hostname;
  if (!originStr) return originStr;
  try {
    const u = new URL(originStr);
    const h = u.hostname;
    const loopbackMismatch =
      (pageHost === 'localhost' && h === '127.0.0.1') || (pageHost === '127.0.0.1' && h === 'localhost');
    if (loopbackMismatch) {
      u.hostname = pageHost;
    }
    return stripTrailingSlash(u.origin);
  } catch {
    return stripTrailingSlash(originStr);
  }
}

function resolveBackendBaseUrl() {
  const configured = stripTrailingSlash(process.env.REACT_APP_BACKEND_URL);
  if (configured) {
    return alignLoopbackApiOrigin(configured);
  }

  return stripTrailingSlash(window.location.origin);
}

export const BACKEND_BASE_URL = resolveBackendBaseUrl();
export const API_BASE_URL = `${BACKEND_BASE_URL}/api`;

export function resolveMediaUrl(url) {
  const value = String(url || '').trim();
  if (!value) return '';
  if (value.startsWith('data:') || value.startsWith('blob:')) return value;
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith('/')) return `${BACKEND_BASE_URL}${value}`;
  return value;
}
