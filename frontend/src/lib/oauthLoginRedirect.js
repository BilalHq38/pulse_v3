/**
 * Same OAuth redirect as Sign in: gateway /api/auth/{provider} with next=/dashboard.
 */
export function startOAuthLoginRedirect(backendBaseUrl, provider) {
  const base = (backendBaseUrl || '').replace(/\/$/, '');
  const params = new URLSearchParams({
    frontend_origin: window.location.origin,
    next: '/dashboard',
  });
  window.location.href = `${base}/api/auth/${provider}?${params.toString()}`;
}
