import { buildOAuthRedirectUrl } from './oauthLoginRedirect';

test('buildOAuthRedirectUrl builds sign-in OAuth URL with dashboard next path', () => {
  const url = buildOAuthRedirectUrl('https://pulse-engine.dev/', 'google');

  expect(url).toBe(
    'https://pulse-engine.dev/api/auth/google?frontend_origin=http%3A%2F%2Flocalhost&next=%2Fdashboard',
  );
});

test('buildOAuthRedirectUrl builds signup prefill OAuth URL', () => {
  const url = buildOAuthRedirectUrl(
    'https://pulse-engine.dev',
    'google',
    '/__signup_oauth_prefill__',
  );

  expect(url).toBe(
    'https://pulse-engine.dev/api/auth/google?frontend_origin=http%3A%2F%2Flocalhost&next=%2F__signup_oauth_prefill__',
  );
});
