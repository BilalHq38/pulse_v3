/**
 * Post-login routing: email verification → onboarding → enterprise invite gate (if applicable)
 * → plan/billing → app. super_admin bypasses tenant enrollment.
 */
export function postAuthDestination(user) {
  if (!user) return '/signin';
  if (user.role === 'super_admin') return '/super-admin';
  const status = user.account_status || user.status || 'active';
  if (['blocked', 'paused', 'inactive', 'rejected'].includes(status)) return '/account-status';
  if (user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return `/verify-email${q}`;
  }
  if (user.onboarding_completed === false) return '/onboarding';
  if (user.enterprise_invite_gate_pending) return '/onboarding';
  if (user.plan_selected === false) return '/billing';
  if (status === 'pending_approval') return '/account-status';
  return '/dashboard';
}
