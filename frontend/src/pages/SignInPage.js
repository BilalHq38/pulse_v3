import { useEffect, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { postAuthDestination } from '@/lib/auth-gates';
import { startOAuthLoginRedirect } from '@/lib/oauthLoginRedirect';
import PlatformLogo from '@/components/PlatformLogo';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { LogIn, Mail, Lock, Eye, EyeOff, ArrowLeft, CheckCircle, AlertCircle } from 'lucide-react';

const GoogleIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
    <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" fill="#4285F4"/>
    <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853"/>
    <path d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332Z" fill="#FBBC05"/>
    <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.163 6.656 3.58 9 3.58Z" fill="#EA4335"/>
  </svg>
);

const FacebookIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M24 12.073C24 5.404 18.627 0 12 0S0 5.404 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.41c0-3.025 1.792-4.697 4.532-4.697 1.312 0 2.686.236 2.686.236v2.97h-1.513c-1.491 0-1.956.93-1.956 1.886v2.268h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073Z" fill="#ffffff"/>
  </svg>
);

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const Header = () => (
  <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <PlatformLogo />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 13, color: '#64748b' }}>New here?</span>
        <Link to="/signup" style={{ fontSize: 13.5, fontWeight: 600, color: '#2563eb', textDecoration: 'none', padding: '7px 16px', borderRadius: 8, border: '1px solid rgba(37,99,235,0.2)', background: 'rgba(37,99,235,0.04)', transition: 'all 0.2s' }}>Sign Up</Link>
      </div>
    </div>
  </header>
);

const inputStyle = { width: '100%', padding: '10px 14px 10px 38px', fontSize: 14, border: '1.5px solid #e2e8f0', borderRadius: 10, outline: 'none', background: '#fff', color: '#0f172a', transition: 'all 0.2s', boxSizing: 'border-box', fontFamily: F };
const focusIn = (e) => { e.target.style.borderColor = '#2563eb'; e.target.style.boxShadow = '0 0 0 3px rgba(37,99,235,0.1)'; };
const focusOut = (e) => { e.target.style.borderColor = '#e2e8f0'; e.target.style.boxShadow = 'none'; };

