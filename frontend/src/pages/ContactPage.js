import { useState } from 'react';
import { Link } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import { Mail, User, MessageSquare, Send, ArrowLeft, Phone, MapPin, Clock, CheckCircle, ChevronRight } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

export default function ContactPage() {
  const [sent, setSent] = useState(false);
  const [focused, setFocused] = useState(null);
  const [form, setForm] = useState({ name: '', email: '', subject: '', message: '' });

  const inputStyle = (field) => ({
    width: '100%', padding: '11px 14px 11px 40px', fontSize: 14, fontFamily: F,
    border: `1.5px solid ${focused === field ? '#2563eb' : '#e2e8f0'}`,
    borderRadius: 10, outline: 'none', background: focused === field ? '#fafbff' : '#fff',
    color: '#0f172a', transition: 'all 0.2s', boxSizing: 'border-box',
    boxShadow: focused === field ? '0 0 0 3px rgba(37,99,235,0.08)' : 'none',
  });

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }}>

      {/* bg blobs */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -160, right: -160, width: 500, height: 500, background: 'radial-gradient(circle, rgba(37,99,235,0.07) 0%, transparent 65%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -80, left: -80, width: 380, height: 380, background: 'radial-gradient(circle, rgba(99,102,241,0.06) 0%, transparent 65%)', borderRadius: '50%' }} />
      </div>

      {/* ── Header ── */}
      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.88)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo />
          <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5, color: '#64748b', textDecoration: 'none', padding: '6px 14px', borderRadius: 8, border: '1px solid #e2e8f0', background: '#fff', fontWeight: 500, transition: 'all 0.2s' }}
            onMouseEnter={e => { e.currentTarget.style.color = '#0f172a'; e.currentTarget.style.borderColor = '#cbd5e1'; }}
            onMouseLeave={e => { e.currentTarget.style.color = '#64748b'; e.currentTarget.style.borderColor = '#e2e8f0'; }}>
            <ArrowLeft size={14} /> Back to Home
          </Link>
        </div>
      </header>

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1100, margin: '0 auto', padding: '52px 24px 72px' }}>

        {/* Page title */}
        <div style={{ textAlign: 'center', marginBottom: 48 }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 14px', background: 'rgba(37,99,235,0.08)', border: '1px solid rgba(37,99,235,0.14)', borderRadius: 100, marginBottom: 16 }}>
            <MessageSquare size={12} color="#2563eb" />
            <span style={{ fontSize: 12, color: '#2563eb', fontWeight: 600, letterSpacing: 0.4 }}>We'd love to hear from you</span>
          </div>
          <h1 style={{ fontSize: 'clamp(28px,4vw,42px)', fontWeight: 800, color: '#0f172a', letterSpacing: '-1px', marginBottom: 10 }}>Get in Touch</h1>
          <p style={{ fontSize: 15.5, color: '#64748b', maxWidth: 440, margin: '0 auto', lineHeight: 1.6 }}>Have a question, feedback, or want to discuss a custom plan? Our team typically responds within a few hours.</p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: 28, alignItems: 'start' }}>

          {/* ── Left info panel ── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {[
              { icon: Mail,    color: '#2563eb', bg: '#eff6ff', title: 'Email Us',        body: 'hello@pulseengine.io',     sub: 'We reply within a few hours' },
              { icon: Phone,   color: '#7c3aed', bg: '#f5f3ff', title: 'Call Us',         body: '+1 (555) 123-4567',        sub: 'Mon–Fri, 9am–6pm PST' },
              { icon: MapPin,  color: '#059669', bg: '#ecfdf5', title: 'Office',           body: '123 Tech Street',         sub: 'San Francisco, CA 94102' },
              { icon: Clock,   color: '#d97706', bg: '#fffbeb', title: 'Response Time',   body: 'Under 4 hours',            sub: 'During business hours' },
            ].map((item, i) => (
              <div key={i} style={{ background: '#fff', border: '1px solid #f1f5f9', borderRadius: 16, padding: '18px 20px', display: 'flex', alignItems: 'flex-start', gap: 14, boxShadow: '0 1px 8px rgba(0,0,0,0.04)', transition: 'all 0.2s' }}
                onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 4px 20px rgba(0,0,0,0.08)'; e.currentTarget.style.transform = 'translateY(-2px)'; }}
                onMouseLeave={e => { e.currentTarget.style.boxShadow = '0 1px 8px rgba(0,0,0,0.04)'; e.currentTarget.style.transform = 'translateY(0)'; }}>
                <div style={{ width: 40, height: 40, borderRadius: 11, background: item.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <item.icon size={18} color={item.color} />
                </div>
                <div>
                  <p style={{ fontSize: 11.5, color: '#94a3b8', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, margin: '0 0 3px' }}>{item.title}</p>
                  <p style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', margin: '0 0 2px' }}>{item.body}</p>
                  <p style={{ fontSize: 12.5, color: '#94a3b8', margin: 0 }}>{item.sub}</p>
                </div>
              </div>
            ))}

            {/* Also try */}
            <div style={{ background: 'linear-gradient(135deg,#1e3a8a,#2563eb)', borderRadius: 16, padding: '20px', boxShadow: '0 8px 24px rgba(37,99,235,0.25)' }}>
              <p style={{ fontSize: 13, fontWeight: 700, color: '#fff', marginBottom: 8 }}>Looking for instant help?</p>
              <p style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.7)', marginBottom: 14, lineHeight: 1.5 }}>Check our knowledge base for guides, FAQs, and tutorials.</p>
              <Link to="/knowledge" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 14px', background: 'rgba(255,255,255,0.15)', color: '#fff', borderRadius: 9, fontSize: 12.5, fontWeight: 600, textDecoration: 'none', border: '1px solid rgba(255,255,255,0.2)', transition: 'all 0.2s' }}
                onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.22)'}
                onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.15)'}>
                Browse Help Center <ChevronRight size={13} />
              </Link>
            </div>
          </div>

          {/* ── Right form ── */}
          <div style={{ background: '#fff', borderRadius: 22, border: '1px solid #f1f5f9', padding: '36px 36px', boxShadow: '0 4px 30px rgba(0,0,0,0.06)' }}>
            {!sent ? (
              <>
                <h2 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', marginBottom: 6 }}>Send us a message</h2>
                <p style={{ fontSize: 13.5, color: '#94a3b8', marginBottom: 26 }}>Fill in the form and we'll get back to you shortly.</p>

                <form onSubmit={(e) => { e.preventDefault(); setSent(true); }} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {/* Name + Email row */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                    <div>
                      <label style={{ fontSize: 12.5, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Your Name</label>
                      <div style={{ position: 'relative' }}>
                        <User size={14} color="#94a3b8" style={{ position: 'absolute', left: 13, top: 12, pointerEvents: 'none' }} />
                        <input value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))}
                          onFocus={() => setFocused('name')} onBlur={() => setFocused(null)}
                          style={inputStyle('name')} placeholder="Alex Johnson" required />
                      </div>
                    </div>
                    <div>
                      <label style={{ fontSize: 12.5, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Email Address</label>
                      <div style={{ position: 'relative' }}>
                        <Mail size={14} color="#94a3b8" style={{ position: 'absolute', left: 13, top: 12, pointerEvents: 'none' }} />
                        <input type="email" value={form.email} onChange={e => setForm(f => ({...f, email: e.target.value}))}
                          onFocus={() => setFocused('email')} onBlur={() => setFocused(null)}
                          style={inputStyle('email')} placeholder="alex@company.com" required />
                      </div>
                    </div>
                  </div>

                  {/* Subject */}
                  <div>
                    <label htmlFor="contact-subject" style={{ fontSize: 12.5, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Subject</label>
                    <div style={{ position: 'relative' }}>
                      <MessageSquare size={14} color="#94a3b8" style={{ position: 'absolute', left: 13, top: 12, pointerEvents: 'none' }} />
                      <select id="contact-subject" value={form.subject} onChange={e => setForm(f => ({...f, subject: e.target.value}))}
                        onFocus={() => setFocused('subject')} onBlur={() => setFocused(null)}
                        style={{ ...inputStyle('subject'), appearance: 'none', cursor: 'pointer', color: form.subject ? '#0f172a' : '#94a3b8' }}>
                        <option value="" disabled>Select a topic…</option>
                        <option value="sales">Sales & Pricing</option>
                        <option value="support">Technical Support</option>
                        <option value="billing">Billing & Account</option>
                        <option value="partnership">Partnership</option>
                        <option value="other">Other</option>
                      </select>
                    </div>
                  </div>

                  {/* Message */}
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Message</label>
                    <textarea value={form.message} onChange={e => setForm(f => ({...f, message: e.target.value}))}
                      onFocus={() => setFocused('message')} onBlur={() => setFocused(null)}
                      rows={5} placeholder="Tell us how we can help you…" required
                      style={{ ...inputStyle('message'), paddingLeft: 14, paddingTop: 11, resize: 'vertical', minHeight: 120, lineHeight: 1.6 }} />
                  </div>

                  <button type="submit" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '13px 28px', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', border: 'none', borderRadius: 11, fontSize: 14, fontWeight: 700, cursor: 'pointer', fontFamily: F, boxShadow: '0 4px 16px rgba(37,99,235,0.35)', transition: 'all 0.2s', alignSelf: 'flex-start' }}
                    onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(37,99,235,0.45)'; }}
                    onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 4px 16px rgba(37,99,235,0.35)'; }}>
                    <Send size={15} /> Send Message
                  </button>
                </form>
              </>
            ) : (
              <div style={{ textAlign: 'center', padding: '40px 20px' }}>
                <div style={{ width: 64, height: 64, borderRadius: '50%', background: '#ecfdf5', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
                  <CheckCircle size={30} color="#16a34a" />
                </div>
                <h3 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>Message Sent!</h3>
                <p style={{ fontSize: 14.5, color: '#64748b', lineHeight: 1.6, marginBottom: 24, maxWidth: 320, margin: '0 auto 24px' }}>Thanks for reaching out. Our team will get back to you within a few hours.</p>
                <button onClick={() => { setSent(false); setForm({ name: '', email: '', subject: '', message: '' }); }}
                  style={{ padding: '10px 22px', background: 'transparent', border: '1.5px solid #e2e8f0', borderRadius: 10, fontSize: 13.5, fontWeight: 600, color: '#475569', cursor: 'pointer', fontFamily: F, transition: 'all 0.2s' }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = '#cbd5e1'; e.currentTarget.style.color = '#0f172a'; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.color = '#475569'; }}>
                  Send another message
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Footer ── */}
      <footer style={{ background: '#0f172a', padding: '28px 24px', fontFamily: F }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
          <PlatformLogo textColor="#fff" imageWidth={28} fontSize={14} />
          <div style={{ display: 'flex', gap: 22 }}>
            {[['Home','/'],['Privacy','/privacy'],['Terms','/terms'],['Contact','/contact']].map(([l,h]) => (
              <Link key={l} to={h} style={{ fontSize: 13, color: l === 'Contact' ? '#fff' : '#94a3b8', textDecoration: 'none', transition: 'color 0.2s' }}
                onMouseEnter={e => e.target.style.color = '#fff'} onMouseLeave={e => { if (l !== 'Contact') e.target.style.color = '#94a3b8'; }}>{l}</Link>
            ))}
          </div>
          <p style={{ fontSize: 12, color: '#475569', margin: 0 }}>© 2026 Pulse Engine. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
