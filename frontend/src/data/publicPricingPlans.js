/**
 * Canonical public pricing for landing, /pricing, and signup.
 * Starter (free) · Pro ($29/mo, self-serve) · Enterprise ($99/mo, self-serve)
 */

export const PUBLIC_PRICING_PLANS = [
  {
    code: 'starter',
    name: 'Starter',
    contactSales: false,
    cardPrice: '$0',
    cardPeriod: '/month',
    signupPriceTag: 'Free',
    description: 'Everything you need to get started with AI-powered conversations.',
    features: [
      'Up to 500 conversations / month',
      'WhatsApp & Web Chat integration',
      'AI-assisted replies (50 / month)',
      'Basic lead capture & contact profiles',
      'Shared team inbox (1 user)',
      'Email support',
      '30-day free trial on Pro features',
    ],
  },
  {
    code: 'pro',
    name: 'Pro',
    contactSales: false,
    cardPrice: '$29',
    cardPeriod: '/month',
    signupPriceTag: '$29/mo',
    description: 'Everything growing teams need to run omnichannel conversations at scale.',
    features: [
      'Up to 2,500 conversations / month',
      'WhatsApp & Instagram integrations',
      'Unlimited AI-assisted replies',
      'Lead scoring, capturing & nurture journeys',
      'Shared team inbox with collision detection',
      'Post-conversation follow-up sequences',
      'Real-time analytics dashboard',
      'Priority onboarding & standard support',
      '1 team member seat',
    ],
  },
  {
    code: 'enterprise',
    name: 'Enterprise',
    contactSales: false,
    cardPrice: '$99',
    cardPeriod: '/month',
    signupPriceTag: '$99/mo',
    description: 'Higher limits, all channels, and priority support for teams operating at serious volume.',
    features: [
      'Up to 10,000 conversations / month',
      'All channels: WhatsApp, Instagram, Facebook, Email & Web Chat',
      'Advanced AI Agents (support, sales & nurturing)',
      'BANT lead scoring + opportunity prediction',
      'SLA-aware workflows & supervisor controls',
      'Full customer history & identity unification',
      'Multi-step automation sequences',
      'Revenue & performance analytics',
      'Priority onboarding & faster response times',
      'Up to 3 user seats',
    ],
  },
];

/** Stripe / paid checkout signup column. */
export function getSignupStripePlanOptions() {
  return PUBLIC_PRICING_PLANS.map((p) => ({
    code: p.code,
    name: p.name,
    price: p.signupPriceTag,
    description: p.description,
    bullets: [...p.features],
    contactSales: Boolean(p.contactSales),
  }));
}

/** Trial-mode signup: same product surface. */
export function getSignupTrialPlanOptions() {
  return PUBLIC_PRICING_PLANS.map((p) => ({
    code: p.code,
    name: p.name,
    price: p.code === 'starter' ? 'Free' : `${p.cardPrice}/mo after trial`,
    description: p.description,
    bullets: [...p.features],
    contactSales: Boolean(p.contactSales),
  }));
}

/** Cards for LandingPage `#pricing` section. */
export function getLandingPricingPlans() {
  return PUBLIC_PRICING_PLANS.map((p) => ({
    code: p.code,
    name: p.name,
    price: p.cardPrice,
    period: p.cardPeriod,
    description: p.description,
    features: [...p.features],
    cta: p.code === 'starter' ? 'Start Free — No Card Needed' : p.code === 'enterprise' ? 'Get Enterprise Access' : 'Start Free Trial',
    href: p.code === 'starter' ? '/signup?plan=starter' : `/signup?plan=${p.code}`,
    popular: p.code === 'pro',
  }));
}
