import { useState, useEffect, useRef } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { normalizeAvatarUrl, displayNameInitial } from '@/lib/avatar';
import { User, Mail, Phone, Globe, MapPin, Camera, Save, Shield, Key, CheckCircle, XCircle, Loader } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function ProfilePage() {
  const { user: authUser } = useAuth();
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState('');
  const [error, setError] = useState('');
  const [form, setForm] = useState({ name: '', email: '', mobile_number: '', avatar: '', website_address: '', address_info: '' });
  const [passwordForm, setPasswordForm] = useState({ new_password: '', confirm_password: '' });
  const [passwordMsg, setPasswordMsg] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [passwordSaving, setPasswordSaving] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/settings/personal');
        setProfile(res.data);
        setForm({ name: res.data.name || '', email: res.data.email || '', mobile_number: res.data.mobile_number || '', avatar: normalizeAvatarUrl(res.data.avatar || ''), website_address: res.data.website_address || '', address_info: res.data.address_info || '' });
      } catch (err) { setError('Failed to load profile'); }
      finally { setLoading(false); }
    })();
  }, []);

  const handleSave = async () => {
    setSaving(true); setSavedMsg(''); setError('');
    try {
      const res = await api.put('/settings/personal', form);
      setProfile(res.data); setSavedMsg('Profile updated successfully!');
      if (form.avatar) localStorage.setItem('pe_avatar', normalizeAvatarUrl(form.avatar));
      setTimeout(() => setSavedMsg(''), 3000);
    } catch (err) { setError(err.response?.data?.detail || 'Failed to save'); }
    finally { setSaving(false); }
  };

  const handleAvatarUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { setForm(f => ({ ...f, avatar: normalizeAvatarUrl(reader.result || '') })); };
    reader.readAsDataURL(file);
  };

  const handlePasswordChange = async () => {
    if (passwordForm.new_password !== passwordForm.confirm_password) { setPasswordError('Passwords do not match'); return; }
    if (passwordForm.new_password.length < 8) { setPasswordError('Password must be at least 8 characters'); return; }
    setPasswordSaving(true); setPasswordError(''); setPasswordMsg('');
    try {
      await api.put(`/users/${authUser?.id}/password`, { new_password: passwordForm.new_password });
      setPasswordMsg('Password changed successfully!'); setPasswordForm({ new_password: '', confirm_password: '' });
      setTimeout(() => setPasswordMsg(''), 3000);
    } catch (err) { setPasswordError(err.response?.data?.detail || 'Failed to change password'); }
    finally { setPasswordSaving(false); }
  };

  const inputStyle = { width: '100%', padding: '10px 14px', fontSize: 14, border: '1.5px solid #e2e8f0', borderRadius: 10, outline: 'none', background: '#fff', color: '#0f172a', boxSizing: 'border-box', fontFamily: F, transition: 'all 0.2s' };
  const focusIn = (e) => { e.target.style.borderColor = '#2563eb'; e.target.style.boxShadow = '0 0 0 3px rgba(37,99,235,0.08)'; };
  const focusOut = (e) => { e.target.style.borderColor = '#e2e8f0'; e.target.style.boxShadow = 'none'; };

  if (loading) return (
    <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}>
      <div style={{ width: 28, height: 28, border: '3px solid #e2e8f0', borderTopColor: '#2563eb', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );

  return (
    <div style={{ fontFamily: F, padding: '24px 28px', maxWidth: 780, margin: '0 auto' }} data-testid="profile-page">
      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: '#0f172a', letterSpacing: '-0.3px' }}>My Profile</h1>
        <p style={{ fontSize: 13, color: '#64748b', marginTop: 2 }}>Manage your personal information and security settings</p>
      </div>

      {/* Status messages */}
      {savedMsg && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: '#ecfdf5', border: '1px solid #bbf7d0', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
          <CheckCircle size={15} color="#22c55e" /><span style={{ fontSize: 13, color: '#16a34a' }}>{savedMsg}</span>
        </div>
      )}
      {error && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
          <XCircle size={15} color="#dc2626" /><span style={{ fontSize: 13, color: '#dc2626' }}>{error}</span>
        </div>
      )}

      {/* Avatar & Personal Info card */}
      <div style={{ background: '#fff', borderRadius: 16, border: '1px solid #e2e8f0', padding: 24, boxShadow: '0 2px 10px rgba(0,0,0,0.04)', marginBottom: 20 }}>
        <p style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 16 }}>Personal Information</p>

        {/* Avatar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
          <div style={{ position: 'relative' }}>
            <div style={{ width: 72, height: 72, borderRadius: 18, background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', border: '3px solid #f1f5f9', color: '#fff', fontSize: 28, fontWeight: 700 }}>
              {form.avatar ? (
                <img src={form.avatar} alt="" referrerPolicy="no-referrer" style={{ width: '100%', height: '100%', objectFit: 'cover' }} onError={() => setForm(f => ({ ...f, avatar: '' }))} />
              ) : (
                displayNameInitial(form.name)
              )}
            </div>
            <button onClick={() => fileInputRef.current?.click()} style={{ position: 'absolute', bottom: -4, right: -4, width: 28, height: 28, borderRadius: 8, background: '#fff', border: '1.5px solid #e2e8f0', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', boxShadow: '0 2px 6px rgba(0,0,0,0.1)' }}>
              <Camera size={13} color="#475569" />
            </button>
            <input ref={fileInputRef} type="file" accept="image/*" onChange={handleAvatarUpload} style={{ display: 'none' }} />
          </div>
          <div>
            <p style={{ fontSize: 16, fontWeight: 600, color: '#0f172a' }}>{form.name || 'Your Name'}</p>
            <p style={{ fontSize: 12, color: '#64748b' }}>{profile?.role ? profile.role.charAt(0).toUpperCase() + profile.role.slice(1) : ''}{profile?.company_name ? ` at ${profile.company_name}` : ''}</p>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><User size={12} /> Full Name</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><Mail size={12} /> Email</label>
            <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} type="email" style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><Phone size={12} /> Phone Number</label>
            <input value={form.mobile_number} onChange={(e) => setForm({ ...form, mobile_number: e.target.value })} style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><Globe size={12} /> Website</label>
            <input value={form.website_address} onChange={(e) => setForm({ ...form, website_address: e.target.value })} placeholder="https://..." style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><MapPin size={12} /> Address</label>
            <input value={form.address_info} onChange={(e) => setForm({ ...form, address_info: e.target.value })} placeholder="City, Country" style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
          <button onClick={handleSave} disabled={saving} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 22px', borderRadius: 10, border: 'none', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: saving ? 'wait' : 'pointer', fontFamily: F, boxShadow: '0 4px 14px rgba(37,99,235,0.25)', opacity: saving ? 0.7 : 1 }}>
            {saving ? <Loader size={14} /> : <Save size={14} />} {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>

      {/* Security card */}
      <div style={{ background: '#fff', borderRadius: 16, border: '1px solid #e2e8f0', padding: 24, boxShadow: '0 2px 10px rgba(0,0,0,0.04)' }}>
        <p style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 16 }}><Shield size={12} style={{ display: 'inline', marginRight: 4 }} />Security</p>

        {passwordMsg && <div style={{ marginBottom: 12, padding: '8px 12px', background: '#ecfdf5', borderRadius: 8, fontSize: 13, color: '#16a34a' }}>{passwordMsg}</div>}
        {passwordError && <div style={{ marginBottom: 12, padding: '8px 12px', background: '#fef2f2', borderRadius: 8, fontSize: 13, color: '#dc2626' }}>{passwordError}</div>}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 14 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><Key size={12} /> New Password</label>
            <input type="password" value={passwordForm.new_password} onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })} placeholder="Min 8 characters" style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 500, color: '#475569', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}><Key size={12} /> Confirm Password</label>
            <input type="password" value={passwordForm.confirm_password} onChange={(e) => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })} placeholder="Re-enter password" style={inputStyle} onFocus={focusIn} onBlur={focusOut} />
          </div>
        </div>
        <button onClick={handlePasswordChange} disabled={passwordSaving || !passwordForm.new_password} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 20px', borderRadius: 10, border: '1.5px solid #e2e8f0', background: '#fff', color: '#475569', fontSize: 13, fontWeight: 500, cursor: 'pointer', fontFamily: F, opacity: passwordSaving ? 0.6 : 1 }}>
          {passwordSaving ? <Loader size={14} /> : <Shield size={14} />} Change Password
        </button>

        <div style={{ marginTop: 20, padding: '12px 16px', background: '#f8fafc', borderRadius: 10 }}>
          <p style={{ fontSize: 12, color: '#64748b' }}>
            <strong>Account Info</strong><br />
            Role: <span style={{ color: '#0f172a', fontWeight: 500 }}>{profile?.role}</span> &middot;
            Company: <span style={{ color: '#0f172a', fontWeight: 500 }}>{profile?.company_name || 'N/A'}</span> &middot;
            Timezone: <span style={{ color: '#0f172a', fontWeight: 500 }}>{profile?.timezone || 'UTC'}</span>
          </p>
        </div>
      </div>
    </div>
  );
}
