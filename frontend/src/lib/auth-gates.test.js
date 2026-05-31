import { postAuthDestination } from './auth-gates';

const activePaidUser = {
  role: 'admin',
  status: 'active',
  email_verified: true,
  onboarding_completed: true,
  plan_selected: true,
  company: {
    plan: 'pro',
    subscription_status: 'active',
    billing_status: 'active',
    stripe_subscription_id: 'sub_123',
  },
};

test('routes active paid users to dashboard', () => {
  expect(postAuthDestination(activePaidUser)).toBe('/dashboard');
});

test('routes active free users to dashboard', () => {
  expect(
    postAuthDestination({
      ...activePaidUser,
      company: {
        plan: 'free',
        subscription_status: 'inactive',
        billing_status: 'inactive',
        stripe_subscription_id: '',
      },
    }),
  ).toBe('/dashboard');
});

test('routes canceled paid users to billing', () => {
  expect(
    postAuthDestination({
      ...activePaidUser,
      company: {
        ...activePaidUser.company,
        subscription_status: 'canceled',
      },
    }),
  ).toBe('/billing');
});

test('routes unpaid users to billing', () => {
  expect(postAuthDestination({ ...activePaidUser, plan_selected: false })).toBe('/billing');
});
