import { Link } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import { AlertTriangle, RefreshCw, Home, ArrowLeft, Wifi } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function ErrorPage() {
  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', flexDirection: 'column' }} data-testid="error-page">
      {/* Background blobs */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -120, right: 80, width: 400, height: 400, background: 'radial-gradient(circle, rgba(239,68,68,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -80, left: -30, width: 350, height: 350, background: 'radial-gradient(circle, rgba(37,99,235,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>

      {/* Header */}
      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>

      {/* Content */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', zIndex: 1, padding: '24px' }}>
        <div style={{ textAlign: 'center', maxWidth: 520 }}>
          {/* Animated icon */}
          <div style={{ width: 88, height: 88, borderRadius: 20, background: 'linear-gradient(135deg, #fef2f2, #fee2e2)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px', boxShadow: '0 8px 30px rgba(239,68,68,0.12)', animation: 'errorFloat 3s ease-in-out infinite' }}>
            <AlertTriangle size={40} color="#ef4444" />
          </div>

          <div style={{ display: 'inline-block', padding: '6px 16px', borderRadius: 100, background: 'linear-gradient(135deg, rgba(239,68,68,0.08), rgba(239,68,68,0.04))', border: '1px solid rgba(239,68,68,0.12)', marginBottom: 16 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#dc2626' }}>Error 500</span>
          </div>

          <h1 style={{ fontSize: 36, fontWeight: 800, color: '#0f172a', letterSpacing: '-0.5px', lineHeight: 1.2, marginBottom: 12 }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: 16, color: '#64748b', lineHeight: 1.6, marginBottom: 32, maxWidth: 400, margin: '0 auto 32px' }}>
            We're experiencing a temporary server issue. Our team has been notified and is working on fixing it.
          </p>

          {/* Quick diagnostics */}
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginBottom: 32, flexWrap: 'wrap' }}>
            {[
              { icon: Wifi, label: 'Server Issue', color: '#ef4444', bg: '#fef2f2' },
              { icon: RefreshCw, label: 'Auto-recovery', color: '#f59e0b', bg: '#fffbeb' },
            ].map((item, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 10, background: item.bg, border: `1px solid ${item.color}22` }}>
                <item.icon size={14} color={item.color} />
                <span style={{ fontSize: 12, color: item.color, fontWeight: 500 }}>{item.label}</span>
              </div>
            ))}
          </div>

          {/* Actions */}
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
            <button onClick={() => window.location.reload()} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)', transition: 'all 0.2s' }}>
              <RefreshCw size={16} /> Try Again
            </button>
            <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, textDecoration: 'none', fontFamily: F, transition: 'all 0.2s' }}>
              <Home size={16} /> Go Home
            </Link>
            <button onClick={() => window.history.back()} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F, transition: 'all 0.2s' }}>
              <ArrowLeft size={16} /> Go Back
            </button>
          </div>

          <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 40 }}>If this problem persists, please <Link to="/contact" style={{ color: '#2563eb', fontWeight: 500 }}>contact our support team</Link>.</p>
        </div>
      </div>
      <style>{`@keyframes errorFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}`}</style>
    </div>
  );
}
