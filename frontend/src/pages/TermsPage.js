import { Link } from 'react-router-dom';
import { useState } from 'react';
import PlatformLogo from '@/components/PlatformLogo';
import { ArrowLeft, FileText, Users, Settings, Ban, CreditCard, BookOpen, Lock, AlertTriangle, XCircle, RefreshCw, Mail, ChevronRight } from 'lucide-react';

const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';

const SECTIONS = [
  {
    id: 't1', num: '01', icon: FileText, color: '#2563eb', bg: '#eff6ff',
    title: 'Acceptance of Terms',
    content: [{ type: 'p', text: 'By accessing or using Pulse Engine\'s services, you agree to be bound by these Terms of Service. If you do not agree to these terms, please do not use our services. These terms apply to all users, including visitors, registered users, and customers.' }],
  },
  {
    id: 't2', num: '02', icon: Settings, color: '#7c3aed', bg: '#f5f3ff',
    title: 'Description of Service',
    content: [
      { type: 'p', text: 'Pulse Engine provides an AI-powered customer engagement platform that includes:' },
      { type: 'bullets', items: [
        'Unified inbox for managing conversations across multiple channels',
        'AI-powered automated responses and sentiment analysis',
        'Lead management and scoring capabilities',
        'Analytics and reporting dashboards',
        'Team collaboration and role-based access controls',
        'Integration with WhatsApp, Facebook, Instagram, and web chat',
      ]},
    ],
  },
  {
    id: 't3', num: '03', icon: Users, color: '#059669', bg: '#ecfdf5',
    title: 'Account Registration',
    content: [
      { type: 'p', text: 'To use our services, you must:' },
      { type: 'bullets', items: [
        'Be at least 18 years old or have legal authority to enter into agreements',
        'Provide accurate and complete registration information',
        'Maintain the security of your account credentials',
        'Notify us immediately of any unauthorised access',
        'Be responsible for all activities under your account',
      ]},
    ],
  },
  {
    id: 't4', num: '04', icon: Ban, color: '#dc2626', bg: '#fef2f2',
    title: 'Acceptable Use',
    content: [
      { type: 'p', text: 'You agree not to use Pulse Engine to:' },
      { type: 'bullets', items: [
        'Violate any applicable laws or regulations',
        'Send spam, unsolicited messages, or harassing content',
        'Impersonate others or provide false information',
        'Attempt to gain unauthorised access to our systems',
        'Interfere with or disrupt our services or servers',
        'Upload malicious code, viruses, or harmful content',
        'Collect user data without proper consent',
      ]},
    ],
  },
  {
    id: 't5', num: '05', icon: CreditCard, color: '#d97706', bg: '#fffbeb',
    title: 'Subscription and Payment',
    content: [
      { type: 'p', text: 'Paid subscriptions are subject to the following terms:' },
      { type: 'list', items: [
        { label: 'Billing', body: 'Subscriptions are billed in advance on a monthly or annual basis.' },
        { label: 'Renewals', body: 'Subscriptions automatically renew unless cancelled before the renewal date.' },
        { label: 'Refunds', body: 'Refunds are available within 14 days of initial purchase for annual plans.' },
        { label: 'Price Changes', body: 'We may modify pricing with 30 days notice to existing subscribers.' },
        { label: 'Taxes', body: 'Prices are exclusive of applicable taxes unless stated otherwise.' },
      ]},
    ],
  },
  {
    id: 't6', num: '06', icon: BookOpen, color: '#0284c7', bg: '#f0f9ff',
    title: 'Intellectual Property',
    content: [{ type: 'p', text: 'Pulse Engine and its original content, features, and functionality are owned by Pulse Engine, Inc. and are protected by international copyright, trademark, patent, and trade secret laws. You may not copy, modify, distribute, or create derivative works from our platform without explicit permission.' }],
  },
  {
    id: 't7', num: '07', icon: Lock, color: '#db2777', bg: '#fdf2f8',
    title: 'Data and Privacy',
    content: [{ type: 'p', text: 'Your use of Pulse Engine is also governed by our Privacy Policy. By using our services, you consent to the collection and use of information as described in our Privacy Policy. You retain ownership of your data and can export or delete it at any time.' }],
  },
  {
    id: 't8', num: '08', icon: AlertTriangle, color: '#ea580c', bg: '#fff7ed',
    title: 'Limitation of Liability',
    content: [{ type: 'p', text: 'To the maximum extent permitted by law, Pulse Engine shall not be liable for any indirect, incidental, special, consequential, or punitive damages, including loss of profits, data, or business opportunities, arising from your use of our services.' }],
  },
  {
    id: 't9', num: '09', icon: XCircle, color: '#64748b', bg: '#f8fafc',
    title: 'Termination',
    content: [{ type: 'p', text: 'We may terminate or suspend your account immediately, without prior notice or liability, for any reason, including breach of these Terms. Upon termination, your right to use the service will cease immediately. You may also terminate your account at any time through your account settings.' }],
  },
  {
    id: 't10', num: '10', icon: RefreshCw, color: '#6366f1', bg: '#eef2ff',
    title: 'Changes to Terms',
    content: [{ type: 'p', text: 'We reserve the right to modify these terms at any time. We will notify users of significant changes via email or through a prominent notice on our platform. Continued use of our services after changes constitutes acceptance of the new terms.' }],
  },
  {
    id: 't11', num: '11', icon: Mail, color: '#16a34a', bg: '#f0fdf4',
    title: 'Contact Information',
    content: [
      { type: 'p', text: 'For questions about these Terms of Service, please contact us:' },
      { type: 'contact', email: 'legal@pulseengine.io', address: 'Pulse Engine, Inc., 123 Tech Street, San Francisco, CA 94102' },
    ],
  },
];

