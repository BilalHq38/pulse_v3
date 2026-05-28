import { Suspense, lazy, useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { postAuthDestination } from '@/lib/auth-gates';
import { API_BASE_URL } from '@/lib/backend-url';
import Layout from '@/components/Layout';
import { Toaster } from '@/components/ui/toaster';
import '@/App.css';

const LandingPage = lazy(() => import('@/pages/LandingPage'));
const SignInPage = lazy(() => import('@/pages/SignInPage'));
const SignUpPage = lazy(() => import('@/pages/SignUpPage'));
const AdminLoginPage = lazy(() => import('@/pages/AdminLoginPage'));
const SignupCompletePage = lazy(() => import('@/pages/SignupCompletePage'));
const AuthCallback = lazy(() => import('@/pages/AuthCallback'));
const DashboardPage = lazy(() => import('@/pages/DashboardPage'));
const InboxPage = lazy(() => import('@/pages/InboxPage'));
const LeadsPage = lazy(() => import('@/pages/LeadsPage'));
const CampaignsPage = lazy(() => import('@/pages/CampaignsPage'));
const CustomersPage = lazy(() => import('@/pages/CustomersPage'));
const AnalyticsPage = lazy(() => import('@/pages/AnalyticsPage'));
const TicketsPage = lazy(() => import('@/pages/TicketsPage'));
const KnowledgeBasePage = lazy(() => import('@/pages/KnowledgeBasePage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const UnificationPage = lazy(() => import('@/pages/UnificationPage'));
const ProductsPage = lazy(() => import('@/pages/ProductsPage'));
const ProductDetailPage = lazy(() => import('@/pages/ProductDetailPage'));
const OrdersPage = lazy(() => import('@/pages/OrdersPage'));
const PrivacyPage = lazy(() => import('@/pages/PrivacyPage'));
const TermsPage = lazy(() => import('@/pages/TermsPage'));
const ContactPage = lazy(() => import('@/pages/ContactPage'));
const PricingPage = lazy(() => import('@/pages/PricingPage'));
const EmailVerificationPage = lazy(() => import('@/pages/EmailVerificationPage'));
const InvitationAcceptancePage = lazy(() => import('@/pages/InvitationAcceptancePage'));
const UnauthorizedPage = lazy(() => import('@/pages/UnauthorizedPage'));
const ErrorPage = lazy(() => import('@/pages/ErrorPage'));
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'));
const SuperAdminDashboardPage = lazy(() => import('@/pages/SuperAdminDashboardPage'));
const ProfilePage = lazy(() => import('@/pages/ProfilePage'));
const OnboardingPage = lazy(() => import('@/pages/OnboardingPage'));
const BillingPlanPage = lazy(() => import('@/pages/BillingPlanPage'));
const WidgetDemoPage = lazy(() => import('@/pages/WidgetDemoPage'));
const AccountStatusPage = lazy(() => import('@/pages/AccountStatusPage'));

function defaultRouteForUser(user) {
  return postAuthDestination(user);
}

function Spinner() {
  return <div className="min-h-screen bg-slate-50 flex items-center justify-center"><div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div></div>;
}

const VISITOR_COOKIE = 'pulse_visitor_session';

function readCookie(name) {
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`))
    ?.split('=')
    .slice(1)
    .join('=') || '';
}

function writeVisitorCookie(value) {
  document.cookie = `${VISITOR_COOKIE}=${encodeURIComponent(value)}; Max-Age=31536000; Path=/; SameSite=Lax`;
}

function visitorSessionId() {
  const existing = decodeURIComponent(readCookie(VISITOR_COOKIE) || '');
  if (existing) return existing;
  const generated = `vis_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
  writeVisitorCookie(generated);
  return generated;
}

function doNotTrackEnabled() {
  return navigator.doNotTrack === '1' || window.doNotTrack === '1';
}

function isPublicTrackingPath(pathname) {
  return ['/', '/pricing', '/contact', '/privacy', '/terms', '/signup', '/signin', '/admin/login', '/widget-demo'].includes(pathname);
}

function safeTrackingUrl() {
  const url = new URL(window.location.href);
  const safe = new URL(`${url.origin}${url.pathname}`);
  ['utm_source', 'utm_medium', 'utm_campaign'].forEach((key) => {
    const value = url.searchParams.get(key);
    if (value) safe.searchParams.set(key, value.slice(0, 120));
  });
  return safe.toString();
}

function safeTrackingPath(pathname) {
  const params = new URLSearchParams(window.location.search);
  const safeParams = new URLSearchParams();
  ['utm_source', 'utm_medium', 'utm_campaign'].forEach((key) => {
    const value = params.get(key);
    if (value) safeParams.set(key, value.slice(0, 120));
  });
  const query = safeParams.toString();
  return query ? `${pathname}?${query}` : pathname;
}

function visitorMetadata() {
  const ua = navigator.userAgent || '';
  const browser = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Other';
  const os = /Windows/i.test(ua) ? 'Windows' : /Mac OS/i.test(ua) ? 'macOS' : /Android/i.test(ua) ? 'Android' : /iPhone|iPad/i.test(ua) ? 'iOS' : /Linux/i.test(ua) ? 'Linux' : 'Other';
  const device = /Mobi|Android|iPhone/i.test(ua) ? 'mobile' : /iPad|Tablet/i.test(ua) ? 'tablet' : 'desktop';
  return {
    title: document.title || '',
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    browser,
    os,
    device,
    utm_source: new URLSearchParams(window.location.search).get('utm_source') || '',
    utm_medium: new URLSearchParams(window.location.search).get('utm_medium') || '',
    utm_campaign: new URLSearchParams(window.location.search).get('utm_campaign') || '',
  };
}

function VisitorTracker() {
  const location = useLocation();
  const { user } = useAuth();
  const sessionIdRef = useRef('');

  useEffect(() => {
    if (!user && !doNotTrackEnabled() && isPublicTrackingPath(location.pathname)) {
      sessionIdRef.current = visitorSessionId();
    }
  }, [location.pathname, user]);

  useEffect(() => {
    if (user || doNotTrackEnabled() || !isPublicTrackingPath(location.pathname)) return;
    if (!sessionIdRef.current) sessionIdRef.current = visitorSessionId();
    const payload = {
      session_id: sessionIdRef.current,
      event_type: 'page_view',
      page_url: safeTrackingUrl(),
      path: safeTrackingPath(location.pathname),
      referrer: document.referrer || '',
      metadata: visitorMetadata(),
    };
    fetch(`${API_BASE_URL}/visitor/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      keepalive: true,
      body: JSON.stringify(payload),
    }).catch(() => {});
  }, [location.pathname, location.search, user]);

  return null;
}

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const locationParams = new URLSearchParams(location.search);
  const isOnboardingInviteRoute =
    location.pathname === '/settings'
    && locationParams.get('tab') === 'users'
    && locationParams.get('onboarding') === '1';
  if (loading) return <Spinner />;
  if (!user) return <Navigate to="/signin" replace />;
  const status = user.account_status || user.status || 'active';
  if (['blocked', 'paused', 'inactive', 'rejected'].includes(status)) return <Navigate to="/account-status" replace />;
  if (user.role !== 'super_admin' && user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return <Navigate to={`/verify-email${q}`} replace />;
  }
  if (user.role !== 'super_admin' && user.onboarding_completed === false) {
    return <Navigate to="/onboarding" replace />;
  }
  if (user.role !== 'super_admin' && user.enterprise_invite_gate_pending) {
    return <Navigate to="/onboarding" replace />;
  }
  if (user.role !== 'super_admin' && user.plan_selected === false && !isOnboardingInviteRoute) {
    return <Navigate to="/billing" replace />;
  }
  if (user.role !== 'super_admin' && status === 'pending_approval') return <Navigate to="/account-status" replace />;
  return <Layout>{children}</Layout>;
}

function RoleRoute({ children, allowedRoles }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner />;
  if (!user) return <Navigate to="/signin" replace />;
  if (!allowedRoles.includes(user.role)) return <Navigate to="/unauthorized" replace />;
  if (user.role === 'super_admin') return <Layout>{children}</Layout>;
  const status = user.account_status || user.status || 'active';
  if (['blocked', 'paused', 'inactive', 'rejected'].includes(status)) return <Navigate to="/account-status" replace />;
  if (user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return <Navigate to={`/verify-email${q}`} replace />;
  }
  if (user.onboarding_completed === false) return <Navigate to="/onboarding" replace />;
  if (user.enterprise_invite_gate_pending) return <Navigate to="/onboarding" replace />;
  if (user.plan_selected === false) return <Navigate to="/billing" replace />;
  if (status === 'pending_approval') return <Navigate to="/account-status" replace />;
  return <Layout>{children}</Layout>;
}

function PublicRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner />;
  if (user) return <Navigate to={defaultRouteForUser(user)} replace />;
  return children;
}

function DashboardWithAuthCheck() {
  const { user, loading } = useAuth();
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session_id');
  if (sessionId && !user && !loading) return <AuthCallback />;
  if (loading) return <Spinner />;
  if (!user) return <Navigate to="/signin" replace />;
  if (user.role === 'super_admin') return <Navigate to="/super-admin" replace />;
  const status = user.account_status || user.status || 'active';
  if (['blocked', 'paused', 'inactive', 'rejected'].includes(status)) return <Navigate to="/account-status" replace />;
  if (user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return <Navigate to={`/verify-email${q}`} replace />;
  }
  if (user.onboarding_completed === false) return <Navigate to="/onboarding" replace />;
  if (user.enterprise_invite_gate_pending) return <Navigate to="/onboarding" replace />;
  if (user.plan_selected === false) return <Navigate to="/billing" replace />;
  if (status === 'pending_approval') return <Navigate to="/account-status" replace />;
  return <Layout><DashboardPage /></Layout>;
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <VisitorTracker />
        <Suspense fallback={<Spinner />}>
          <Routes>
            <Route path="/" element={<PublicRoute><LandingPage /></PublicRoute>} />
            <Route path="/pricing" element={<PricingPage />} />
            <Route path="/contact" element={<ContactPage />} />
            <Route path="/verify-email" element={<EmailVerificationPage />} />
            <Route path="/accept-invite" element={<InvitationAcceptancePage />} />
            <Route path="/admin/login" element={<PublicRoute><AdminLoginPage /></PublicRoute>} />
            <Route path="/signin" element={<PublicRoute><SignInPage /></PublicRoute>} />
            <Route path="/signup" element={<PublicRoute><SignUpPage /></PublicRoute>} />
            <Route path="/signup/complete" element={<PublicRoute><SignupCompletePage /></PublicRoute>} />
            <Route path="/auth/callback" element={<AuthCallback />} />
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route path="/billing" element={<BillingPlanPage />} />
            <Route path="/account-status" element={<AccountStatusPage />} />
            <Route path="/widget-demo" element={<WidgetDemoPage />} />
            <Route path="/c/:companySlug/product/:productSlug" element={<ProductDetailPage />} />
            <Route path="/dashboard" element={<DashboardWithAuthCheck />} />
            <Route path="/inbox" element={<ProtectedRoute><InboxPage /></ProtectedRoute>} />
            <Route path="/leads" element={<ProtectedRoute><LeadsPage /></ProtectedRoute>} />
            <Route path="/campaigns" element={<ProtectedRoute><CampaignsPage /></ProtectedRoute>} />
            <Route path="/customers" element={<ProtectedRoute><CustomersPage /></ProtectedRoute>} />
            <Route path="/analytics" element={<ProtectedRoute><AnalyticsPage /></ProtectedRoute>} />
            <Route path="/tickets" element={<ProtectedRoute><TicketsPage /></ProtectedRoute>} />
            <Route path="/knowledge-base" element={<ProtectedRoute><KnowledgeBasePage /></ProtectedRoute>} />
            <Route path="/products" element={<ProtectedRoute><ProductsPage /></ProtectedRoute>} />
            <Route path="/orders" element={<ProtectedRoute><OrdersPage /></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
            <Route path="/unification" element={<RoleRoute allowedRoles={['admin']}><UnificationPage /></RoleRoute>} />
            <Route path="/profile" element={<ProtectedRoute><ProfilePage /></ProtectedRoute>} />
            <Route path="/super-admin" element={<RoleRoute allowedRoles={['super_admin']}><SuperAdminDashboardPage /></RoleRoute>} />
            <Route path="/privacy" element={<PrivacyPage />} />
            <Route path="/terms" element={<TermsPage />} />
            <Route path="/unauthorized" element={<UnauthorizedPage />} />
            <Route path="/500" element={<ErrorPage />} />
            <Route path="/login" element={<Navigate to="/signin" replace />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Suspense>
        <Toaster />
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
