import { Link } from 'react-router-dom';
import { AlertCircle, Clock, PauseCircle, ShieldOff } from 'lucide-react';

import PlatformLogo from '@/components/PlatformLogo';
import { useAuth } from '@/contexts/AuthContext';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const STATUS_COPY = {
  pending_approval: {
    title: 'Pending approval',
    message: 'Your account is pending admin approval. You will get access to Pulse Engine after Super Admin verification.',
    icon: Clock,
    color: '#d97706',
    bg: '#fffbeb',
  },
  rejected: {
    title: 'Request not approved',
    message: 'Your account request was not approved. Please contact the administrator for more information.',
    icon: ShieldOff,
    color: '#dc2626',
    bg: '#fef2f2',
  },
  blocked: {
    title: 'Account blocked',
    message: 'Your account has been blocked. Please contact your administrator to restore access.',
    icon: ShieldOff,
    color: '#dc2626',
    bg: '#fef2f2',
  },
  paused: {
    title: 'Account paused',
    message: 'Your account has been paused. Please contact your administrator.',
    icon: PauseCircle,
    color: '#d97706',
    bg: '#fffbeb',
  },
  inactive: {
    title: 'Account inactive',
    message: 'Your account is inactive. Please contact your administrator.',
    icon: AlertCircle,
    color: '#64748b',
    bg: '#f8fafc',
  },
};

export function accountStatusCopy(status) {
  return STATUS_COPY[status] || STATUS_COPY.pending_approval;
}

export default function AccountStatusPage() {
  const { user, logout } = useAuth();
  const status = user?.account_status || user?.status || sessionStorage.getItem('pe_account_status') || 'pending_approval';
  const copy = accountStatusCopy(status);
  const Icon = copy.icon;

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }}>
      <header style={{ borderBottom: '1px solid #e2e8f0', background: '#fff' }}>
        <div style={{ maxWidth: 1120, margin: '0 auto', height: 64, padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo />
          <Link to="/signin" onClick={logout} style={{ color: '#2563eb', fontSize: 13, fontWeight: 700, textDecoration: 'none' }}>
            Sign out
          </Link>
        </div>
      </header>
      <main style={{ minHeight: 'calc(100vh - 64px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <section style={{ width: '100%', maxWidth: 480, background: '#fff', border: '1px solid #e2e8f0', borderRadius: 18, padding: 32, boxShadow: '0 18px 48px rgba(15,23,42,0.08)', textAlign: 'center' }} data-testid="account-status-page">
          <div style={{ width: 64, height: 64, borderRadius: 18, background: copy.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            <Icon size={30} color={copy.color} />
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#0f172a', marginBottom: 10 }}>{copy.title}</h1>
          <p style={{ fontSize: 14, lineHeight: 1.7, color: '#475569', margin: 0 }}>{copy.message}</p>
          {user?.email && (
            <p style={{ marginTop: 18, fontSize: 12, color: '#94a3b8' }}>{user.email}</p>
          )}
        </section>
      </main>
    </div>
  );
}
