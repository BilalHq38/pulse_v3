import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Shield, Mail, Lock, Eye, EyeOff, ArrowRight, AlertCircle } from 'lucide-react';

import PlatformLogo from '@/components/PlatformLogo';
import { useAuth } from '@/contexts/AuthContext';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const inputStyle = {
  width: '100%',
  padding: '10px 14px 10px 38px',
  fontSize: 14,
  border: '1.5px solid #d7dce5',
  borderRadius: 10,
  outline: 'none',
  background: '#fff',
  color: '#0f172a',
  transition: 'all 0.2s',
  boxSizing: 'border-box',
  fontFamily: F,
};

const focusIn = (e) => {
  e.target.style.borderColor = '#0f766e';
  e.target.style.boxShadow = '0 0 0 3px rgba(15,118,110,0.12)';
};

const focusOut = (e) => {
  e.target.style.borderColor = '#d7dce5';
  e.target.style.boxShadow = 'none';
};

export default function AdminLoginPage() {
  const navigate = useNavigate();
  const { adminLogin } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setLoading(true);
    try {
      const userData = await adminLogin(email, password);
      if (userData?.role !== 'super_admin') {
        setError('This account does not have platform admin access.');
        return;
      }
      navigate('/super-admin', { replace: true });
    } catch (err) {
      setError(err.response?.data?.detail || 'Admin authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(180deg, #f6fffb 0%, #eef2ff 100%)', fontFamily: F }}>
      <header style={{ position: 'sticky', top: 0, zIndex: 10, background: 'rgba(255,255,255,0.9)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #e2e8f0' }}>
        <div style={{ maxWidth: 1120, margin: '0 auto', padding: '0 24px', height: 64, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 13, color: '#64748b' }}>Workspace login</span>
            <Link to="/signin" style={{ fontSize: 13, fontWeight: 600, color: '#0f766e', textDecoration: 'none', padding: '7px 14px', borderRadius: 999, border: '1px solid rgba(15,118,110,0.18)', background: 'rgba(15,118,110,0.05)' }}>
              User Sign In
            </Link>
          </div>
        </div>
      </header>

      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '56px 24px 72px', display: 'grid', gap: 32, gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
        <section style={{ alignSelf: 'center' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 999, background: 'rgba(15,118,110,0.08)', color: '#0f766e', fontSize: 12, fontWeight: 700, letterSpacing: 0.3, textTransform: 'uppercase' }}>
            <Shield size={14} />
            Platform Control Plane
          </div>
          <h1 style={{ fontSize: 40, lineHeight: 1.1, fontWeight: 800, color: '#0f172a', margin: '18px 0 14px' }}>
            Separate super-admin access for the platform layer.
          </h1>
          <p style={{ fontSize: 16, lineHeight: 1.7, color: '#475569', maxWidth: 620 }}>
            This login is isolated from tenant user sign-in and is intended only for platform operators. Super-admin sessions are routed through the dedicated admin service and use separate admin APIs.
          </p>
          <div style={{ marginTop: 24, display: 'grid', gap: 12 }}>
            {[
              'Dedicated admin authentication route at /admin/login',
              'Platform-wide logs, sessions, and user controls served from the admin service',
              'Super-admin accounts are blocked from the standard tenant sign-in flow',
            ].map((item) => (
              <div key={item} style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#0f172a', fontSize: 14 }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0f766e' }} />
                <span>{item}</span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <form onSubmit={handleSubmit} style={{ background: '#fff', borderRadius: 24, border: '1px solid rgba(148,163,184,0.18)', padding: 28, boxShadow: '0 18px 48px rgba(15,23,42,0.08)' }} data-testid="admin-login-form">
            <div style={{ width: 56, height: 56, borderRadius: 18, background: 'linear-gradient(135deg,#ccfbf1,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 18 }}>
              <Shield size={28} color="#0f766e" />
            </div>
            <h2 style={{ fontSize: 24, fontWeight: 800, color: '#0f172a', marginBottom: 6 }}>Admin Sign In</h2>
            <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.6, marginBottom: 20 }}>
              Use your super-admin account to access platform-wide oversight tools.
            </p>

            {error && (
              <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                <AlertCircle size={15} color="#dc2626" />
                <span style={{ fontSize: 13, color: '#dc2626' }}>{error}</span>
              </div>
            )}

            <div style={{ display: 'grid', gap: 16 }}>
              <div>
                <label style={{ display: 'block', marginBottom: 6, fontSize: 12, fontWeight: 600, color: '#475569' }}>Admin Email</label>
                <div style={{ position: 'relative' }}>
                  <Mail size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="admin@pulseengine.com"
                    style={inputStyle}
                    onFocus={focusIn}
                    onBlur={focusOut}
                    required
                  />
                </div>
              </div>

              <div>
                <label style={{ display: 'block', marginBottom: 6, fontSize: 12, fontWeight: 600, color: '#475569' }}>Password</label>
                <div style={{ position: 'relative' }}>
                  <Lock size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter your admin password"
                    style={{ ...inputStyle, paddingRight: 42 }}
                    onFocus={focusIn}
                    onBlur={focusOut}
                    required
                  />
                  <button type="button" onClick={() => setShowPassword((value) => !value)} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}>
                    {showPassword ? <EyeOff size={15} color="#94a3b8" /> : <Eye size={15} color="#94a3b8" />}
                  </button>
                </div>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              style={{ width: '100%', marginTop: 18, padding: '13px 18px', borderRadius: 12, border: 'none', background: 'linear-gradient(135deg,#0f766e,#0f172a)', color: '#fff', fontSize: 14, fontWeight: 700, cursor: loading ? 'wait' : 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, boxShadow: '0 14px 24px rgba(15,118,110,0.18)' }}
            >
              {loading ? 'Signing In...' : <>Enter Admin Panel <ArrowRight size={16} /></>}
            </button>

            <p style={{ marginTop: 16, fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
              Need regular tenant access instead? Use the <Link to="/signin" style={{ color: '#0f766e', fontWeight: 700, textDecoration: 'none' }}>standard sign-in page</Link>.
            </p>
          </form>
        </section>
      </div>
    </div>
  );
}
