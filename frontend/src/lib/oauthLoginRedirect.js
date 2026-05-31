const SIGNUP_PREFILL_NEXT_PATH = '/__signup_oauth_prefill__';

export function buildOAuthRedirectUrl(backendBaseUrl, provider, nextPath = '/dashboard') {
  const base = (backendBaseUrl || '').replace(/\/$/, '');
  const params = new URLSearchParams({
    frontend_origin: window.location.origin,
    next: nextPath,
  });
  return `${base}/api/auth/${provider}?${params.toString()}`;
}

/**
 * Same OAuth redirect as Sign in: gateway /api/auth/{provider} with next=/dashboard.
 */
export function startOAuthLoginRedirect(backendBaseUrl, provider) {
  window.location.href = buildOAuthRedirectUrl(backendBaseUrl, provider, '/dashboard');
}

/**
 * Signup OAuth verifies the provider account and returns profile fields to pre-fill the form.
 */
export function startOAuthSignupPrefillRedirect(backendBaseUrl, provider) {
  window.location.href = buildOAuthRedirectUrl(backendBaseUrl, provider, SIGNUP_PREFILL_NEXT_PATH);
}
