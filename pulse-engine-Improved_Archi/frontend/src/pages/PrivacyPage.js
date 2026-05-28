import { Link } from 'react-router-dom';
import { useState } from 'react';
import PlatformLogo from '@/components/PlatformLogo';
import { ArrowLeft, Shield, Database, Share2, UserCheck, Lock, Mail, ChevronRight } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const SECTIONS = [
  {
    id: 's1', num: '01', icon: Database, color: '#2563eb', bg: '#eff6ff',
    title: 'Information We Collect',
    content: [
      { type: 'p', text: 'At Pulse Engine, we collect information to provide better services to our users. We collect information in the following ways:' },
      { type: 'list', items: [
        { label: 'Account Information', body: 'When you sign up, we collect your name, email address, company name, and other profile information.' },
        { label: 'Usage Data', body: 'We collect information about how you use our platform, including conversation data, analytics preferences, and feature interactions.' },
        { label: 'Device Information', body: 'We may collect device-specific information such as browser type, IP address, and operating system.' },
        { label: 'Cookies', body: 'We use cookies and similar technologies to maintain sessions and improve user experience.' },
      ]},
    ],
  },
  {
    id: 's2', num: '02', icon: Shield, color: '#7c3aed', bg: '#f5f3ff',
    title: 'How We Use Your Information',
    content: [
      { type: 'p', text: 'We use the collected information to:' },
      { type: 'bullets', items: [
        'Provide, maintain, and improve our services',
        'Process and manage customer conversations across channels',
        'Analyse usage patterns to enhance platform features',
        'Send important updates, security alerts, and support messages',
        'Detect and prevent fraud or abuse of our services',
      ]},
    ],
  },
  {
    id: 's3', num: '03', icon: Lock, color: '#059669', bg: '#ecfdf5',
    title: 'Data Security',
    content: [
      { type: 'p', text: 'We implement industry-standard security measures to protect your data, including:' },
      { type: 'bullets', items: [
        'End-to-end encryption for sensitive data transmission',
        'JWT-based authentication with secure token management',
        'Role-based access control (RBAC) for data authorisation',
        'Regular security audits and vulnerability assessments',
        'Encrypted database storage with backup protocols',
      ]},
    ],
  },
  {
    id: 's4', num: '04', icon: Share2, color: '#d97706', bg: '#fffbeb',
    title: 'Data Sharing',
    content: [
      { type: 'p', text: 'We do not sell your personal information. We may share data with:' },
      { type: 'list', items: [
        { label: 'Service Providers', body: 'Third-party services that help us operate our platform (e.g., AI processing, cloud hosting).' },
        { label: 'Legal Requirements', body: 'When required by law or to protect our legal rights.' },
        { label: 'Business Transfers', body: 'In connection with mergers, acquisitions, or asset sales.' },
      ]},
    ],
  },
  {
    id: 's5', num: '05', icon: UserCheck, color: '#0284c7', bg: '#f0f9ff',
    title: 'Your Rights',
    content: [
      { type: 'p', text: 'You have the following rights regarding your personal data:' },
      { type: 'list', items: [
        { label: 'Access', body: 'Request a copy of your personal data.' },
        { label: 'Correction', body: 'Update or correct inaccurate information.' },
        { label: 'Deletion', body: 'Request deletion of your account and associated data.' },
        { label: 'Export', body: 'Download your data in a portable format.' },
        { label: 'Opt-out', body: 'Unsubscribe from marketing communications.' },
      ]},
    ],
  },
  {
    id: 's6', num: '06', icon: Mail, color: '#db2777', bg: '#fdf2f8',
    title: 'Contact Us',
    content: [
      { type: 'p', text: 'If you have questions about this Privacy Policy or your personal data, please contact us:' },
      { type: 'contact', email: 'privacy@pulseengine.io', address: 'Pulse Engine, Inc., 123 Tech Street, San Francisco, CA 94102' },
    ],
  },
];

