/**
 * Post-login routing: email verification → onboarding → enterprise invite gate (if applicable)
 * → plan/billing → app. super_admin bypasses tenant enrollment.
 *
 * @param {object} user
 * @param {{ skipBillingRedirect?: boolean }} [options]
 */
const ACTIVE_SUBSCRIPTION_STATUSES = new Set(['active', 'trialing']);
const BLOCKED_BILLING_STATUSES = new Set(['failed', 'past_due', 'unpaid']);
const FREE_PLAN_CODES = new Set(['free', 'starter']);

export function hasActiveSubscription(user) {
  if (!user || user.plan_selected === false) return false;
  const company = user.company || {};
  const plan = String(company.plan || user.subscription_plan_code || '').toLowerCase();
  const subscriptionStatus = String(company.subscription_status || user.subscription_status || '').toLowerCase();
  const billingStatus = String(company.billing_status || user.company_billing_status || '').toLowerCase();
  if (FREE_PLAN_CODES.has(plan)) return true;
  if (!ACTIVE_SUBSCRIPTION_STATUSES.has(subscriptionStatus)) return false;
  if (BLOCKED_BILLING_STATUSES.has(billingStatus)) return false;
  return true;
}

export function shouldRedirectToBilling(user, { skipBillingRedirect = false } = {}) {
  if (!user || user.role === 'super_admin' || skipBillingRedirect) return false;
  if (user.plan_selected === false) return true;
  return !hasActiveSubscription(user);
}

export function postAuthDestination(user, { skipBillingRedirect = false } = {}) {
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
  if (shouldRedirectToBilling(user, { skipBillingRedirect })) return '/billing';
  if (status === 'pending_approval') return '/account-status';
  return '/dashboard';
}

/**
 * Like postAuthDestination but skips the /billing redirect if the current URL
 * contains ?checkout=success (prevents a redirect loop on Stripe return).
 */
export function postAuthDestinationFromURL(user) {
  const isCheckoutReturn = typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('checkout') === 'success';
  return postAuthDestination(user, { skipBillingRedirect: isCheckoutReturn });
}
