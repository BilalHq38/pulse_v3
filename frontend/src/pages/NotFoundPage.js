import { Link } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import { Search, Home, ArrowLeft, Compass } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function NotFoundPage() {
  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', flexDirection: 'column' }} data-testid="not-found-page">
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -100, left: '30%', width: 400, height: 400, background: 'radial-gradient(circle, rgba(99,102,241,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -80, right: '20%', width: 300, height: 300, background: 'radial-gradient(circle, rgba(37,99,235,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>

      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', zIndex: 1, padding: 24 }}>
        <div style={{ textAlign: 'center', maxWidth: 520 }}>
          {/* Large 404 */}
          <div style={{ position: 'relative', marginBottom: 24 }}>
            <span style={{ fontSize: 140, fontWeight: 900, background: 'linear-gradient(135deg,#e2e8f0,#cbd5e1)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', lineHeight: 1, letterSpacing: '-4px', display: 'block' }}>404</span>
            <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: 64, height: 64, borderRadius: 16, background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 8px 30px rgba(37,99,235,0.3)', animation: 'searchBob 2.5s ease-in-out infinite' }}>
              <Search size={28} color="#fff" />
            </div>
          </div>

          <h1 style={{ fontSize: 28, fontWeight: 800, color: '#0f172a', letterSpacing: '-0.5px', lineHeight: 1.2, marginBottom: 10 }}>Page not found</h1>
          <p style={{ fontSize: 15, color: '#64748b', lineHeight: 1.6, marginBottom: 32, maxWidth: 400, margin: '0 auto 32px' }}>
            The page you're looking for doesn't exist or has been moved. Let's get you back on track.
          </p>

          {/* Suggested links */}
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginBottom: 28, flexWrap: 'wrap' }}>
            {[
              { to: '/dashboard', label: 'Dashboard', icon: Compass },
              { to: '/inbox', label: 'Inbox', icon: Search },
              { to: '/', label: 'Home', icon: Home },
            ].map((link, i) => (
              <Link key={i} to={link.to} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 18px', borderRadius: 10, background: '#fff', border: '1px solid #e2e8f0', color: '#475569', fontSize: 13, fontWeight: 500, textDecoration: 'none', transition: 'all 0.2s', boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}>
                <link.icon size={15} /> {link.label}
              </Link>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
            <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, textDecoration: 'none', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)' }}>
              <Home size={16} /> Back to Home
            </Link>
            <button onClick={() => window.history.back()} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 14, fontWeight: 500, cursor: 'pointer', fontFamily: F }}>
              <ArrowLeft size={16} /> Go Back
            </button>
          </div>
        </div>
      </div>
      <style>{`@keyframes searchBob{0%,100%{transform:translate(-50%,-50%) rotate(0deg)}25%{transform:translate(-50%,-55%) rotate(-5deg)}75%{transform:translate(-50%,-45%) rotate(5deg)}}`}</style>
    </div>
  );
}
