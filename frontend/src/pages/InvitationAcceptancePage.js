import { useState, useEffect } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import { Lock, User, Eye, EyeOff, ArrowRight, UserPlus, CheckCircle } from 'lucide-react';
import api from '@/lib/api';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function InvitationAcceptancePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [token, setToken] = useState(() => {
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
    return (hashParams.get('token') || searchParams.get('token') || '').trim();
  });
  const email = searchParams.get('email') || '';

  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
    const nextToken = (hashParams.get('token') || searchParams.get('token') || '').trim();
    if (hashParams.has('token')) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
    }
    setToken(nextToken);
  }, [searchParams]);

  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');
  const [redirectTarget, setRedirectTarget] = useState('/signin');

  const inputStyle = { width: '100%', padding: '10px 14px 10px 38px', fontSize: 14, border: '1.5px solid #e2e8f0', borderRadius: 10, outline: 'none', background: '#fff', color: '#0f172a', transition: 'all 0.2s', boxSizing: 'border-box', fontFamily: F };
  const focusIn = (e) => { e.target.style.borderColor = '#2563eb'; e.target.style.boxShadow = '0 0 0 3px rgba(37,99,235,0.1)'; };
  const focusOut = (e) => { e.target.style.borderColor = '#e2e8f0'; e.target.style.boxShadow = 'none'; };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { setError('Please enter your name'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return; }
    setError(''); setLoading(true);
    try {
      const res = await api.post('/auth/invitations/accept', { token, name: name.trim(), password });
      const verification = res.data?.email_verification;
      if (verification?.required) {
        const params = new URLSearchParams({
          email: res.data?.email || email,
          cooldown: String(verification.resend_available_in_seconds || 120),
          attempts: String(verification.remaining_attempts ?? 2),
          locked: verification.locked ? '1' : '0',
          lock: String(verification.lock_remaining_seconds || 0),
        });
        const verificationPath = `/verify-email?${params.toString()}`;
        setRedirectTarget(verificationPath);
        setSuccessMessage(
          res.data?.message || 'Invitation accepted. Verify your email to continue joining the workspace.'
        );
        setSuccess(true);
        setTimeout(() => navigate(verificationPath, { replace: true }), 2000);
        return;
      }
      setRedirectTarget('/signin');
      setSuccessMessage(res.data?.message || 'Your account is ready. Redirecting you to sign in...');
      setSuccess(true);
      setTimeout(() => navigate('/signin', { replace: true }), 2000);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to accept invitation. The link may be invalid or expired.');
    } finally { setLoading(false); }
  };

  if (!token) {
    return (
      <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', flexDirection: 'column' }}>
        <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9' }}>
          <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center' }}>
            <PlatformLogo />
          </div>
        </header>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Invalid Invitation Link</h2>
            <p style={{ fontSize: 14, color: '#64748b', marginBottom: 20 }}>This invitation link is missing or malformed.</p>
            <Link to="/signin" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 24px', borderRadius: 10, background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, textDecoration: 'none', fontFamily: F }}>Sign In Instead</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F, display: 'flex', flexDirection: 'column' }} data-testid="invitation-page">
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -100, right: 0, width: 400, height: 400, background: 'radial-gradient(circle, rgba(99,102,241,0.07) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>

      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center' }}>
          <PlatformLogo />
        </div>
      </header>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', zIndex: 1, padding: 24 }}>
        <div style={{ width: '100%', maxWidth: 420 }}>
          <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', padding: '36px 28px', boxShadow: '0 8px 30px rgba(0,0,0,0.06)' }}>
            {success ? (
              <div style={{ textAlign: 'center' }}>
                <div style={{ width: 64, height: 64, borderRadius: 16, background: 'linear-gradient(135deg,#ecfdf5,#d1fae5)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
                  <CheckCircle size={28} color="#22c55e" />
                </div>
                <h2 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', marginBottom: 6 }}>Welcome aboard!</h2>
                <p style={{ fontSize: 14, color: '#64748b', lineHeight: 1.6 }}>{successMessage || 'Your account is ready. Redirecting you to sign in...'}</p>
                <div style={{ marginTop: 16, height: 3, background: '#f1f5f9', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: '100%', background: 'linear-gradient(135deg,#22c55e,#16a34a)', borderRadius: 4, animation: 'progressShrink 2s linear forwards' }} />
                </div>
                <button
                  type="button"
                  onClick={() => navigate(redirectTarget, { replace: true })}
                  style={{ marginTop: 16, display: 'inline-flex', alignItems: 'center', gap: 8, padding: '11px 20px', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F }}
                >
                  {redirectTarget.startsWith('/verify-email') ? 'Continue to Verification' : 'Continue to Sign In'}
                  <ArrowRight size={16} />
                </button>
              </div>
            ) : (
              <>
                <div style={{ textAlign: 'center', marginBottom: 24 }}>
                  <div style={{ width: 56, height: 56, borderRadius: 14, background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px', boxShadow: '0 4px 12px rgba(37,99,235,0.3)' }}>
                    <UserPlus size={24} color="#fff" />
                  </div>
                  <h2 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Accept Your Invitation</h2>
                  <p style={{ fontSize: 13, color: '#64748b' }}>You've been invited to join a team on Pulse Engine</p>
                  {email && <p style={{ fontSize: 13, color: '#2563eb', fontWeight: 500, marginTop: 4 }}>{email}</p>}
                </div>

                {error && (
                  <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, fontSize: 13, color: '#dc2626' }}>{error}</div>
                )}

                <form onSubmit={handleSubmit}>
                  <div style={{ marginBottom: 14 }}>
                    <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Your Name</label>
                    <div style={{ position: 'relative' }}>
                      <User size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Enter your full name" style={inputStyle} onFocus={focusIn} onBlur={focusOut} required />
                    </div>
                  </div>
                  <div style={{ marginBottom: 20 }}>
                    <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Create Password</label>
                    <div style={{ position: 'relative' }}>
                      <Lock size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                      <input type={showPw ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Min 8 characters" style={{ ...inputStyle, paddingRight: 38 }} onFocus={focusIn} onBlur={focusOut} required />
                      <button type="button" onClick={() => setShowPw(!showPw)} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                        {showPw ? <EyeOff size={15} color="#94a3b8" /> : <Eye size={15} color="#94a3b8" />}
                      </button>
                    </div>
                  </div>
                  <button type="submit" disabled={loading} style={{ width: '100%', padding: '12px 0', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: loading ? 'wait' : 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: loading ? 0.7 : 1 }}>
                    {loading ? 'Joining...' : <><span>Join Team</span><ArrowRight size={16} /></>}
                  </button>
                </form>
              </>
            )}
          </div>
          <p style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8', marginTop: 16 }}>Already have an account? <Link to="/signin" style={{ color: '#2563eb', fontWeight: 500 }}>Sign in</Link></p>
        </div>
      </div>
      <style>{`@keyframes progressShrink{from{width:100%}to{width:0}}`}</style>
    </div>
  );
}
