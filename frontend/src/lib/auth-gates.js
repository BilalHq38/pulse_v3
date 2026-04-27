/**
 * Post-login routing: email verification → onboarding → enterprise invite gate (if applicable)
 * → plan/billing → app. super_admin bypasses tenant enrollment.
 */
export function postAuthDestination(user) {
  if (!user) return '/signin';
  if (user.role === 'super_admin') return '/super-admin';
  if (user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return `/verify-email${q}`;
  }
  if (user.onboarding_completed === false) return '/onboarding';
  if (user.enterprise_invite_gate_pending) return '/onboarding';
  if (user.plan_selected === false) return '/billing';
  return '/dashboard';
}