function SectionBlock({ s, active, onSelect }) {
  return (
    <div id={s.id} style={{ background: '#fff', border: `1.5px solid ${active ? s.color + '40' : '#f1f5f9'}`, borderRadius: 18, overflow: 'hidden', boxShadow: active ? `0 4px 24px ${s.color}18` : '0 1px 6px rgba(0,0,0,0.04)', transition: 'all 0.3s' }}>
      {/* Header row */}
      <div onClick={() => onSelect(s.id)} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '18px 22px', cursor: 'pointer', background: active ? `${s.bg}` : '#fff', transition: 'background 0.2s' }}>
        <div style={{ width: 38, height: 38, borderRadius: 10, background: s.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: `1px solid ${s.color}22` }}>
          <s.icon size={17} color={s.color} />
        </div>
        <div style={{ flex: 1 }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: s.color, textTransform: 'uppercase', letterSpacing: 0.8, display: 'block', marginBottom: 1 }}>Section {s.num}</span>
          <span style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>{s.title}</span>
        </div>
        <ChevronRight size={16} color="#94a3b8" style={{ transform: active ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s', flexShrink: 0 }} />
      </div>

      {/* Body */}
      <div style={{ maxHeight: active ? 600 : 0, overflow: 'hidden', transition: 'max-height 0.45s cubic-bezier(0.4,0,0.2,1)' }}>
        <div style={{ padding: '6px 22px 22px', borderTop: `1px solid ${s.color}18` }}>
          {s.content.map((block, bi) => {
            if (block.type === 'p') return <p key={bi} style={{ fontSize: 14, color: '#64748b', lineHeight: 1.7, marginTop: 14, marginBottom: 0 }}>{block.text}</p>;
            if (block.type === 'bullets') return (
              <ul key={bi} style={{ margin: '12px 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {block.items.map((item, ii) => (
                  <li key={ii} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 14, color: '#475569' }}>
                    <span style={{ width: 20, height: 20, borderRadius: 6, background: s.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1, fontSize: 10, color: s.color, fontWeight: 800 }}>✓</span>
                    {item}
                  </li>
                ))}
              </ul>
            );
            if (block.type === 'list') return (
              <div key={bi} style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {block.items.map((item, ii) => (
                  <div key={ii} style={{ padding: '10px 14px', background: '#f8fafc', borderRadius: 10, borderLeft: `3px solid ${s.color}` }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: '#0f172a' }}>{item.label}: </span>
                    <span style={{ fontSize: 13.5, color: '#64748b' }}>{item.body}</span>
                  </div>
                ))}
              </div>
            );
            if (block.type === 'contact') return (
              <div key={bi} style={{ marginTop: 14, padding: '16px', background: '#f8fafc', borderRadius: 12, fontSize: 14, color: '#475569', lineHeight: 1.8 }}>
                <div><span style={{ fontWeight: 600, color: '#0f172a' }}>Email: </span><a href={`mailto:${block.email}`} style={{ color: s.color, textDecoration: 'none' }}>{block.email}</a></div>
                <div><span style={{ fontWeight: 600, color: '#0f172a' }}>Address: </span>{block.address}</div>
              </div>
            );
            return null;
          })}
        </div>
      </div>
    </div>
  );
}

