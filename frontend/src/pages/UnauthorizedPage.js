import { Link } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import { ShieldX, Home, ArrowLeft, Lock, LogIn } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function UnauthorizedPage() {
  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', flexDirection: 'column' }} data-testid="unauthorized-page">
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -80, right: -40, width: 350, height: 350, background: 'radial-gradient(circle, rgba(245,158,11,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -100, left: 30, width: 300, height: 300, background: 'radial-gradient(circle, rgba(37,99,235,0.05) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>

      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', zIndex: 1, padding: 24 }}>
        <div style={{ textAlign: 'center', maxWidth: 480 }}>
          <div style={{ width: 80, height: 80, borderRadius: 20, background: 'linear-gradient(135deg, #fffbeb, #fef3c7)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px', boxShadow: '0 8px 30px rgba(245,158,11,0.12)', animation: 'shieldPulse 2s ease-in-out infinite' }}>
            <ShieldX size={36} color="#f59e0b" />
          </div>

          <div style={{ display: 'inline-block', padding: '6px 16px', borderRadius: 100, background: 'linear-gradient(135deg, rgba(245,158,11,0.08), rgba(245,158,11,0.04))', border: '1px solid rgba(245,158,11,0.15)', marginBottom: 16 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#d97706' }}>Error 403</span>
          </div>

          <h1 style={{ fontSize: 32, fontWeight: 800, color: '#0f172a', letterSpacing: '-0.5px', lineHeight: 1.2, marginBottom: 10 }}>Access Denied</h1>
          <p style={{ fontSize: 15, color: '#64748b', lineHeight: 1.6, maxWidth: 380, margin: '0 auto 24px' }}>
            You don't have permission to view this resource. This area requires elevated privileges.
          </p>

          <div style={{ padding: '16px 20px', background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', marginBottom: 28, maxWidth: 360, margin: '0 auto 28px', boxShadow: '0 2px 8px rgba(0,0,0,0.03)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <Lock size={16} color="#64748b" />
              <span style={{ fontSize: 13, fontWeight: 600, color: '#475569' }}>What you can do:</span>
            </div>
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {[
                'Go back to your dashboard',
                'Contact your admin for access',
                'Sign in with a different account',
              ].map((text, i) => (
                <li key={i} style={{ fontSize: 13, color: '#64748b', padding: '6px 0', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#cbd5e1', flexShrink: 0 }} />
                  {text}
                </li>
              ))}
            </ul>
          </div>

          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link to="/dashboard" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, textDecoration: 'none', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)' }}>
              <Home size={16} /> Dashboard
            </Link>
            <Link to="/signin" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, textDecoration: 'none', fontFamily: F }}>
              <LogIn size={16} /> Switch Account
            </Link>
            <button onClick={() => window.history.back()} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F }}>
              <ArrowLeft size={16} /> Go Back
            </button>
          </div>
        </div>
      </div>
      <style>{`@keyframes shieldPulse{0%,100%{transform:scale(1)}50%{transform:scale(1.05)}}`}</style>
    </div>
  );
}
