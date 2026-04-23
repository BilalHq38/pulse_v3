import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { postAuthDestination } from '@/lib/auth-gates';
import Layout from '@/components/Layout';
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

function defaultRouteForUser(user) {
  return postAuthDestination(user);
}

function Spinner() {
  return <div className="min-h-screen bg-slate-50 flex items-center justify-center"><div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div></div>;
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
  return <Layout>{children}</Layout>;
}

function RoleRoute({ children, allowedRoles }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner />;
  if (!user) return <Navigate to="/signin" replace />;
  if (!allowedRoles.includes(user.role)) return <Navigate to="/unauthorized" replace />;
  if (user.role === 'super_admin') return <Layout>{children}</Layout>;
  if (user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return <Navigate to={`/verify-email${q}`} replace />;
  }
  if (user.onboarding_completed === false) return <Navigate to="/onboarding" replace />;
  if (user.enterprise_invite_gate_pending) return <Navigate to="/onboarding" replace />;
  if (user.plan_selected === false) return <Navigate to="/billing" replace />;
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
  if (user.email_verified === false) {
    const q = user.email ? `?${new URLSearchParams({ email: user.email }).toString()}` : '';
    return <Navigate to={`/verify-email${q}`} replace />;
  }
  if (user.onboarding_completed === false) return <Navigate to="/onboarding" replace />;
  if (user.enterprise_invite_gate_pending) return <Navigate to="/onboarding" replace />;
  if (user.plan_selected === false) return <Navigate to="/billing" replace />;
  return <Layout><DashboardPage /></Layout>;
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
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
            <Route path="/widget-demo" element={<WidgetDemoPage />} />
            <Route path="/dashboard" element={<DashboardWithAuthCheck />} />
            <Route path="/inbox" element={<ProtectedRoute><InboxPage /></ProtectedRoute>} />
            <Route path="/leads" element={<ProtectedRoute><LeadsPage /></ProtectedRoute>} />
            <Route path="/campaigns" element={<ProtectedRoute><CampaignsPage /></ProtectedRoute>} />
            <Route path="/customers" element={<ProtectedRoute><CustomersPage /></ProtectedRoute>} />
            <Route path="/analytics" element={<ProtectedRoute><AnalyticsPage /></ProtectedRoute>} />
            <Route path="/tickets" element={<ProtectedRoute><TicketsPage /></ProtectedRoute>} />
            <Route path="/knowledge-base" element={<ProtectedRoute><KnowledgeBasePage /></ProtectedRoute>} />
            <Route path="/products" element={<ProtectedRoute><ProductsPage /></ProtectedRoute>} />
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
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