export default function PrivacyPage() {
  const [active, setActive] = useState('s1');
  const toggle = (id) => setActive(a => a === id ? null : id);

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }}>
      {/* bg */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
        <div style={{ position: 'absolute', top: -120, right: -100, width: 420, height: 420, background: 'radial-gradient(circle, rgba(37,99,235,0.06) 0%, transparent 65%)', borderRadius: '50%' }} />
      </div>

      {/* Header */}
      <header style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(255,255,255,0.9)', backdropFilter: 'blur(14px)', borderBottom: '1px solid #f1f5f9', boxShadow: '0 1px 12px rgba(0,0,0,0.04)' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', padding: '0 28px', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo />
          <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5, color: '#64748b', textDecoration: 'none', padding: '6px 14px', borderRadius: 8, border: '1px solid #e2e8f0', background: '#fff', fontWeight: 500 }}
            onMouseEnter={e => { e.currentTarget.style.color = '#0f172a'; e.currentTarget.style.borderColor = '#cbd5e1'; }}
            onMouseLeave={e => { e.currentTarget.style.color = '#64748b'; e.currentTarget.style.borderColor = '#e2e8f0'; }}>
            <ArrowLeft size={14} /> Back to Home
          </Link>
        </div>
      </header>

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 860, margin: '0 auto', padding: '52px 24px 72px' }}>

        {/* Hero */}
        <div style={{ textAlign: 'center', marginBottom: 44 }}>
          <div style={{ width: 54, height: 54, borderRadius: 16, background: '#eff6ff', border: '1px solid rgba(37,99,235,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            <Shield size={24} color="#2563eb" />
          </div>
          <h1 style={{ fontSize: 'clamp(28px,4vw,42px)', fontWeight: 800, color: '#0f172a', letterSpacing: '-1px', marginBottom: 10 }}>Privacy Policy</h1>
          <p style={{ fontSize: 14.5, color: '#94a3b8', marginBottom: 16 }}>Last updated: February 2026</p>
          <p style={{ fontSize: 15, color: '#64748b', maxWidth: 500, margin: '0 auto', lineHeight: 1.65 }}>We're committed to protecting your personal information and being transparent about what we collect and how we use it.</p>
        </div>

        {/* Quick nav pills */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginBottom: 32 }}>
          {SECTIONS.map(s => (
            <button key={s.id} onClick={() => setActive(s.id)}
              style={{ padding: '6px 14px', borderRadius: 100, fontSize: 12.5, fontWeight: 600, border: `1.5px solid ${active === s.id ? s.color : '#e2e8f0'}`, background: active === s.id ? s.bg : '#fff', color: active === s.id ? s.color : '#64748b', cursor: 'pointer', fontFamily: F, transition: 'all 0.2s' }}>
              {s.title}
            </button>
          ))}
        </div>

        {/* Accordion sections */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {SECTIONS.map(s => <SectionBlock key={s.id} s={s} active={active === s.id} onSelect={toggle} />)}
        </div>

        {/* Bottom CTA */}
        <div style={{ marginTop: 36, background: 'linear-gradient(135deg,#1e3a8a,#2563eb)', borderRadius: 20, padding: '28px 32px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap', boxShadow: '0 8px 32px rgba(37,99,235,0.25)' }}>
          <div>
            <p style={{ fontSize: 16, fontWeight: 700, color: '#fff', marginBottom: 4 }}>Questions about your data?</p>
            <p style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.7)', margin: 0 }}>Our team is happy to help with any privacy-related questions.</p>
          </div>
          <Link to="/contact" style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '11px 22px', background: '#fff', color: '#2563eb', borderRadius: 10, fontSize: 13.5, fontWeight: 700, textDecoration: 'none', whiteSpace: 'nowrap', transition: 'all 0.2s' }}
            onMouseEnter={e => e.currentTarget.style.background = '#f0f4ff'}
            onMouseLeave={e => e.currentTarget.style.background = '#fff'}>
            Contact Us <ChevronRight size={14} />
          </Link>
        </div>
      </div>

      {/* Footer */}
      <footer style={{ background: '#0f172a', padding: '28px 24px', fontFamily: F }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
          <PlatformLogo textColor="#fff" imageWidth={28} fontSize={14} />
          <div style={{ display: 'flex', gap: 22 }}>
            {[['Home','/'],['Privacy','/privacy'],['Terms','/terms'],['Contact','/contact']].map(([l,h]) => (
              <Link key={l} to={h} style={{ fontSize: 13, color: l === 'Privacy' ? '#fff' : '#94a3b8', textDecoration: 'none', transition: 'color 0.2s' }}
                onMouseEnter={e => e.target.style.color = '#fff'} onMouseLeave={e => { if (l !== 'Privacy') e.target.style.color = '#94a3b8'; }}>{l}</Link>
            ))}
          </div>
          <p style={{ fontSize: 12, color: '#475569', margin: 0 }}>© 2026 Pulse Engine. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