function SectionBlock({ s, active, onSelect }) {
  return (
    <div id={s.id} style={{ background: '#fff', border: `1.5px solid ${active ? s.color + '40' : '#f1f5f9'}`, borderRadius: 16, overflow: 'hidden', boxShadow: active ? `0 4px 20px ${s.color}15` : '0 1px 4px rgba(0,0,0,0.04)', transition: 'all 0.3s' }}>
      <div onClick={() => onSelect(s.id)} style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '15px 20px', cursor: 'pointer', background: active ? s.bg : '#fff', transition: 'background 0.2s' }}>
        <div style={{ width: 34, height: 34, borderRadius: 9, background: active ? '#fff' : s.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: `1px solid ${s.color}20`, transition: 'background 0.2s' }}>
          <s.icon size={15} color={s.color} />
        </div>
        <div style={{ flex: 1 }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: s.color, textTransform: 'uppercase', letterSpacing: 0.8, display: 'block', marginBottom: 1 }}>Section {s.num}</span>
          <span style={{ fontSize: 14.5, fontWeight: 700, color: '#0f172a' }}>{s.title}</span>
        </div>
        <ChevronRight size={15} color="#94a3b8" style={{ transform: active ? 'rotate(90deg)' : 'rotate(0)', transition: 'transform 0.2s', flexShrink: 0 }} />
      </div>
      <div style={{ maxHeight: active ? 500 : 0, overflow: 'hidden', transition: 'max-height 0.4s cubic-bezier(0.4,0,0.2,1)' }}>
        <div style={{ padding: '4px 20px 20px', borderTop: `1px solid ${s.color}15` }}>
          {s.content.map((block, bi) => {
            if (block.type === 'p') return <p key={bi} style={{ fontSize: 14, color: '#64748b', lineHeight: 1.75, marginTop: 12, marginBottom: 0 }}>{block.text}</p>;
            if (block.type === 'bullets') return (
              <ul key={bi} style={{ margin: '12px 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 7 }}>
                {block.items.map((item, ii) => (
                  <li key={ii} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, fontSize: 13.5, color: '#475569', lineHeight: 1.5 }}>
                    <span style={{ width: 18, height: 18, borderRadius: 5, background: s.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1, fontSize: 9, color: s.color, fontWeight: 800 }}>✓</span>
                    {item}
                  </li>
                ))}
              </ul>
            );
            if (block.type === 'list') return (
              <div key={bi} style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {block.items.map((item, ii) => (
                  <div key={ii} style={{ padding: '9px 13px', background: '#f8fafc', borderRadius: 9, borderLeft: `3px solid ${s.color}` }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: '#0f172a' }}>{item.label}: </span>
                    <span style={{ fontSize: 13, color: '#64748b' }}>{item.body}</span>
                  </div>
                ))}
              </div>
            );
            if (block.type === 'contact') return (
              <div key={bi} style={{ marginTop: 12, padding: '14px', background: '#f8fafc', borderRadius: 10, fontSize: 13.5, color: '#475569', lineHeight: 1.9 }}>
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

export default function TermsPage() {
  const [active, setActive] = useState('t1');
  const toggle = (id) => setActive(a => a === id ? null : id);

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', fontFamily: F }}>
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
        <div style={{ position: 'absolute', top: -100, left: -100, width: 400, height: 400, background: 'radial-gradient(circle, rgba(99,102,241,0.06) 0%, transparent 65%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -60, right: -60, width: 340, height: 340, background: 'radial-gradient(circle, rgba(37,99,235,0.05) 0%, transparent 65%)', borderRadius: '50%' }} />
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
          <div style={{ width: 54, height: 54, borderRadius: 16, background: '#eef2ff', border: '1px solid rgba(99,102,241,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            <FileText size={24} color="#6366f1" />
          </div>
          <h1 style={{ fontSize: 'clamp(28px,4vw,42px)', fontWeight: 800, color: '#0f172a', letterSpacing: '-1px', marginBottom: 10 }}>Terms of Service</h1>
          <p style={{ fontSize: 14.5, color: '#94a3b8', marginBottom: 16 }}>Last updated: February 2026</p>
          <p style={{ fontSize: 15, color: '#64748b', maxWidth: 500, margin: '0 auto', lineHeight: 1.65 }}>Please read these terms carefully before using Pulse Engine. By using our services you agree to be bound by these terms.</p>
        </div>

        {/* Quick nav */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, justifyContent: 'center', marginBottom: 30 }}>
          {SECTIONS.map(s => (
            <button key={s.id} onClick={() => setActive(s.id)}
              style={{ padding: '5px 12px', borderRadius: 100, fontSize: 12, fontWeight: 600, border: `1.5px solid ${active === s.id ? s.color : '#e2e8f0'}`, background: active === s.id ? s.bg : '#fff', color: active === s.id ? s.color : '#64748b', cursor: 'pointer', fontFamily: F, transition: 'all 0.2s' }}>
              {s.num}. {s.title}
            </button>
          ))}
        </div>

        {/* Sections */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {SECTIONS.map(s => <SectionBlock key={s.id} s={s} active={active === s.id} onSelect={toggle} />)}
        </div>

        {/* Bottom CTA */}
        <div style={{ marginTop: 36, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div style={{ background: '#fff', border: '1px solid #f1f5f9', borderRadius: 16, padding: '22px 24px', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
            <p style={{ fontSize: 14, fontWeight: 700, color: '#0f172a', marginBottom: 6 }}>Questions about these terms?</p>
            <p style={{ fontSize: 13, color: '#94a3b8', marginBottom: 14, lineHeight: 1.5 }}>Our legal team is happy to clarify anything in these terms.</p>
            <Link to="/contact" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 18px', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', borderRadius: 9, fontSize: 13, fontWeight: 700, textDecoration: 'none', boxShadow: '0 3px 12px rgba(37,99,235,0.3)' }}>
              Contact Us <ChevronRight size={13} />
            </Link>
          </div>
          <div style={{ background: '#fff', border: '1px solid #f1f5f9', borderRadius: 16, padding: '22px 24px', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
            <p style={{ fontSize: 14, fontWeight: 700, color: '#0f172a', marginBottom: 6 }}>Also review our Privacy Policy</p>
            <p style={{ fontSize: 13, color: '#94a3b8', marginBottom: 14, lineHeight: 1.5 }}>Understand how we collect and protect your personal data.</p>
            <Link to="/privacy" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 18px', background: '#f8fafc', color: '#334155', borderRadius: 9, fontSize: 13, fontWeight: 700, textDecoration: 'none', border: '1.5px solid #e2e8f0' }}>
              View Privacy Policy <ChevronRight size={13} />
            </Link>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer style={{ background: '#0f172a', padding: '28px 24px', fontFamily: F }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
          <PlatformLogo textColor="#fff" imageWidth={28} fontSize={14} />
          <div style={{ display: 'flex', gap: 22 }}>
            {[['Home','/'],['Privacy','/privacy'],['Terms','/terms'],['Contact','/contact']].map(([l,h]) => (
              <Link key={l} to={h} style={{ fontSize: 13, color: l === 'Terms' ? '#fff' : '#94a3b8', textDecoration: 'none', transition: 'color 0.2s' }}
                onMouseEnter={e => e.target.style.color = '#fff'} onMouseLeave={e => { if (l !== 'Terms') e.target.style.color = '#94a3b8'; }}>{l}</Link>
            ))}
          </div>
          <p style={{ fontSize: 12, color: '#475569', margin: 0 }}>© 2026 Pulse Engine. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