export default function SignInPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [workspaceRequired, setWorkspaceRequired] = useState(false);
  const [workspaceOptions, setWorkspaceOptions] = useState([]);
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const initialHashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
  const initialMode = initialHashParams.get('mode') || searchParams.get('mode');
  const initialResetToken = (initialHashParams.get('token') || searchParams.get('token') || '').trim();

  const [forgotMode, setForgotMode] = useState(initialMode === 'reset' ? 'reset' : false);
  const [emailSent, setEmailSent] = useState(false);
  const [resetEmail, setResetEmail] = useState('');
  const [resetWorkspace, setResetWorkspace] = useState('');
  const [resetToken, setResetToken] = useState(initialResetToken);
  const [generatedToken, setGeneratedToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [resetSuccess, setResetSuccess] = useState('');
  const [resetError, setResetError] = useState('');
  const [resetLoading, setResetLoading] = useState(false);
  const backendBaseUrl = (process.env.REACT_APP_BACKEND_URL || window.location.origin).replace(/\/$/, '');
  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '');
    const mode = hashParams.get('mode') || searchParams.get('mode');
    const tokenFromUrl = (hashParams.get('token') || searchParams.get('token') || '').trim();
    if (mode === 'reset') {
      setForgotMode('reset');
      if (tokenFromUrl) {
        setResetToken(tokenFromUrl);
        // Remove token from the fragment so it is not visible after hydration
        navigate('/signin?mode=reset', { replace: true });
      }
    }
  }, [navigate, searchParams]);

  useEffect(() => {
    const oauthErr = searchParams.get('oauth_error');
    if (oauthErr === 'link_required') {
      setError(
        'That email already has a password account, or this Google/Facebook profile is not linked yet. Sign in with your email and password, then open Settings → Security → Linked sign-in to connect Google or Facebook.',
      );
      navigate('/signin', { replace: true });
    }
  }, [searchParams, navigate]);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError(''); setLoading(true);
    try {
      const userData = await login(email, password, workspace);
      setWorkspaceRequired(false);
      setWorkspaceOptions([]);
      navigate(postAuthDestination(userData), { replace: true });
    }
    catch (err) {
      const detail = err.response?.data?.detail;
      const code = err.response?.data?.code;
      if (err.response?.status === 409 && detail?.code === 'workspace_required') {
        setWorkspaceRequired(true);
        setWorkspaceOptions(Array.isArray(detail.workspaces) ? detail.workspaces : []);
        setError(detail.message || 'Please enter your workspace to continue.');
      } else if (code === 'ACCOUNT_PENDING_APPROVAL' || err.response?.data?.status === 'pending_approval') {
        localStorage.setItem('pe_account_status', 'pending_approval');
        navigate('/account-status', { replace: true });
      } else if (code === 'admin_login_required' || /\/admin\/login/i.test(String(detail || ''))) {
        setError('This account is a platform super admin. Use the dedicated admin sign-in page.');
      } else {
        setError(err.response?.data?.message || err.response?.data?.error || detail || 'Authentication failed');
      }
    }
    finally { setLoading(false); }
  };

  const startOAuth = (provider) => startOAuthLoginRedirect(backendBaseUrl, provider);

  const handleForgotPassword = async (e) => {
    e.preventDefault();
    setResetError(''); setResetLoading(true);
    try {
      await api.post('/auth/forgot-password', { email: resetEmail, workspace: resetWorkspace });
      setEmailSent(true);
    } catch (err) {
      const d = err.response?.data?.detail;
      setResetError(typeof d === 'string' ? d : Array.isArray(d) ? d.map(e => e.msg || e).join(', ') : 'Failed to request reset');
    } finally { setResetLoading(false); }
  };

  const handleResetPassword = async (e) => {
    e.preventDefault();
    setResetError(''); setResetSuccess('');
    if (newPassword.length < 8) { setResetError('Password must be at least 8 characters'); return; }
    if (!/[a-zA-Z]/.test(newPassword)) { setResetError('Must contain at least one letter'); return; }
    if (!/[0-9]/.test(newPassword)) { setResetError('Must contain at least one number'); return; }
    if (!/[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(newPassword)) { setResetError('Must contain a special character'); return; }
    if (newPassword !== confirmPassword) { setResetError('Passwords do not match'); return; }
    setResetLoading(true);
    try {
      await api.post('/auth/reset-password', { token: resetToken || generatedToken, new_password: newPassword });
      setResetSuccess('Password reset successfully!');
      setTimeout(() => { setForgotMode(false); setResetEmail(''); setResetToken(''); setGeneratedToken(''); setNewPassword(''); setConfirmPassword(''); setResetSuccess(''); }, 2000);
    } catch (err) { setResetError(typeof err.response?.data?.detail === 'string' ? err.response.data.detail : 'Reset failed'); }
    finally { setResetLoading(false); }
  };

  // ── Forgot Password – Email Step ──
  if (forgotMode === 'email') {
    // ── Email Sent Confirmation ──
    if (emailSent) {
      return (
        <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }}>
          <Header />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 'calc(100vh - 62px)', padding: 24 }}>
            <div style={{ width: '100%', maxWidth: 420 }}>
              <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', padding: '40px 32px', boxShadow: '0 8px 30px rgba(0,0,0,0.07)', textAlign: 'center' }}>
                <div style={{ width: 68, height: 68, borderRadius: '50%', background: 'linear-gradient(135deg,#ecfdf5,#d1fae5)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px', boxShadow: '0 4px 16px rgba(5,150,105,0.18)' }}>
                  <Mail size={30} color="#059669" />
                </div>
                <h1 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Check Your Email</h1>
                <p style={{ fontSize: 14, color: '#475569', lineHeight: 1.6, marginBottom: 4 }}>We sent a password reset link to</p>
                <p style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', marginBottom: 24 }}>{resetEmail}</p>
                <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 12, padding: '14px 18px', marginBottom: 24, textAlign: 'left' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
                    <CheckCircle size={15} color="#059669" style={{ flexShrink: 0, marginTop: 1 }} />
                    <span style={{ fontSize: 13, color: '#475569' }}>Click the link in the email to reset your password</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
                    <CheckCircle size={15} color="#059669" style={{ flexShrink: 0, marginTop: 1 }} />
                    <span style={{ fontSize: 13, color: '#475569' }}>The link expires in 15 minutes</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                    <AlertCircle size={15} color="#94a3b8" style={{ flexShrink: 0, marginTop: 1 }} />
                    <span style={{ fontSize: 13, color: '#64748b' }}>Didn't receive it? Check your spam folder</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => { setEmailSent(false); setResetEmail(''); setResetWorkspace(''); setResetError(''); }}
                  style={{ width: '100%', padding: '11px 0', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', fontSize: 13, fontWeight: 500, color: '#64748b', cursor: 'pointer', fontFamily: F, marginBottom: 10 }}
                >
                  Didn't receive it? Try again
                </button>
                <button
                  type="button"
                  onClick={() => { setForgotMode(false); setEmailSent(false); setResetEmail(''); setResetWorkspace(''); }}
                  style={{ width: '100%', padding: '11px 0', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                >
                  <ArrowLeft size={14} /> Back to Sign In
                </button>
              </div>
            </div>
          </div>
          <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
        </div>
      );
    }

    return (
      <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }} data-testid="forgot-password-page">
        <Header />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 'calc(100vh - 62px)', padding: 24 }}>
          <div style={{ width: '100%', maxWidth: 400 }}>
            <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', padding: '36px 28px', boxShadow: '0 8px 30px rgba(0,0,0,0.06)' }}>
              <div style={{ textAlign: 'center', marginBottom: 24 }}>
                <div style={{ width: 52, height: 52, borderRadius: 14, background: 'linear-gradient(135deg,#eff6ff,#dbeafe)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px' }}><Lock size={24} color="#2563eb" /></div>
                <h1 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Forgot Password</h1>
                <p style={{ fontSize: 13, color: '#64748b' }}>Enter your email to receive a password reset link</p>
              </div>
              <form onSubmit={handleForgotPassword}>
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Email Address</label>
                  <div style={{ position: 'relative' }}>
                    <Mail size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                    <input type="email" value={resetEmail} onChange={(e) => setResetEmail(e.target.value)} placeholder="your@email.com" style={inputStyle} onFocus={focusIn} onBlur={focusOut} required data-testid="forgot-email-input" />
                  </div>
                </div>
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Workspace</label>
                  <input type="text" value={resetWorkspace} onChange={(e) => setResetWorkspace(e.target.value)} placeholder="Optional workspace name or id" style={{ ...inputStyle, paddingLeft: 14 }} onFocus={focusIn} onBlur={focusOut} />
                </div>
                {resetError && (
                  <Alert variant="destructive" className="mb-3 border-red-200 bg-red-50 text-red-700">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{resetError}</AlertDescription>
                  </Alert>
                )}
                <button type="submit" disabled={resetLoading || !resetEmail} style={{ width: '100%', padding: '12px 0', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: resetLoading ? 0.7 : 1 }} data-testid="generate-token-btn">
                  {resetLoading ? <div style={{ width: 16, height: 16, border: '2px solid #fff', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} /> : <Mail size={16} />} Send Reset Email
                </button>
                <button type="button" onClick={() => { setForgotMode(false); setResetWorkspace(''); }} style={{ width: '100%', padding: '8px 0', marginTop: 10, background: 'none', border: 'none', fontSize: 12, color: '#94a3b8', cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}><ArrowLeft size={14} /> Back to Sign In</button>
              </form>
            </div>
          </div>
        </div>
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </div>
    );
  }

  // ── Forgot Password – Reset Step ──
  if (forgotMode === 'reset') {
    return (
      <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }} data-testid="reset-password-page">
        <Header />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 'calc(100vh - 62px)', padding: 24 }}>
          <div style={{ width: '100%', maxWidth: 400 }}>
            <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', padding: '36px 28px', boxShadow: '0 8px 30px rgba(0,0,0,0.06)' }}>
              <div style={{ textAlign: 'center', marginBottom: 24 }}>
                <div style={{ width: 52, height: 52, borderRadius: 14, background: 'linear-gradient(135deg,#ecfdf5,#d1fae5)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px' }}><Lock size={24} color="#059669" /></div>
                <h1 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>Reset Password</h1>
                <p style={{ fontSize: 13, color: '#64748b' }}>Enter your reset token and new password</p>
              </div>
              <form onSubmit={handleResetPassword}>
                {(resetToken || generatedToken) ? (
                  <div style={{ marginBottom: 14, padding: '10px 14px', background: '#ecfdf5', border: '1px solid #bbf7d0', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <CheckCircle size={14} color="#059669" />
                    <span style={{ fontSize: 12, color: '#16a34a', fontWeight: 500 }}>Reset link verified. Enter your new password below.</span>
                  </div>
                ) : (
                  <div style={{ marginBottom: 14 }}>
                    <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Reset Token</label>
                    <input type="text" value={resetToken} onChange={(e) => setResetToken(e.target.value)} placeholder="Paste your reset token" style={{ ...inputStyle, paddingLeft: 14, fontFamily: 'monospace' }} onFocus={focusIn} onBlur={focusOut} required data-testid="reset-token-input" />
                  </div>
                )}
                <div style={{ marginBottom: 14 }}>
                  <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>New Password</label>
                  <div style={{ position: 'relative' }}>
                    <Lock size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                    <input type={showPassword ? 'text' : 'password'} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="New password" style={{ ...inputStyle, paddingRight: 38 }} onFocus={focusIn} onBlur={focusOut} required data-testid="new-password-input" />
                    <button type="button" onClick={() => setShowPassword(!showPassword)} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>{showPassword ? <EyeOff size={15} color="#94a3b8" /> : <Eye size={15} color="#94a3b8" />}</button>
                  </div>
                </div>
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Confirm Password</label>
                  <input type={showPassword ? 'text' : 'password'} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Confirm password" style={{ ...inputStyle, paddingLeft: 14 }} onFocus={focusIn} onBlur={focusOut} required data-testid="confirm-password-input" />
                </div>
                {newPassword.length > 0 && (
                  <div style={{ marginBottom: 14, padding: '10px 14px', background: '#f8fafc', borderRadius: 10 }}>
                    {[
                      { label: 'At least 8 characters', met: newPassword.length >= 8 },
                      { label: 'Contains a letter', met: /[a-zA-Z]/.test(newPassword) },
                      { label: 'Contains a number', met: /[0-9]/.test(newPassword) },
                      { label: 'Contains a special character', met: /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(newPassword) },
                      { label: 'Passwords match', met: confirmPassword.length > 0 && newPassword === confirmPassword },
                    ].map((r, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        {r.met ? <CheckCircle size={12} color="#22c55e" /> : <AlertCircle size={12} color="#cbd5e1" />}
                        <span style={{ fontSize: 11, color: r.met ? '#16a34a' : '#64748b' }}>{r.label}</span>
                      </div>
                    ))}
                  </div>
                )}
                {resetError && (
                  <Alert variant="destructive" className="mb-3 border-red-200 bg-red-50 text-red-700">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{resetError}</AlertDescription>
                  </Alert>
                )}
                {resetSuccess && <div style={{ marginBottom: 12, padding: '10px 14px', background: '#ecfdf5', border: '1px solid #bbf7d0', borderRadius: 10, fontSize: 13, color: '#16a34a', display: 'flex', alignItems: 'center', gap: 8 }}><CheckCircle size={14} /> {resetSuccess}</div>}
                <button type="submit" disabled={resetLoading} style={{ width: '100%', padding: '12px 0', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#059669,#10b981)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(5,150,105,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: resetLoading ? 0.7 : 1 }} data-testid="reset-password-btn">
                  {resetLoading ? <div style={{ width: 16, height: 16, border: '2px solid #fff', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} /> : <Lock size={16} />} Reset Password
                </button>
                <button type="button" onClick={() => { setForgotMode(false); setResetError(''); setResetSuccess(''); }} style={{ width: '100%', padding: '10px 0', marginTop: 10, background: 'none', border: 'none', fontSize: 12, color: '#94a3b8', cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}><ArrowLeft size={14} /> Back to Sign In</button>
              </form>
            </div>
          </div>
        </div>
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </div>
    );
  }

  // ── Main Login ──
  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }} data-testid="signin-page">
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -200, right: -100, width: 500, height: 500, background: 'radial-gradient(circle, rgba(99,102,241,0.08) 0%, transparent 70%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -100, left: -50, width: 400, height: 400, background: 'radial-gradient(circle, rgba(37,99,235,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
      </div>
      <Header />

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 440, margin: '0 auto', padding: '48px 24px 72px' }}>
          <div>
            <div style={{ textAlign: 'center', marginBottom: 24 }}>
              <h1 style={{ fontSize: 24, fontWeight: 700, color: '#0f172a', marginBottom: 6 }}>Sign in to your account</h1>
              <p style={{ fontSize: 14, color: '#64748b' }}>Don't have an account? <Link to="/signup" style={{ color: '#2563eb', fontWeight: 600, textDecoration: 'none' }}>Sign up</Link></p>
              <p style={{ fontSize: 12, color: '#64748b', marginTop: 8 }}>
                Platform operators should use the <Link to="/admin/login" style={{ color: '#0f766e', fontWeight: 700, textDecoration: 'none' }}>admin login</Link>.
              </p>
            </div>

            {error && (
              <Alert
                variant="destructive"
                className="mb-4 border-red-200 bg-red-50 text-red-700"
                data-testid="signin-error"
              >
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <div style={{ background: '#fff', borderRadius: 16, border: '1px solid #e2e8f0', padding: 16, boxShadow: '0 4px 20px rgba(0,0,0,0.04)', marginBottom: 14 }}>
              <button
                type="button"
                onClick={() => startOAuth('google')}
                onMouseEnter={e => { e.currentTarget.style.background = '#f8fafc'; e.currentTarget.style.borderColor = '#adb5bd'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.10)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = '#fff'; e.currentTarget.style.borderColor = '#d1d5db'; e.currentTarget.style.boxShadow = 'none'; }}
                style={{ width: '100%', padding: '11px 16px', borderRadius: 10, border: '1.5px solid #d1d5db', background: '#fff', color: '#111827', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, transition: 'all 0.18s', letterSpacing: '0.01em' }}
                data-testid="google-signin-btn"
              >
                <GoogleIcon />
                Continue with Google
              </button>

              <button
                type="button"
                onClick={() => startOAuth('facebook')}
                onMouseEnter={e => { e.currentTarget.style.background = '#1558c0'; e.currentTarget.style.boxShadow = '0 4px 14px rgba(22,87,209,0.45)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = '#1877F2'; e.currentTarget.style.boxShadow = '0 4px 14px rgba(22,87,209,0.30)'; }}
                style={{ width: '100%', padding: '11px 16px', borderRadius: 10, border: 'none', marginTop: 10, background: '#1877F2', color: '#fff', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: F, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, transition: 'all 0.18s', boxShadow: '0 4px 14px rgba(22,87,209,0.30)', letterSpacing: '0.01em' }}
                data-testid="facebook-signin-btn"
              >
                <FacebookIcon />
                Continue with Facebook
              </button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <div style={{ flex: 1, height: 1, background: '#e2e8f0' }} />
              <span style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.8 }}>or continue with email</span>
              <div style={{ flex: 1, height: 1, background: '#e2e8f0' }} />
            </div>

            <form onSubmit={handleLogin} data-testid="login-form">
              <div style={{ background: '#fff', borderRadius: 16, border: '1px solid #e2e8f0', padding: 24, boxShadow: '0 4px 20px rgba(0,0,0,0.06)' }}>
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Email</label>
                  <div style={{ position: 'relative' }}>
                    <Mail size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                    <input type="email" value={email} onChange={(e) => { setEmail(e.target.value); setWorkspaceRequired(false); setWorkspaceOptions([]); }} placeholder="your@email.com" style={inputStyle} onFocus={focusIn} onBlur={focusOut} data-testid="email-input" required />
                  </div>
                </div>
                {(workspaceRequired || workspace || workspaceOptions.length > 0) && (
                  <div style={{ marginBottom: 16 }}>
                    <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Workspace</label>
                    <input type="text" value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="Workspace name or tenant id" style={{ ...inputStyle, paddingLeft: 14 }} onFocus={focusIn} onBlur={focusOut} data-testid="workspace-input" required={workspaceRequired} />
                    {workspaceOptions.length > 0 && (
                      <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {workspaceOptions.map((option) => (
                          <button
                            key={option.company_id || option.label}
                            type="button"
                            onClick={() => setWorkspace(option.company_name || option.company_id || '')}
                            style={{ border: '1px solid #cbd5e1', background: '#f8fafc', color: '#334155', borderRadius: 999, padding: '6px 10px', fontSize: 12, cursor: 'pointer', fontFamily: F }}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <div>
                  <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'block', marginBottom: 6 }}>Password</label>
                  <div style={{ position: 'relative' }}>
                    <Lock size={15} color="#94a3b8" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
                    <input type={showPassword ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Enter your password" style={{ ...inputStyle, paddingRight: 40 }} onFocus={focusIn} onBlur={focusOut} data-testid="password-input" required />
                    <button type="button" onClick={() => setShowPassword(!showPassword)} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>{showPassword ? <EyeOff size={15} color="#94a3b8" /> : <Eye size={15} color="#94a3b8" />}</button>
                  </div>
                </div>
              </div>

              <button type="submit" disabled={loading} style={{ width: '100%', padding: '12px 0', marginTop: 16, borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 14, fontWeight: 600, cursor: loading ? 'wait' : 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: loading ? 0.7 : 1 }} data-testid="login-btn">
                {loading ? <div style={{ width: 16, height: 16, border: '2px solid #fff', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} /> : <LogIn size={16} />} Sign In
              </button>
            </form>

            <button type="button" onClick={() => { setForgotMode('email'); setResetError(''); setResetSuccess(''); setResetWorkspace(workspace); }} style={{ width: '100%', padding: '12px 0', marginTop: 8, background: 'none', border: 'none', fontSize: 13, color: '#2563eb', fontWeight: 500, cursor: 'pointer', fontFamily: F }} data-testid="forgot-password-btn">Forgot Password?</button>

            <p style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8', marginTop: 14 }}>Secured with enterprise-grade encryption</p>
          </div>
      </div>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>    </div>
  );
}
