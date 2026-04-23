/**
 * Canonical public pricing for landing, /pricing, and signup.
 * Pro / Enterprise are self-serve checkout; Custom is sales-led (Contact Sales).
 */

export const PUBLIC_PRICING_PLANS = [
  {
    code: 'pro',
    name: 'Pro',
    contactSales: false,
    cardPrice: '$29',
    cardPeriod: '/month',
    signupPriceTag: '$29/mo',
    description: 'Everything growing teams need to run omnichannel conversations in one place.',
    features: [
      'Conversation limits — up to 2,500 conversations / month',
      'Channel integrations — WhatsApp, Instagram',
      'Lead capturing & nurturing — capture from chats with starter nurture journeys',
      'Customer conversation handling — shared inbox, assignments, and AI-assisted replies',
      'Post-conversation follow-ups — reminders and reusable follow-up templates',
      'Onboarding support — guided setup resources and standard support',
      'Team member limits — 1 user',
    ],
  },
  {
    code: 'enterprise',
    name: 'Enterprise',
    contactSales: false,
    cardPrice: '$99',
    cardPeriod: '/month',
    signupPriceTag: '$99/mo',
    description: 'Higher limits and priority support for teams operating at serious volume.',
    features: [
      'Conversation limits — up to 10,000 conversations / month',
      'Channel integrations — WhatsApp, Instagram, Email, Web Chat (unified inbox)',
      'Lead capturing & nurturing — scoring, segments, and nurture playbooks',
      'Customer conversation handling — SLA-minded workflows, supervisors, full customer history',
      'Post-conversation follow-ups — multi-step sequences and structured handoffs',
      'Onboarding support — priority onboarding with faster response times',
      'Team member limits — up to 3 users',
    ],
  },
  {
    code: 'custom',
    name: 'Custom',
    contactSales: true,
    cardPrice: 'Custom',
    cardPeriod: '',
    signupPriceTag: 'Custom',
    description: 'Tailored limits, integrations, and rollout for complex or high-volume programs.',
    features: [
      'Conversation limits — negotiated for your regions and peak traffic',
      'Channel integrations — full suite plus alignment with your existing tools',
      'Lead capturing & nurturing — pipelines and motions matched to your sales process',
      'Customer conversation handling — governance, routing, and reporting your stakeholders need',
      'Post-conversation follow-ups — bespoke automation and executive visibility',
      'Onboarding support — white-glove onboarding and ongoing success alignment',
      'Team member limits — flexible seats, roles, and expansion as you grow',
    ],
  },
];

/** Stripe / paid checkout signup column (includes `contactSales` for Custom card). */
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

/** Trial-mode signup: same product surface; Custom remains Contact Sales only. */
export function getSignupTrialPlanOptions() {
  return PUBLIC_PRICING_PLANS.map((p) => ({
    code: p.code,
    name: p.name,
    price: p.contactSales ? 'Custom' : `${p.cardPrice}/mo after trial`,
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
    cta: p.contactSales ? 'Contact Sales' : 'Continue to Secure Checkout',
    href: p.contactSales ? '/contact' : `/signup?plan=${p.code}`,
    popular: p.code === 'pro',
  }));
}
