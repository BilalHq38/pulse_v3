/* landingPage_v2.js — Pulse Engine Landing Page — v2 */
/* eslint-disable */
const { useState, useEffect, useRef, useCallback, memo } = React;

/* ══════════════════════════════════════════════════════════════
   0. GLOBAL STYLES — injected once, covers all design tokens,
      keyframes, class utilities, and responsive breakpoints.
   ══════════════════════════════════════════════════════════════ */
function GlobalStyles() {
  return (
    <style>{`
      /* ── Google Fonts ──────────────────────────────────── */
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Syne:wght@700;800&family=Outfit:wght@400;500;600;700&display=swap');

      /* ── Design Tokens (dark — always dark per user request) */
      :root, [data-theme="dark"] {
        --bg:          #020817;
        --bg2:         #070d1a;
        --fg:          #f1f5f9;
        --fg2:         #cbd5e1;
        --fg3:         #64748b;
        --surface:     #0c1324;
        --surface2:    #111827;
        --border:      rgba(255,255,255,0.07);
        --border2:     rgba(255,255,255,0.14);
        --accent:      #6366f1;
        --accent2:     #818cf8;
        --accent-dim:  rgba(99,102,241,0.12);
        --blue:        #3b82f6;
        --glass-bg:    rgba(7,9,22,0.88);
        --font-d:      'Syne', 'Inter', sans-serif;
        --font-b:      'Inter', system-ui, sans-serif;
        --font-m:      'Outfit', 'Inter', monospace;
        --noise:       url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
      }

      /* ── Reset & Base ──────────────────────────────────── */
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
      html { scroll-behavior: smooth; }
      body {
        background: var(--bg);
        color: var(--fg);
        font-family: var(--font-b);
        font-size: 15px;
        line-height: 1.6;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
        overflow-x: hidden;
      }

      /* ── Scrollbar ─────────────────────────────────────── */
      ::-webkit-scrollbar { width: 6px; }
      ::-webkit-scrollbar-track { background: var(--bg); }
      ::-webkit-scrollbar-thumb { background: rgba(99,102,241,.4); border-radius: 3px; }
      ::-webkit-scrollbar-thumb:hover { background: rgba(99,102,241,.7); }

      /* ── Typography Utilities ──────────────────────────── */
      .display {
        font-family: var(--font-d);
        font-size: clamp(36px, 5.5vw, 68px);
        font-weight: 800;
        line-height: 1.06;
        letter-spacing: -0.04em;
        color: var(--fg);
      }
      .h2 {
        font-family: var(--font-d);
        font-size: clamp(26px, 3.5vw, 44px);
        font-weight: 800;
        line-height: 1.1;
        letter-spacing: -0.03em;
        color: var(--fg);
        margin-bottom: 14px;
      }
      .h3 {
        font-family: var(--font-d);
        font-size: clamp(20px, 2.5vw, 28px);
        font-weight: 700;
        line-height: 1.2;
        letter-spacing: -0.02em;
        color: var(--fg);
        margin-bottom: 10px;
      }
      .body-lg {
        font-size: 17px;
        color: var(--fg2);
        line-height: 1.7;
      }
      .body {
        font-size: 15px;
        color: var(--fg2);
        line-height: 1.65;
      }
      .label {
        display: inline-block;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--accent2);
        margin-bottom: 10px;
      }

      /* ── Layout ────────────────────────────────────────── */
      .container {
        max-width: 1200px;
        margin: 0 auto;
        padding: 0 24px;
      }
      .section     { padding: 100px 24px; }
      .section-sm  { padding: 60px 24px; }

      /* ── Buttons ───────────────────────────────────────── */
      .btn {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 20px;
        border-radius: 10px;
        font-size: 14px;
        font-weight: 600;
        font-family: var(--font-b);
        text-decoration: none;
        cursor: pointer;
        transition: all 0.22s cubic-bezier(0.4,0,0.2,1);
        border: none;
        position: relative;
        overflow: hidden;
        white-space: nowrap;
      }
      .btn::after {
        content: '';
        position: absolute;
        inset: 0;
        background: linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.14) 50%, transparent 60%);
        transform: translateX(-100%);
        transition: transform 0.5s;
      }
      .btn:hover::after { transform: translateX(100%); }

      .btn-primary {
        background: linear-gradient(135deg, #6366f1, #3b82f6);
        color: #fff;
        box-shadow: 0 4px 20px rgba(99,102,241,0.4);
      }
      .btn-primary:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 32px rgba(99,102,241,0.55);
      }
      .btn-ghost {
        background: transparent;
        color: var(--fg2);
        border: 1px solid var(--border2);
      }
      .btn-ghost:hover {
        background: var(--surface);
        color: var(--fg);
        border-color: var(--accent);
      }
      .btn-lg { padding: 14px 28px; font-size: 15px; border-radius: 12px; }
      .btn-sm { padding: 7px 14px;  font-size: 13px; border-radius: 8px;  }

      /* ── Badge ─────────────────────────────────────────── */
      .badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 5px 14px;
        border-radius: 100px;
        background: rgba(99,102,241,0.1);
        border: 1px solid rgba(99,102,241,0.3);
        color: var(--accent2);
        font-size: 12.5px;
        font-weight: 600;
        font-family: var(--font-b);
      }

      /* ── Card ──────────────────────────────────────────── */
      .card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 20px;
        padding: 28px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
        transition: border-color 0.2s, box-shadow 0.2s, transform 0.2s;
      }
      .card:hover {
        border-color: var(--border2);
        box-shadow: 0 8px 40px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08);
        transform: translateY(-2px);
      }

      /* ── Reveal animation ──────────────────────────────── */
      .reveal {
        opacity: 0;
        transform: translateY(20px);
        transition: opacity 0.7s cubic-bezier(0.4,0,0.2,1), transform 0.7s cubic-bezier(0.4,0,0.2,1);
      }
      .reveal.in {
        opacity: 1;
        transform: translateY(0);
      }

      /* ── Responsive ────────────────────────────────────── */
      .hide-mobile  { display: flex; }
      .show-mobile  { display: none; }
      @media (max-width: 768px) {
        .hide-mobile { display: none !important; }
        .show-mobile { display: flex !important; }
        .section     { padding: 60px 20px; }
        .section-sm  { padding: 40px 20px; }
      }

      /* ── Keyframe Animations ───────────────────────────── */
      @keyframes fadeIn {
        from { opacity: 0; }
        to   { opacity: 1; }
      }
      @keyframes slideUp {
        from { opacity: 0; transform: translateY(16px); }
        to   { opacity: 1; transform: translateY(0);    }
      }
      @keyframes slideDown {
        from { opacity: 1; transform: translateY(0);    }
        to   { opacity: 0; transform: translateY(10px); }
      }
      @keyframes blink {
        0%, 100% { opacity: 1; }
        50%       { opacity: 0; }
      }
      @keyframes typing {
        0%, 80%, 100% { transform: translateY(0); }
        40%            { transform: translateY(-4px); }
      }
      @keyframes msg-in {
        from { opacity: 0; transform: translateY(6px); }
        to   { opacity: 1; transform: translateY(0);   }
      }
      @keyframes marquee {
        from { transform: translateX(0); }
        to   { transform: translateX(-50%); }
      }
      @keyframes pulseGlow {
        0%, 100% { box-shadow: 0 4px 14px rgba(99,102,241,.35); }
        50%       { box-shadow: 0 4px 26px rgba(99,102,241,.65); }
      }
      @keyframes heroBlobA {
        0%, 100% { transform: translate(0, 0) scale(1); }
        50%       { transform: translate(40px, -30px) scale(1.08); }
      }
      @keyframes heroBlobB {
        0%, 100% { transform: translate(0, 0) scale(1); }
        50%       { transform: translate(-30px, 20px) scale(1.06); }
      }
      @keyframes scrollProg {
        from { transform: scaleX(0); }
        to   { transform: scaleX(1); }
      }

      /* ── Reduced motion ────────────────────────────────── */
      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
          animation-duration: 0.01ms !important;
          animation-iteration-count: 1 !important;
          transition-duration: 0.01ms !important;
        }
        .reveal { opacity: 1; transform: none; }
      }

      /* ── Stats grid responsive ─────────────────────────── */
      #stats .stats-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 2px;
      }
      @media (max-width: 640px) {
        #stats .stats-grid { grid-template-columns: 1fr 1fr; }
      }

      /* ── Testimonials grid responsive ──────────────────── */
      .testimonials-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 20px;
      }
      @media (max-width: 768px) {
        .testimonials-grid { grid-template-columns: 1fr; }
      }

      /* ── Features tab panel responsive ────────────────── */
      .features-panel-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 48px;
        align-items: center;
      }
      @media (max-width: 1024px) {
        .features-panel-grid { grid-template-columns: 1fr; }
      }

      /* ── Footer grid responsive ────────────────────────── */
      .footer-grid {
        display: grid;
        grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
        gap: 40px;
        margin-bottom: 48px;
      }
      @media (max-width: 1100px) {
        .footer-grid { grid-template-columns: 1fr 1fr 1fr; }
      }
      @media (max-width: 700px) {
        .footer-grid { grid-template-columns: 1fr 1fr; }
      }

      /* ── Pricing grid responsive ───────────────────────── */
      .pricing-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 20px;
        align-items: stretch;
      }
      @media (max-width: 900px) {
        .pricing-grid { grid-template-columns: 1fr; }
        .pricing-popular-card { transform: scale(1) !important; }
      }

      /* ── Globe tag focus visible ───────────────────────── */
      .globe-tag:focus-visible {
        outline: 2px solid var(--accent2);
        outline-offset: 2px;
      }

      /* ── Noise overlay ─────────────────────────────────── */
      .noise-overlay {
        position: absolute;
        inset: 0;
        background-image: var(--noise);
        opacity: 0.035;
        pointer-events: none;
        z-index: 0;
      }

      /* ── Shimmer btn ───────────────────────────────────── */
      @keyframes shimmer {
        from { background-position: -200% center; }
        to   { background-position:  200% center; }
      }
    `}</style>
  );
}

/* ══════════════════════════════════════════════════════════════
   0b. SCROLL PROGRESS BAR
   ══════════════════════════════════════════════════════════════ */
function ScrollProgress() {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const onScroll = () => {
      const el = document.documentElement;
      const pct = (el.scrollTop / (el.scrollHeight - el.clientHeight)) * 100;
      setWidth(Math.min(pct, 100));
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, zIndex: 99999,
      width: `${width}%`, height: 2,
      background: 'linear-gradient(90deg, #6366f1, #3b82f6, #818cf8)',
      transition: 'width 0.1s linear',
      transformOrigin: 'left',
    }} aria-hidden="true" />
  );
}

/* ══════════════════════════════════════════════════════════════
   1. PLATFORM LOGO — kept EXACTLY as original (font, animation)
   ══════════════════════════════════════════════════════════════ */
function PlatformLogo({ textColor, imageWidth, fontSize, fontWeight }) {
  const [hovered, setHovered] = useState(false);
  return (
    <a href="#"
       onMouseEnter={() => setHovered(true)}
       onMouseLeave={() => setHovered(false)}
       style={{ display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none', cursor: 'pointer' }}>
      {/* perspective wrapper fixes the 3-D flip visibility bug */}
      <div style={{ perspective: 600 }}>
        <div className="platform-logo-icon" style={{
          width: imageWidth || 34,
          height: imageWidth || 34,
          borderRadius: 10,
          background: 'linear-gradient(135deg,#2563eb,#6366f1)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'transform 0.5s cubic-bezier(.4,0,.2,1)',
          transform: hovered ? 'rotateY(180deg)' : 'rotateY(0deg)',
          animation: 'pulseGlow 3s ease infinite',
          boxShadow: '0 4px 14px rgba(99,102,241,.35)',
        }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
        </div>
      </div>
      <span style={{
        fontSize: fontSize || 17,
        fontWeight: fontWeight || 800,
        color: textColor || 'var(--fg)',
        letterSpacing: '-0.5px',
        fontFamily: 'var(--font-d)',
      }}>
        Pulse Engine
      </span>
    </a>
  );
}

/* ══════════════════════════════════════════════════════════════
   2. NAVIGATION
   ══════════════════════════════════════════════════════════════ */
const MenuIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>
  </svg>
);
const XIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);

function NavLink({ href, children }) {
  const [hov, setHov] = useState(false);
  return (
    <a href={href}
       onMouseEnter={() => setHov(true)}
       onMouseLeave={() => setHov(false)}
       style={{
         fontSize: 14, color: hov ? 'var(--fg)' : 'var(--fg2)',
         textDecoration: 'none', fontWeight: 500,
         padding: '6px 4px', position: 'relative',
         transition: 'color .2s', letterSpacing: '-.01em',
       }}>
      {children}
      <span style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        height: 2, borderRadius: 2,
        background: 'linear-gradient(90deg,#6366f1,#3b82f6)',
        transform: hov ? 'scaleX(1)' : 'scaleX(0)',
        transformOrigin: 'left',
        transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)',
      }} />
    </a>
  );
}

function PulseNav() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 24);
    window.addEventListener('scroll', fn, { passive: true });
    fn();
    return () => window.removeEventListener('scroll', fn);
  }, []);

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [mobileOpen]);

  const links = [
    { href: '#features',  label: 'Features'     },
    { href: '#globe',     label: 'Integrations'  },
    { href: '#pricing',   label: 'Pricing'       },
    { href: '#faq',       label: 'FAQ'           },
  ];

  const closeMobile = () => { setMobileOpen(false); document.body.style.overflow = ''; };

  return (
    <>
      <nav
        className="pe-nav"
        role="navigation"
        aria-label="Main navigation"
        style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 9000,
          height: 68,
          background: scrolled ? 'var(--glass-bg)' : 'transparent',
          backdropFilter: scrolled ? 'blur(20px)' : 'none',
          WebkitBackdropFilter: scrolled ? 'blur(20px)' : 'none',
          borderBottom: scrolled ? '1px solid var(--border)' : '1px solid transparent',
          transition: 'all .35s cubic-bezier(.4,0,.2,1)',
        }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo fontWeight={800} />

          <div className="hide-mobile" style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
            {links.map(l => <NavLink key={l.href} href={l.href}>{l.label}</NavLink>)}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div className="hide-mobile" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <a href="#" style={{
                fontSize: 13.5, fontWeight: 500, color: 'var(--fg2)',
                textDecoration: 'none', padding: '8px 14px', borderRadius: 8,
                border: '1px solid var(--border)', background: 'transparent', transition: 'all .2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface)'; e.currentTarget.style.color = 'var(--fg)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg2)'; }}>
                Sign In
              </a>
              <a href="#" className="btn btn-primary btn-sm">Get Started</a>
            </div>

            <button
              className="show-mobile"
              onClick={() => setMobileOpen(!mobileOpen)}
              aria-expanded={mobileOpen}
              aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
              style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--fg)', borderRadius: 8, padding: 7, cursor: 'pointer', display: 'flex' }}>
              {mobileOpen ? <XIcon /> : <MenuIcon />}
            </button>
          </div>
        </div>
      </nav>

      {mobileOpen && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 8999, background: 'var(--bg)', paddingTop: 80, paddingLeft: 24, paddingRight: 24, display: 'flex', flexDirection: 'column', gap: 4, animation: 'fadeIn .2s ease' }}>
          {links.map(l =>
            <a key={l.href} href={l.href} onClick={closeMobile}
               style={{ display: 'block', padding: '14px 16px', fontSize: 18, fontWeight: 600, fontFamily: 'var(--font-d)', color: 'var(--fg)', textDecoration: 'none', borderRadius: 10, borderBottom: '1px solid var(--border)' }}>
              {l.label}
            </a>
          )}
          <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <a href="#" className="btn btn-ghost" style={{ justifyContent: 'center' }} onClick={closeMobile}>Sign In</a>
            <a href="#" className="btn btn-primary" style={{ justifyContent: 'center' }} onClick={closeMobile}>Get Started Free</a>
          </div>
        </div>
      )}
    </>
  );
}

/* ══════════════════════════════════════════════════════════════
   3. HERO SECTION
   ══════════════════════════════════════════════════════════════ */
const PHRASES = ['Win More Customers.', 'Close Deals Faster.', 'Delight at Scale.', 'Grow Without Limits.'];

function TypeWriter() {
  const [idx, setIdx] = useState(0);
  const [text, setText] = useState('');
  const [del, setDel] = useState(false);
  const [pause, setPause] = useState(false);

  useEffect(() => {
    if (pause) {
      const t = setTimeout(() => { setPause(false); setDel(true); }, 1800);
      return () => clearTimeout(t);
    }
    const phrase = PHRASES[idx];
    if (!del) {
      if (text.length < phrase.length) {
        const t = setTimeout(() => setText(phrase.slice(0, text.length + 1)), 58);
        return () => clearTimeout(t);
      } else { setPause(true); }
    } else {
      if (text.length > 0) {
        const t = setTimeout(() => setText(text.slice(0, -1)), 28);
        return () => clearTimeout(t);
      } else {
        setDel(false);
        setIdx(i => (i + 1) % PHRASES.length);
      }
    }
  }, [text, del, idx, pause]);

  return (
    <span style={{ background: 'linear-gradient(135deg,#818cf8,#2563eb)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text', display: 'inline-block', minWidth: 8 }}>
      {text || '\u00A0'}
      <span style={{ display: 'inline-block', width: 3, height: '0.85em', background: 'var(--accent2)', borderRadius: 2, verticalAlign: 'middle', marginLeft: 2, animation: 'blink 1s step-end infinite' }} aria-hidden="true" />
    </span>
  );
}

const BADGE_STATS = [
  '★ Rated #1 AI CRM Platform · 2025',
  '⚡ 70% of queries resolved by AI automatically',
  '🚀 Trusted by 10,000+ teams worldwide',
  '💬 WhatsApp · Instagram · Facebook · Email · Web Chat',
];

function AnimatedBadge() {
  const [idx, setIdx]   = useState(0);
  const [fade, setFade] = useState(true);
  const fading = useRef(false);

  useEffect(() => {
    const id = setInterval(() => {
      if (fading.current) return;
      fading.current = true;
      setFade(false);
      const t = setTimeout(() => {
        setIdx(i => (i + 1) % BADGE_STATS.length);
        setFade(true);
        fading.current = false;
      }, 300);
      return () => clearTimeout(t);
    }, 3000);
    return () => clearInterval(id);
  }, []);

  return (
    <span className="badge"
      style={{ marginBottom: 28, transition: 'opacity .3s ease', opacity: fade ? 1 : 0, display: 'inline-flex', padding: '6px 16px', fontSize: 13, letterSpacing: '.01em' }}>
      <span style={{ marginRight: 7, fontSize: 11 }} aria-hidden="true">✦</span>
      {BADGE_STATS[idx]}
    </span>
  );
}

function ChannelIcon({ ch, size = 14 }) {
  const map = { whatsapp: '#25D366', facebook: '#1877F2', instagram: '#E1306C', email: '#0ea5e9', web_chat: '#8b5cf6' };
  const letters = { whatsapp: 'W', facebook: 'F', instagram: 'I', email: 'E', web_chat: '💬' };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, borderRadius: 3, background: map[ch] || '#475569', fontSize: size * 0.55, fontWeight: 700, color: '#fff', flexShrink: 0 }}>
      {letters[ch]}
    </span>
  );
}

function InboxPreview() {
  const [msgIdx, setMsgIdx] = useState(0);
  const [showTyping, setShowTyping] = useState(false);
  const [msgs, setMsgs] = useState([]);
  const [started, setStarted] = useState(false);

  const conversation = [
    { from: 'customer', text: "Hi! My order hasn't arrived yet. Can you check?" },
    { from: 'ai',       text: "Hi Sarah! Found order #PE-2847 — it shipped May 24th and is arriving today by 8 PM 📦" },
    { from: 'customer', text: "Amazing, thank you! 🎉" },
  ];

  /* delay start by 800ms to avoid competing with page render */
  useEffect(() => {
    const t = setTimeout(() => setStarted(true), 800);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (!started) return;
    let t;
    const addNext = () => {
      if (msgIdx >= conversation.length) {
        t = setTimeout(() => { setMsgs([]); setMsgIdx(0); setShowTyping(false); }, 3500);
        return;
      }
      const next = conversation[msgIdx];
      if (next.from === 'ai') {
        setShowTyping(true);
        t = setTimeout(() => {
          setShowTyping(false);
          setMsgs(p => [...p, next]);
          setMsgIdx(i => i + 1);
          setTimeout(addNext, 1600);
        }, 1500);
      } else {
        setMsgs(p => [...p, next]);
        setMsgIdx(i => i + 1);
        t = setTimeout(addNext, 1300);
      }
    };
    t = setTimeout(addNext, msgIdx === 0 ? 0 : 0);
    return () => clearTimeout(t);
  }, [msgIdx, started]);

  const columns = [
    { ch: 'whatsapp', label: 'WhatsApp', color: '#25D366', count: 4, convos: [
      { name: 'Sarah M.',   preview: "My order hasn't arrived", time: '2m',  unread: 2, ai: true,  sentiment: 'positive' },
      { name: 'James K.',   preview: 'Is the Pro plan right?',  time: '8m',  unread: 1, ai: false, sentiment: 'neutral'  },
      { name: 'Priya S.',   preview: "Thanks! That worked 🎉",  time: '15m', unread: 0, ai: true,  sentiment: 'positive' },
    ]},
    { ch: 'facebook', label: 'Facebook', color: '#1877F2', count: 2, convos: [
      { name: 'TechCorp',  preview: 'Enterprise pricing?',     time: '10m', unread: 3, ai: true,  sentiment: 'positive' },
      { name: 'Mike D.',   preview: 'Response time was bad!',  time: '1h',  unread: 0, ai: true,  sentiment: 'negative' },
    ]},
    { ch: 'instagram', label: 'Instagram', color: '#E1306C', count: 2, convos: [
      { name: '@maya_d',   preview: 'Love your platform! 😍',  time: '5m',  unread: 3, ai: true,  sentiment: 'positive' },
      { name: '@techxyz',  preview: 'API integration help?',   time: '20m', unread: 1, ai: false, sentiment: 'neutral'  },
    ]},
  ];
  const sentColor = { positive: '#22c55e', neutral: '#f59e0b', negative: '#ef4444' };

  return (
    <div style={{ background: 'rgba(12,19,36,0.85)', borderRadius: 20, border: '1px solid rgba(255,255,255,0.1)', boxShadow: '0 32px 80px rgba(0,0,0,.6), 0 0 0 1px rgba(99,102,241,.12)', overflow: 'hidden', userSelect: 'none', backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '9px 14px', background: 'rgba(7,9,22,0.8)', borderBottom: '1px solid var(--border)' }}>
        <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#ff5f57' }} />
        <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#febc2e' }} />
        <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#28c840' }} />
        <div style={{ flex: 1, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 5, padding: '3px 10px', fontSize: 10.5, color: 'var(--fg3)', marginLeft: 8, fontFamily: 'var(--font-m)' }}>app.pulseengine.io/inbox</div>
        <div style={{ display: 'flex', gap: 3 }}>
          {['W','F','I','E'].map((l, i) => (
            <div key={l} style={{ width: 16, height: 16, borderRadius: 4, background: ['#25D366','#1877F2','#E1306C','#0ea5e9'][i], display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 8, fontWeight: 700, color: '#fff' }}>{l}</div>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', height: 248 }}>
        {columns.map((col, ci) => (
          <div key={col.ch} style={{ width: 160, flexShrink: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', background: ci === 0 ? 'rgba(7,9,22,0.6)' : 'var(--surface)' }}>
            <div style={{ padding: '7px 10px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6, background: 'var(--surface)' }}>
              <ChannelIcon ch={col.ch} size={14} />
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg)', flex: 1 }}>{col.label}</span>
              <span style={{ fontSize: 9.5, fontWeight: 600, color: 'var(--fg3)', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 4, padding: '0px 5px' }}>{col.count}</span>
              <div style={{ width: 14, height: 14, borderRadius: 4, border: '1px solid var(--accent)', background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                <span style={{ fontSize: 9, color: 'var(--accent2)', lineHeight: 1 }}>+</span>
              </div>
            </div>
            <div style={{ flex: 1, padding: '5px 6px', display: 'flex', flexDirection: 'column', gap: 4, overflow: 'hidden' }}>
              {col.convos.map((c, i) => (
                <div key={i} style={{ padding: '7px 8px', borderRadius: 9, background: ci === 0 && i === 0 ? 'var(--accent-dim)' : 'var(--surface)', border: ci === 0 && i === 0 ? '1px solid rgba(99,102,241,.2)' : '1px solid var(--border)', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                    <div style={{ width: 22, height: 22, borderRadius: '50%', background: col.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, fontWeight: 700, color: '#fff', flexShrink: 0 }}>{c.name[0]}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 1 }}>
                        <span style={{ fontSize: 10.5, fontWeight: c.unread ? 700 : 500, color: 'var(--fg)', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis', maxWidth: 70 }}>{c.name}</span>
                        <span style={{ fontSize: 9, color: 'var(--fg3)', flexShrink: 0 }}>{c.time}</span>
                      </div>
                      <p style={{ fontSize: 9.5, color: 'var(--fg3)', margin: 0, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{c.preview}</p>
                      <div style={{ display: 'flex', gap: 3, marginTop: 3 }}>
                        {c.ai && <span style={{ fontSize: 8.5, padding: '0px 5px', borderRadius: 100, background: 'var(--accent-dim)', color: 'var(--accent2)', border: '1px solid rgba(99,102,241,.2)', fontWeight: 600 }}>AI</span>}
                        <span style={{ fontSize: 8.5, padding: '0px 5px', borderRadius: 100, background: `${sentColor[c.sentiment]}18`, color: sentColor[c.sentiment], border: `1px solid ${sentColor[c.sentiment]}30` }}>{c.sentiment}</span>
                      </div>
                    </div>
                    {c.unread > 0 && <span style={{ width: 14, height: 14, borderRadius: '50%', background: 'var(--accent)', color: '#fff', fontSize: 8, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{c.unread}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div style={{ padding: '7px 12px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, background: 'var(--surface)' }}>
            <div style={{ width: 26, height: 26, borderRadius: '50%', background: '#25D366', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: '#fff', flexShrink: 0 }}>S</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--fg)' }}>Sarah M.</div>
              <div style={{ fontSize: 9.5, color: '#25D366', display: 'flex', alignItems: 'center', gap: 3 }}>
                <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#25D366', display: 'inline-block' }} /> WhatsApp
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 20, background: 'var(--accent-dim)', border: '1px solid rgba(99,102,241,.2)' }}>
              <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#22c55e', display: 'inline-block', animation: 'blink 1.5s ease infinite' }} aria-hidden="true" />
              <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--accent2)' }}>AI Active</span>
            </div>
          </div>
          <div style={{ flex: 1, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 6, justifyContent: 'flex-end', overflow: 'hidden', background: 'rgba(2,8,23,0.6)' }}>
            {msgs.map((m, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: m.from === 'ai' ? 'flex-start' : 'flex-end', animation: 'msg-in .3s ease' }}>
                <div style={{ maxWidth: '80%', padding: '7px 10px', borderRadius: m.from === 'ai' ? '12px 12px 12px 3px' : '12px 12px 3px 12px', background: m.from === 'ai' ? 'var(--surface2)' : 'linear-gradient(135deg,var(--accent),var(--blue))', fontSize: 10.5, color: m.from === 'ai' ? 'var(--fg)' : '#fff', lineHeight: 1.45, border: m.from === 'ai' ? '1px solid var(--border)' : 'none' }}>
                  {m.text}
                </div>
              </div>
            ))}
            {showTyping && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, animation: 'fadeIn .2s ease' }}>
                <div style={{ display: 'flex', gap: 3, padding: '7px 10px', background: 'var(--surface2)', borderRadius: '12px 12px 12px 3px', border: '1px solid var(--border)' }}>
                  {[0,1,2].map(i => <span key={i} style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--accent2)', display: 'block', animation: `typing .8s ease ${i*.15}s infinite` }} aria-hidden="true" />)}
                </div>
                <span style={{ fontSize: 9.5, color: 'var(--fg3)' }}>AI responding…</span>
              </div>
            )}
          </div>
          <div style={{ padding: '6px 10px', borderTop: '1px solid var(--border)', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px' }}>
              <span style={{ flex: 1, fontSize: 10.5, color: 'var(--fg3)' }}>Reply via WhatsApp…</span>
              <div style={{ width: 22, height: 22, borderRadius: 6, background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function HeroSection({ onWatchDemo }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => { const t = setTimeout(() => setVisible(true), 80); return () => clearTimeout(t); }, []);

  return (
    <section style={{ position: 'relative', minHeight: '100vh', display: 'flex', alignItems: 'center', overflow: 'hidden', paddingTop: 120, paddingBottom: 80 }}>
      <div style={{ position: 'absolute', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }} aria-hidden="true">
        <div style={{ position: 'absolute', top: -200, right: -100, width: 700, height: 700, borderRadius: '50%', background: 'radial-gradient(circle,rgba(99,102,241,.16) 0%,transparent 70%)', filter: 'blur(40px)', animation: 'heroBlobA 12s ease infinite' }} />
        <div style={{ position: 'absolute', top: 100, left: -150, width: 500, height: 500, borderRadius: '50%', background: 'radial-gradient(circle,rgba(37,99,235,.12) 0%,transparent 70%)', filter: 'blur(40px)', animation: 'heroBlobB 16s ease infinite' }} />
        <div style={{ position: 'absolute', bottom: -100, left: '35%', width: 400, height: 400, borderRadius: '50%', background: 'radial-gradient(circle,rgba(129,140,248,.08) 0%,transparent 70%)', filter: 'blur(40px)' }} />
        <div style={{ position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(99,102,241,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(99,102,241,.04) 1px,transparent 1px)', backgroundSize: '60px 60px', opacity: .5 }} />
        <div className="noise-overlay" />
      </div>

      <div className="container" style={{ position: 'relative', zIndex: 1, width: '100%' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', textAlign: 'center', marginBottom: 60 }}>
          <div style={{ opacity: visible ? 1 : 0, transform: visible ? 'translateY(0)' : 'translateY(20px)', transition: 'all .6s cubic-bezier(.4,0,.2,1)' }}>
            <AnimatedBadge />
          </div>

          {/* FIX: Single <h1> wrapping both lines — no more double h1 */}
          <h1 className="display" style={{ color: 'var(--fg)', marginBottom: 28, opacity: visible ? 1 : 0, transform: visible ? 'translateY(0)' : 'translateY(24px)', transition: 'all .7s cubic-bezier(.4,0,.2,1) .08s' }}>
            <TypeWriter />
            <br />
            <span style={{ color: 'var(--fg)' }}>All From One Inbox.</span>
          </h1>

          <p className="body-lg" style={{ maxWidth: 560, margin: '0 auto 36px', opacity: visible ? 1 : 0, transform: visible ? 'translateY(0)' : 'translateY(20px)', transition: 'all .7s cubic-bezier(.4,0,.2,1) .16s' }}>
            Unify WhatsApp, Instagram, Facebook, Email &amp; Web Chat into one AI-powered workspace. Capture leads, automate follow-ups, and close deals — 24/7.
          </p>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'center', marginBottom: 28, opacity: visible ? 1 : 0, transform: visible ? 'translateY(0)' : 'translateY(16px)', transition: 'all .7s cubic-bezier(.4,0,.2,1) .24s' }}>
            <a href="#" className="btn btn-primary btn-lg">
              Start Free Trial
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </a>
            <button onClick={onWatchDemo} className="btn btn-ghost btn-lg" aria-label="Watch product demo">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              Watch Demo
            </button>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 22, fontSize: 13, color: 'var(--fg3)', opacity: visible ? 1 : 0, transition: 'all .7s cubic-bezier(.4,0,.2,1) .32s' }}>
            {['Free 14-day trial','No credit card required','Cancel anytime'].map(t => (
              <span key={t} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
                {t}
              </span>
            ))}
          </div>
        </div>

        {/* Floating stat badges */}
        <div style={{ maxWidth: 820, margin: '0 auto', position: 'relative', opacity: visible ? 1 : 0, transform: visible ? 'translateY(0)' : 'translateY(48px)', transition: 'all 1s cubic-bezier(.4,0,.2,1) .4s' }}>
          <div style={{ position: 'absolute', top: -18, left: -20, zIndex: 10, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '6px 12px', fontSize: 11, fontWeight: 600, color: 'var(--fg)', boxShadow: '0 8px 24px rgba(0,0,0,.4)', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#22c55e', animation: 'blink 2s ease infinite' }} aria-hidden="true" />
            🤖 AI resolved · 2s ago
          </div>
          <div style={{ position: 'absolute', top: 30, right: -24, zIndex: 10, background: 'var(--surface)', border: '1px solid rgba(99,102,241,.3)', borderRadius: 10, padding: '6px 12px', fontSize: 11, fontWeight: 600, color: 'var(--accent2)', boxShadow: '0 8px 24px rgba(0,0,0,.4)', whiteSpace: 'nowrap' }}>
            📈 Lead score: <strong>88</strong>
          </div>
          <InboxPreview />
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   4. TRUST BAR (NEW)
   ══════════════════════════════════════════════════════════════ */
function TrustBar() {
  const brands = ['Shopify','Salesforce','HubSpot','Zendesk','Stripe','Intercom','Twilio','Slack'];
  return (
    <section style={{ padding: '28px 24px', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', background: 'var(--bg2)', overflow: 'hidden' }} aria-label="Trusted by">
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: 20 }}>
          <span style={{ fontSize: 11, color: 'var(--fg3)', letterSpacing: '.12em', textTransform: 'uppercase', fontFamily: 'var(--font-m)' }}>Trusted by teams at</span>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center', gap: '14px 28px' }}>
          {brands.map(b => (
            <span key={b} style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--fg3)', letterSpacing: '.02em', opacity: .5, fontFamily: 'var(--font-d)', transition: 'opacity .2s, color .2s', cursor: 'default' }}
              onMouseEnter={e => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.color = 'var(--fg)'; }}
              onMouseLeave={e => { e.currentTarget.style.opacity = '0.5'; e.currentTarget.style.color = 'var(--fg3)'; }}>
              {b}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   5. STATS SECTION
   ══════════════════════════════════════════════════════════════ */
function Counter({ value, suffix = '', prefix = '', duration = 1800 }) {
  const [count, setCount] = useState(0);
  const [visible, setVisible] = useState(false);
  const ref = useRef(null);
  const num = parseFloat(String(value).replace(/[^0-9.]/g, '')) || 0;
  const hasDecimal = String(value).includes('.');

  useEffect(() => {
    const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setVisible(true); obs.disconnect(); } }, { threshold: .2 });
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let start = 0, frame;
    const step = () => {
      start += num / (duration / 16);
      if (start >= num) { setCount(num); return; }
      setCount(hasDecimal ? Math.round(start * 10) / 10 : Math.floor(start));
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [visible, num, duration, hasDecimal]);

  return <span ref={ref}>{prefix}{hasDecimal ? count.toFixed(1) : count}{suffix}</span>;
}

function StatsSection() {
  const stats = [
    { value: '70', suffix: '%', label: 'AI Resolution Rate',   sub: 'Queries resolved without human'  },
    { value: '3',  suffix: '×', label: 'Faster Response Time', sub: 'vs. manual handling'             },
    { value: '40', suffix: '%', label: 'More Leads Captured',  sub: 'From inbound conversations'      },
    { value: '4.9',suffix: '/5',label: 'Average CSAT Score',   sub: 'Industry avg: 3.8'               },
  ];
  const accentColors = ['#6366f1','#3b82f6','#22c55e','#f59e0b'];
  return (
    <section id="stats" className="section-sm" style={{ background: 'var(--bg2)', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
      <div className="container">
        <div className="stats-grid">
          {stats.map((s, i) => (
            <div key={s.label} style={{ padding: '24px 16px', textAlign: 'center', borderRight: i < 3 ? '1px solid var(--border)' : 'none' }}>
              <div style={{ fontFamily: 'var(--font-d)', fontSize: 'clamp(28px,3.5vw,44px)', fontWeight: 800, color: accentColors[i], lineHeight: 1, marginBottom: 6, textShadow: `0 0 40px ${accentColors[i]}55` }}>
                <Counter value={s.value} suffix={s.suffix} duration={1800 + i * 150} />
              </div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg)', marginBottom: 3 }}>{s.label}</div>
              <div style={{ fontSize: 11.5, color: 'var(--fg3)' }}>{s.sub}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   6. FEATURES SECTION
   ══════════════════════════════════════════════════════════════ */
function FeaturesChIcon({ ch, size = 22 }) {
  const cfg = {
    whatsapp:  { bg: '#25D366',   svg: <path fill="white" d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/> },
    facebook:  { bg: '#1877F2',   svg: <path fill="white" d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/> },
    instagram: { bg: 'radial-gradient(circle at 30% 107%,#fdf497 0%,#fd5949 45%,#d6249f 60%,#285AEB 90%)', svg: <path fill="white" d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/> },
    email:     { bg: 'linear-gradient(135deg,#0ea5e9,#0284c7)', svg: <><path fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" points="22,6 12,13 2,6"/></> },
    web_chat:  { bg: '#1e293b',   svg: <path fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/> },
  };
  const c = cfg[ch] || cfg.web_chat;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, borderRadius: size * 0.22, background: c.bg, flexShrink: 0 }}>
      <svg viewBox="0 0 24 24" width={size*.68} height={size*.68}>{c.svg}</svg>
    </span>
  );
}

function InboxPreviewUI() {
  const cols = [
    { ch: 'whatsapp', label: 'WhatsApp', count: 2 },
    { ch: 'facebook', label: 'Facebook', count: 0 },
    { ch: 'instagram', label: 'Instagram', count: 0 },
  ];
  /* FIX: replaced test names "Ali Bhi"/"M Bilal Zong" with professional personas */
  const convos = [
    { name: 'Sarah Mitchell', initials: 'S', bg: '#10b981', time: '10:11 AM', score: 88, unread: 2,
      msg: "Hi, I need help with my recent order — it hasn't arrived yet.",
      reply: "Hi Sarah! Order #PE-2847 shipped May 24th and arrives today by 8 PM 📦", ai: true },
    { name: 'James Torres',   initials: 'J', bg: '#6366f1', time: '09:43 AM', score: 64, unread: 1,
      msg: 'What is the difference between your Pro and Enterprise plans?',
      reply: 'Great question! Pro is great for growing teams. Enterprise adds SSO, custom AI agents and dedicated support.', ai: true },
    { name: 'Priya Sharma',   initials: 'P', bg: '#f59e0b', time: '09:12 AM', score: 52, unread: 0,
      msg: "Thanks! That worked perfectly 🎉",
      reply: "Wonderful! Let me know if you need anything else.", ai: true },
  ];

  return (
    <div style={{ background: 'var(--surface)', borderRadius: 14, border: '1px solid var(--border)', overflow: 'hidden', minHeight: 320 }}>
      <div style={{ display: 'flex', gap: 0 }}>
        <div style={{ width: 80, background: 'var(--bg2)', borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 4, padding: '10px 6px' }}>
          {cols.map(c => (
            <div key={c.ch} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, padding: '8px 4px', borderRadius: 10, cursor: 'pointer', position: 'relative' }}>
              <FeaturesChIcon ch={c.ch} size={24} />
              <span style={{ fontSize: 8.5, fontWeight: 600, color: 'var(--fg3)' }}>{c.label}</span>
              {c.count > 0 && <span style={{ position: 'absolute', top: 4, right: 4, width: 14, height: 14, borderRadius: '50%', background: 'var(--accent)', color: '#fff', fontSize: 8, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{c.count}</span>}
            </div>
          ))}
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)', fontSize: 11, fontWeight: 700, color: 'var(--fg)', background: 'var(--surface)' }}>All Conversations</div>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            {convos.map((c, i) => (
              <div key={i} style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', background: i === 0 ? 'var(--accent-dim)' : 'transparent', cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <div style={{ width: 32, height: 32, borderRadius: '50%', background: c.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, color: '#fff', flexShrink: 0, position: 'relative' }}>
                    {c.initials}
                    {c.ai && <span style={{ position: 'absolute', bottom: -2, right: -2, width: 12, height: 12, borderRadius: '50%', background: 'var(--accent)', border: '2px solid var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><span style={{ fontSize: 6, color: '#fff', fontWeight: 800 }}>AI</span></span>}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--fg)' }}>{c.name}</span>
                      <span style={{ fontSize: 10, color: 'var(--fg3)' }}>{c.time}</span>
                    </div>
                    <p style={{ fontSize: 11, color: 'var(--fg3)', margin: 0, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{c.msg}</p>
                    <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                      <span style={{ fontSize: 9, padding: '1px 7px', borderRadius: 100, background: 'var(--accent-dim)', color: 'var(--accent2)', border: '1px solid rgba(99,102,241,.2)', fontWeight: 600 }}>AI handled</span>
                      <span style={{ fontSize: 9, padding: '1px 7px', borderRadius: 100, background: 'rgba(34,197,94,.1)', color: '#22c55e', fontWeight: 600 }}>Score: {c.score}</span>
                    </div>
                  </div>
                  {c.unread > 0 && <span style={{ width: 16, height: 16, borderRadius: '50%', background: 'var(--accent)', color: '#fff', fontSize: 9, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{c.unread}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function AutomationPreviewUI() {
  const nodes = [
    { type: 'Trigger',   label: 'New WhatsApp message received',      ico: '⚡', color: '#f59e0b' },
    { type: 'Condition', label: 'AI: Is it a product enquiry?',        ico: '🤖', color: '#818cf8' },
    { type: 'Action',    label: 'Send product catalogue reply',         ico: '📨', color: '#22c55e' },
    { type: 'Action',    label: 'Score lead & update CRM pipeline',     ico: '🎯', color: '#3b82f6' },
    { type: 'Condition', label: 'Lead score ≥ 70?',                    ico: '📊', color: '#ec4899' },
    { type: 'Action',    label: 'Assign to sales agent + send Slack',   ico: '👤', color: '#f97316' },
  ];
  return (
    <div style={{ background: 'var(--surface)', borderRadius: 14, border: '1px solid var(--border)', padding: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg)', marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>⚙️ Lead Qualification Flow</span>
        <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 100, background: 'rgba(34,197,94,.1)', color: '#22c55e', border: '1px solid rgba(34,197,94,.2)' }}>● Active</span>
      </div>
      {nodes.map((n, i) => (
        <React.Fragment key={i}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface2)', cursor: 'pointer', transition: 'border-color .2s' }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = n.color; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; }}>
            <span style={{ fontSize: 14, flexShrink: 0 }}>{n.ico}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 9.5, fontWeight: 700, color: n.color, letterSpacing: '.08em', textTransform: 'uppercase' }}>{n.type}</div>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--fg)', marginTop: 1 }}>{n.label}</div>
            </div>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--fg3)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
          </div>
          {i < nodes.length - 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingLeft: 22, height: 18 }}>
              <div style={{ width: 2, height: 18, background: 'var(--border)', borderRadius: 2 }} />
            </div>
          )}
        </React.Fragment>
      ))}
      <div style={{ display: 'flex', gap: 6, marginTop: 14 }}>
        <button style={{ fontSize: 11, padding: '5px 12px', borderRadius: 7, border: '1px dashed var(--border2)', background: 'transparent', color: 'var(--fg3)', cursor: 'pointer', fontFamily: 'var(--font-b)' }}>+ Add Step</button>
        <button style={{ fontSize: 11, padding: '5px 12px', borderRadius: 7, border: '1px dashed var(--border2)', background: 'transparent', color: 'var(--fg3)', cursor: 'pointer', fontFamily: 'var(--font-b)' }}>+ Add Branch</button>
        <button style={{ fontSize: 11, padding: '5px 12px', borderRadius: 7, border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontFamily: 'var(--font-b)', marginLeft: 'auto' }}>Save</button>
      </div>
    </div>
  );
}

function LeadsPreviewUI() {
  const leads = [
    { name: 'Sarah Mitchell', initials: 'S', bg: '#2563eb',  grade: 'hot',  score: 88, status: 'Qualified', ch: 'whatsapp', company: 'Northstar Labs' },
    { name: 'James Torres',   initials: 'J', bg: '#8b5cf6',  grade: 'warm', score: 64, status: 'Contacted', ch: 'instagram', company: 'TechVentures' },
    { name: 'Priya Sharma',   initials: 'P', bg: '#10b981',  grade: 'warm', score: 52, status: 'New',       ch: 'email',     company: 'GrowthCo' },
    { name: 'Marcus Webb',    initials: 'M', bg: '#f59e0b',  grade: 'cold', score: 31, status: 'New',       ch: 'facebook',  company: 'Startup XYZ' },
  ];
  const gradeStyle = { hot: 'rgba(239,68,68,.12)', warm: 'rgba(245,158,11,.12)', cold: 'rgba(59,130,246,.12)' };
  const gradeColor = { hot: '#ef4444', warm: '#d97706', cold: '#2563eb' };
  const statusStyle = { Qualified: 'rgba(16,185,129,.12)', Contacted: 'rgba(6,182,212,.12)', New: 'rgba(37,99,235,.12)' };
  const statusColor = { Qualified: '#10b981', Contacted: '#0891b2', New: '#2563eb' };
  return (
    <div style={{ background: 'var(--surface)', borderRadius: 14, border: '1px solid var(--border)', overflow: 'hidden', minHeight: 320 }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--fg)' }}>Leads Pipeline</span>
        <div style={{ display: 'flex', gap: 6 }}>
          <span style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 100, background: 'rgba(239,68,68,.12)', color: '#ef4444', border: '1px solid rgba(239,68,68,.25)', fontWeight: 600 }}>🔥 Hot: 1</span>
          <span style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 100, background: 'rgba(245,158,11,.12)', color: '#d97706', border: '1px solid rgba(245,158,11,.25)', fontWeight: 600 }}>☀️ Warm: 2</span>
        </div>
      </div>
      <div style={{ padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {leads.map((l, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', borderRadius: 10, background: 'var(--surface2)', border: '1px solid var(--border)', cursor: 'pointer' }}>
            <div style={{ width: 32, height: 32, borderRadius: '50%', background: l.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, color: '#fff', flexShrink: 0 }}>{l.initials}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--fg)', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{l.name}</span>
                <span style={{ fontSize: 9.5, padding: '1px 7px', borderRadius: 100, background: gradeStyle[l.grade], color: gradeColor[l.grade], border: `1px solid ${gradeColor[l.grade]}44`, fontWeight: 600 }}>{l.grade}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ fontSize: 10.5, color: 'var(--fg3)', flex: 1 }}>{l.company}</span>
                <FeaturesChIcon ch={l.ch} size={13} />
              </div>
            </div>
            <div style={{ textAlign: 'right', flexShrink: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--accent2)', fontFamily: 'var(--font-d)' }}>{l.score}</div>
              <span style={{ fontSize: 9.5, padding: '1px 7px', borderRadius: 4, background: statusStyle[l.status] || 'var(--surface)', color: statusColor[l.status] || 'var(--fg3)', fontWeight: 600 }}>{l.status}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AnalyticsPreviewUI() {
  const bars = [38,52,44,71,63,88,74];
  const days = ['M','T','W','T','F','S','S'];
  return (
    <div style={{ background: 'var(--surface)', borderRadius: 14, border: '1px solid var(--border)', padding: 14, minHeight: 320 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
        {[['47,832','Total Messages','↑ 32%','var(--accent2)'],['72%','AI Resolution','auto-resolved','#22c55e'],['1m 24s','Avg Response','↓ 45s faster','#0ea5e9'],['4.9/5','CSAT Score','↑ from 4.2','#f59e0b']].map(([v,l,s,c]) => (
          <div key={l} style={{ padding: '10px 11px', background: 'var(--surface2)', borderRadius: 10, border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 17, fontWeight: 800, color: c, fontFamily: 'var(--font-d)', lineHeight: 1 }}>{v}</div>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--fg)', marginTop: 3 }}>{l}</div>
            <div style={{ fontSize: 10, color: '#22c55e', marginTop: 2 }}>{s}</div>
          </div>
        ))}
      </div>
      <div style={{ background: 'var(--surface2)', borderRadius: 10, border: '1px solid var(--border)', padding: '10px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg)' }}>Message Volume · Last 7 Days</span>
          <span style={{ fontSize: 10, color: 'var(--fg3)' }}>↑ 18% vs last week</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 5, height: 70 }}>
          {bars.map((v, i) => (
            <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
              <div style={{ width: '100%', borderRadius: '3px 3px 0 0', background: i === 5 ? 'var(--accent)' : 'var(--border2)', height: `${v}%`, minHeight: 4, transition: 'height .3s ease' }} />
              <span style={{ fontSize: 9, color: 'var(--fg3)' }}>{days[i]}</span>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4, marginTop: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: 'var(--fg3)' }}>Sentiment:</span>
          {[['Positive','#22c55e',65],['Neutral','#94a3b8',22],['Negative','#ef4444',13]].map(([l,c,p]) => (
            <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: c }} />
              <span style={{ fontSize: 10, color: 'var(--fg2)' }}>{l} {p}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const TABS = [
  { id: 'inbox',     label: 'AI Inbox',          icon: '💬', accent: '#6366f1',
    headline: 'Every Channel. One Inbox. Zero Chaos.',
    body: 'WhatsApp, Instagram, Facebook, Email, and Web Chat — all in one blazing-fast inbox. AI routes conversations, detects collisions, and unifies customer identities across channels.',
    bullets: ['Real-time collision detection prevents duplicate replies','Identity unification across all channels & sessions','Multi-agent AI orchestration with instant human handoff','Smart routing by skill, team, or AI confidence score'],
    Preview: InboxPreviewUI },
  { id: 'auto',      label: 'Automation',         icon: '⚡', accent: '#f59e0b',
    headline: 'Build Powerful Workflows Without Writing Code.',
    body: 'Visual drag-and-drop workflow builder with AI agents, smart triggers, and multi-step CRM journeys. From lead capture to closed deal — fully automated.',
    bullets: ['50+ trigger types: message, form, webhook, schedule, CRM event','AI agents for qualification, support, sales & escalation','BANT scoring, lead routing, and pipeline automation','7-step drip sequences, broadcast campaigns & A/B tests'],
    Preview: AutomationPreviewUI },
  { id: 'leads',     label: 'Lead Intelligence',  icon: '🎯', accent: '#22c55e',
    headline: 'Turn Every Message Into a Qualified Lead.',
    body: 'Pulse Engine captures contact data automatically, scores leads using BANT criteria, and feeds your CRM pipeline — so nothing falls through the cracks.',
    bullets: ['Auto-capture leads from all incoming conversations','AI-powered BANT scoring updates in real time','Hot/warm/cold grading with smart deduplication','CRM sync with Salesforce, HubSpot & more'],
    Preview: LeadsPreviewUI },
  { id: 'analytics', label: 'Analytics',           icon: '📊', accent: '#0ea5e9',
    headline: "Know Exactly What's Working — and What Isn't.",
    body: 'Real-time dashboards surface conversation volume, response times, CSAT, and conversion funnels. Share reports with stakeholders in one click.',
    bullets: ['Live dashboards updating every 30 seconds','Channel-by-channel breakdown & comparison','AI performance vs. human response quality','Exportable reports for any time range'],
    Preview: AnalyticsPreviewUI },
];

function FeaturesSection() {
  const [activeTab, setActiveTab] = useState('inbox');
  const [visible, setVisible] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) setVisible(true); }, { threshold: .1 });
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  const tab = TABS.find(t => t.id === activeTab) || TABS[0];

  return (
    <section id="features" ref={ref} className="section" style={{ background: 'var(--bg)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: 52, opacity: visible ? 1 : 0, transition: 'opacity .7s cubic-bezier(.4,0,.2,1)' }}>
          <span className="label" style={{ display: 'block', marginBottom: 10 }}>Platform Features</span>
          <h2 className="h2" style={{ color: 'var(--fg)', marginBottom: 14 }}>Everything You Need to<br />Scale Customer Conversations</h2>
          <p className="body-lg" style={{ maxWidth: 480, margin: '0 auto' }}>Built for speed, designed for scale — four pillars that turn every message into an opportunity.</p>
        </div>

        <div role="tablist" aria-label="Feature tabs" style={{ display: 'flex', overflowX: 'auto', gap: 4, marginBottom: 40, padding: '4px 4px 0', borderBottom: '1px solid var(--border)', scrollbarWidth: 'none' }}>
          {TABS.map(t => (
            <button key={t.id}
              role="tab"
              aria-selected={activeTab === t.id}
              aria-controls={`panel-${t.id}`}
              id={`tab-${t.id}`}
              onClick={() => setActiveTab(t.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '10px 18px', borderRadius: '10px 10px 0 0', fontSize: 13.5, fontWeight: 600, fontFamily: 'var(--font-b)', border: 'none', cursor: 'pointer', whiteSpace: 'nowrap', transition: 'all .2s', borderBottom: activeTab === t.id ? `2px solid ${t.accent}` : '2px solid transparent', boxShadow: activeTab === t.id ? `0 2px 8px ${t.accent}55` : 'none', background: activeTab === t.id ? 'var(--surface2)' : 'transparent', color: activeTab === t.id ? 'var(--fg)' : 'var(--fg2)', marginBottom: -1 }}>
              <span aria-hidden="true">{t.icon}</span>{t.label}
            </button>
          ))}
        </div>

        <div id={`panel-${tab.id}`} role="tabpanel" aria-labelledby={`tab-${tab.id}`} className="features-panel-grid" style={{ opacity: visible ? 1 : 0, transition: 'opacity .5s ease' }}>
          <div key={activeTab} style={{ animation: 'slideUp .35s ease' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 12px', borderRadius: 100, background: `${tab.accent}18`, border: `1px solid ${tab.accent}44`, fontSize: 12, fontWeight: 600, color: tab.accent, marginBottom: 18 }}>
              {tab.icon} {tab.label}
            </div>
            <h3 className="h3" style={{ color: 'var(--fg)', marginBottom: 16, lineHeight: 1.2 }}>{tab.headline}</h3>
            <p className="body" style={{ marginBottom: 24, lineHeight: 1.7 }}>{tab.body}</p>
            <ul style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {tab.bullets.map(b => (
                <li key={b} style={{ display: 'flex', gap: 10, fontSize: 14, color: 'var(--fg2)', alignItems: 'flex-start' }}>
                  <span style={{ width: 18, height: 18, borderRadius: '50%', background: `${tab.accent}1a`, border: `1px solid ${tab.accent}44`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 2 }}>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke={tab.accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
                  </span>
                  {b}
                </li>
              ))}
            </ul>
          </div>
          <div key={activeTab + 'p'} style={{ animation: 'slideUp .35s ease .1s backwards' }}>
            <tab.Preview />
          </div>
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   7. GLOBE SECTION
   ══════════════════════════════════════════════════════════════ */
const TAGS = [
  { id: 'wa',   label: 'WhatsApp',         color: '#25D366', orbit: 1.22, inc:  0.30, phase: 0,                desc: 'WhatsApp Business API — 2-way messaging & automation' },
  { id: 'ig',   label: 'Instagram',         color: '#E1306C', orbit: 1.22, inc: -0.28, phase: Math.PI * .70,  desc: 'Instagram DM & story reply automation' },
  { id: 'fb',   label: 'Facebook',          color: '#1877F2', orbit: 1.22, inc:  0.48, phase: Math.PI * 1.40, desc: 'Facebook Messenger & Page inbox' },
  { id: 'wc',   label: 'Web Chat',          color: '#8b5cf6', orbit: 1.22, inc: -0.12, phase: Math.PI * 1.05, desc: 'Embeddable live chat widget for your website' },
  { id: 'ai1',  label: 'AI Auto-Reply',     color: '#818cf8', orbit: 1.48, inc:  0.55, phase: Math.PI * .15,  desc: 'AI generates & sends contextual replies automatically' },
  { id: 'ai2',  label: 'Lead Scoring',      color: '#f59e0b', orbit: 1.48, inc: -0.42, phase: Math.PI * .85,  desc: 'BANT-powered lead qualification in real time' },
  { id: 'ai3',  label: 'Smart Routing',     color: '#22c55e', orbit: 1.48, inc:  0.18, phase: Math.PI * 1.55, desc: 'Routes conversations by skill, team & workload' },
  { id: 'ai4',  label: 'Sentiment AI',      color: '#ec4899', orbit: 1.48, inc: -0.60, phase: Math.PI * .40,  desc: 'Real-time sentiment scoring on every message' },
  { id: 'ai5',  label: 'Workflow Builder',  color: '#6366f1', orbit: 1.48, inc:  0.65, phase: Math.PI * 1.20, desc: 'Visual no-code automation editor — trigger, condition, action' },
  { id: 'ai6',  label: 'Follow-up Drip',   color: '#0ea5e9', orbit: 1.72, inc:  0.22, phase: Math.PI * .30,  desc: 'Multi-step AI follow-up sequences on autopilot' },
  { id: 'ai7',  label: 'Broadcasting',      color: '#f97316', orbit: 1.72, inc: -0.48, phase: Math.PI * 1.00, desc: 'Bulk broadcast campaigns with AI personalization' },
  { id: 'ai8',  label: 'Knowledge Base',    color: '#14b8a6', orbit: 1.72, inc:  0.68, phase: Math.PI * 1.70, desc: 'AI answers customer questions from your knowledge base' },
  { id: 'ai9',  label: 'Identity Unify',    color: '#a78bfa', orbit: 1.72, inc: -0.32, phase: Math.PI * .60,  desc: 'Merges customer profiles across all channels' },
  { id: 'ai10', label: 'Analytics AI',      color: '#fb923c', orbit: 1.72, inc:  0.38, phase: Math.PI * 1.35, desc: 'Automated reporting & AI-driven conversation insights' },
  { id: 'ai11', label: 'Escalation AI',     color: '#38bdf8', orbit: 1.72, inc: -0.58, phase: Math.PI * .08,  desc: 'Smart escalation to human agents at the right moment' },
  { id: 'ai12', label: 'Multi-Agent AI',    color: '#c084fc', orbit: 1.96, inc:  0.15, phase: Math.PI * .50,  desc: 'Multiple specialized AI agents collaborate on each conversation' },
  { id: 'ai13', label: 'Live Handoff',      color: '#34d399', orbit: 1.96, inc: -0.45, phase: Math.PI * 1.25, desc: 'Seamless AI-to-human handoff with full context preserved' },
  { id: 'ai14', label: 'Auto Lead Capture', color: '#fbbf24', orbit: 1.96, inc:  0.60, phase: Math.PI * 1.85, desc: 'Captures and qualifies leads automatically from every message' },
  { id: 'ai15', label: 'CRM Sync',          color: '#60a5fa', orbit: 1.96, inc: -0.25, phase: Math.PI * .72,  desc: 'Bi-directional sync keeps contacts & pipeline always up to date' },
  { id: 'ai16', label: 'Template AI',       color: '#f472b6', orbit: 1.96, inc:  0.42, phase: Math.PI * 1.12, desc: 'AI-powered reply templates that match your brand voice' },
  { id: 'ai17', label: 'Convo Memory',      color: '#a3e635', orbit: 1.96, inc: -0.65, phase: Math.PI * .35,  desc: 'Short & long-term memory across every conversation and channel' },
];

const CITIES = [
  [40.7,-74],[51.5,0],[35.7,139.7],[25.2,55.3],[-33.9,151.2],
  [-23.5,-46.6],[1.4,103.8],[43.7,-79.4],[48.9,2.3],[19.1,72.9],
  [55.8,37.6],[31.2,121.5],[4.0,-74.1],[-26.2,28.0],
];
const TILT = 0.22;

function projectPoint(x3, y3, z3, cx, cy, R) {
  const y2 = y3 * Math.cos(TILT) - z3 * Math.sin(TILT);
  const z2 = y3 * Math.sin(TILT) + z3 * Math.cos(TILT);
  return { x: cx + x3 * R, y: cy + y2 * R, z: z2 };
}

function drawGlobe(ctx, cx, cy, R, rotY) {
  /* FIX: removed the partial clearRect — frame() handles full clear */

  /* outer glow */
  const glow = ctx.createRadialGradient(cx, cy, R * .6, cx, cy, R * 1.6);
  glow.addColorStop(0, 'rgba(99,102,241,0.0)');
  glow.addColorStop(.5, 'rgba(99,102,241,0.08)');
  glow.addColorStop(1, 'rgba(99,102,241,0.0)');
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.6, 0, Math.PI * 2);
  ctx.fillStyle = glow; ctx.fill();

  /* globe body — rich dark gradient */
  const grad = ctx.createRadialGradient(cx - R * .25, cy - R * .3, R * .05, cx, cy, R);
  grad.addColorStop(0,   'rgba(99,102,241,0.18)');
  grad.addColorStop(.35, 'rgba(20,25,45,0.85)');
  grad.addColorStop(1,   'rgba(2,8,23,0.96)');
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.fillStyle = grad; ctx.fill();

  /* border ring with gradient stroke */
  const ringGrad = ctx.createLinearGradient(cx-R, cy-R, cx+R, cy+R);
  ringGrad.addColorStop(0,   'rgba(99,102,241,0.55)');
  ringGrad.addColorStop(0.5, 'rgba(129,140,248,0.9)');
  ringGrad.addColorStop(1,   'rgba(59,130,246,0.55)');
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.strokeStyle = ringGrad; ctx.lineWidth = 1.5; ctx.stroke();

  /* grid lines — more visible neon-indigo */
  const lineColor = 'rgba(99,102,241,0.22)';
  ctx.lineWidth = .65;

  for (let lat = -75; lat <= 75; lat += 25) {
    const phi  = (90 - lat) * Math.PI / 180;
    const sinP = Math.sin(phi), cosP = Math.cos(phi);
    ctx.beginPath(); let first = true;
    for (let lon = 0; lon <= 360; lon += 3) {
      const theta = lon * Math.PI / 180 + rotY;
      const p = projectPoint(sinP * Math.cos(theta), cosP, sinP * Math.sin(theta), cx, cy, R);
      if (first) { ctx.moveTo(p.x, p.y); first = false; }
      else if (p.z > -.8) ctx.lineTo(p.x, p.y);
      else { ctx.strokeStyle = lineColor; ctx.stroke(); ctx.beginPath(); first = true; }
    }
    ctx.strokeStyle = lineColor; ctx.stroke();
  }
  for (let lon = 0; lon < 180; lon += 25) {
    const theta0 = lon * Math.PI / 180 + rotY;
    for (const theta of [theta0, theta0 + Math.PI]) {
      ctx.beginPath(); let first = true;
      for (let lat = -88; lat <= 88; lat += 3) {
        const phi  = (90 - lat) * Math.PI / 180;
        const sinP = Math.sin(phi), cosP = Math.cos(phi);
        const p = projectPoint(sinP * Math.cos(theta), cosP, sinP * Math.sin(theta), cx, cy, R);
        if (first) { ctx.moveTo(p.x, p.y); first = false; }
        else if (p.z > -.8) ctx.lineTo(p.x, p.y);
        else { ctx.strokeStyle = lineColor; ctx.stroke(); ctx.beginPath(); first = true; }
      }
      ctx.strokeStyle = lineColor; ctx.stroke();
    }
  }

  /* city dots — two-pass: ambient halo + bright core */
  CITIES.forEach(([lat, lon]) => {
    const phi   = (90 - lat) * Math.PI / 180;
    const theta = lon * Math.PI / 180 + rotY;
    const p = projectPoint(Math.sin(phi)*Math.cos(theta), Math.cos(phi), Math.sin(phi)*Math.sin(theta), cx, cy, R);
    if (p.z < -.1) return;
    const alpha = .25 + .55 * ((p.z + 1) / 2);
    const size  = 1.5 + p.z * 1.2;
    /* halo */
    ctx.beginPath(); ctx.arc(p.x, p.y, size * 2.8, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(99,102,241,${alpha * 0.25})`; ctx.fill();
    /* bright core */
    ctx.beginPath(); ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(200,210,255,${alpha})`; ctx.fill();
  });
}

function GlobeSection() {
  const canvasRef  = useRef(null);
  const wrapRef    = useRef(null);
  const rotY       = useRef(0);
  const drag       = useRef({ active: false, lastX: 0, vel: 0 });
  const raf        = useRef(null);
  const inView     = useRef(false);
  const [positions, setPositions] = useState([]);
  const [active,    setActive]    = useState(null);

  /* IntersectionObserver to PAUSE RAF when off-screen (perf fix) */
  useEffect(() => {
    const obs = new IntersectionObserver(([e]) => { inView.current = e.isIntersecting; }, { threshold: 0.05 });
    if (wrapRef.current) obs.observe(wrapRef.current);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap   = wrapRef.current;
    if (!canvas || !wrap) return;

    const dpr = window.devicePixelRatio || 1;

    function resize() {
      const w = wrap.clientWidth;
      const h = Math.min(w * .72, 540);
      canvas.width  = w  * dpr;
      canvas.height = h  * dpr;
      canvas.style.width  = w  + 'px';
      canvas.style.height = h  + 'px';
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    function calcTagPos() {
      const w  = canvas.width  / dpr;
      const h  = canvas.height / dpr;
      const cx = w / 2, cy = h / 2;
      const R  = Math.min(w, h) * .33;
      return TAGS.map(tag => {
        const angle = tag.phase + rotY.current;
        const cosA = Math.cos(angle), sinA = Math.sin(angle);
        const cosI = Math.cos(tag.inc), sinI = Math.sin(tag.inc);
        const x3 = tag.orbit * cosA;
        const y3 = tag.orbit * sinA * sinI;
        const z3 = tag.orbit * sinA * cosI;
        const p     = projectPoint(x3, y3, z3, cx, cy, R);
        const depth = (p.z + tag.orbit) / (2 * tag.orbit);
        return { ...tag, px: p.x, py: p.y, depth, opacity: .35 + .65 * depth, scale: .72 + .32 * depth };
      }).sort((a, b) => a.depth - b.depth);
    }

    function frame() {
      const canvas = canvasRef.current;
      if (!canvas) return;
      if (!inView.current) { raf.current = requestAnimationFrame(frame); return; }
      const ctx = canvas.getContext('2d');
      const w   = canvas.width  / dpr;
      const h   = canvas.height / dpr;
      const cx  = w / 2, cy = h / 2;
      const R   = Math.min(w, h) * .33;
      ctx.clearRect(0, 0, w, h);
      drawGlobe(ctx, cx, cy, R, rotY.current);

      if (!drag.current.active) {
        rotY.current += .003;
        drag.current.vel *= .92;
        rotY.current += drag.current.vel * .01;
      } else {
        rotY.current += drag.current.vel * .01;
        drag.current.vel *= .85;
      }
      setPositions(calcTagPos());
      raf.current = requestAnimationFrame(frame);
    }
    frame();

    const el = wrap;
    const onDown = e => { drag.current.active = true; drag.current.lastX = e.clientX || e.touches?.[0]?.clientX || 0; };
    const onMove = e => { if (!drag.current.active) return; const x = e.clientX || e.touches?.[0]?.clientX || 0; drag.current.vel = x - drag.current.lastX; drag.current.lastX = x; };
    const onUp   = () => { drag.current.active = false; };
    el.addEventListener('mousedown', onDown); el.addEventListener('mousemove', onMove); el.addEventListener('mouseup', onUp); el.addEventListener('mouseleave', onUp);
    el.addEventListener('touchstart', onDown, { passive: true }); el.addEventListener('touchmove', onMove, { passive: true }); el.addEventListener('touchend', onUp);

    /* close tooltip on outside click */
    const onDocClick = e => { if (!el.contains(e.target)) setActive(null); };
    document.addEventListener('click', onDocClick);

    return () => {
      ro.disconnect(); cancelAnimationFrame(raf.current);
      el.removeEventListener('mousedown', onDown); el.removeEventListener('mousemove', onMove); el.removeEventListener('mouseup', onUp); el.removeEventListener('mouseleave', onUp);
      el.removeEventListener('touchstart', onDown); el.removeEventListener('touchmove', onMove); el.removeEventListener('touchend', onUp);
      document.removeEventListener('click', onDocClick);
    };
  }, []);

  /* FIX: tooltip position uses tag coords, not dead-center */
  const getTooltipStyle = tag => {
    const wrap = wrapRef.current;
    if (!wrap) return { left: '50%', top: '50%' };
    const W = wrap.clientWidth, H = wrap.clientHeight;
    const padX = 130, padY = 90;
    const x = Math.max(padX, Math.min(W - padX, tag.px));
    const y = Math.max(padY, Math.min(H - padY, tag.py));
    return { left: x, top: y };
  };

  return (
    <section id="globe" className="section" style={{ position: 'relative', overflow: 'hidden', background: 'var(--bg2)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: 56 }}>
          <span className="label" style={{ marginBottom: 10, display: 'block' }}>AI Platform</span>
          <h2 className="h2" style={{ color: 'var(--fg)', marginBottom: 16 }}>Channels &amp; AI Automation<br />Working Together</h2>
          <p className="body-lg" style={{ maxWidth: 500, margin: '0 auto', color: 'var(--fg2)' }}>Connect your channels and let AI automation handle the rest — click any element to learn more.</p>
        </div>
        <div ref={wrapRef} style={{ position: 'relative', maxWidth: 700, margin: '0 auto', cursor: 'grab' }}>
          <canvas ref={canvasRef} style={{ width: '100%', display: 'block' }} aria-label="Interactive globe showing AI integrations" />
          {/* soft vignette overlay */}
          <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at center, transparent 50%, var(--bg2) 90%)', pointerEvents: 'none', zIndex: 1 }} aria-hidden="true" />
          {positions.map(tag => (
            <button key={tag.id}
              className="globe-tag"
              onClick={e => { e.stopPropagation(); setActive(prev => prev?.id === tag.id ? null : tag); }}
              aria-pressed={active?.id === tag.id}
              aria-label={`${tag.label}: ${tag.desc}`}
              style={{ position: 'absolute', left: tag.px, top: tag.py, transform: `translate(-50%,-50%) scale(${tag.scale})`, opacity: tag.opacity, background: active?.id === tag.id ? tag.color : 'rgba(12,19,36,0.75)', border: `1px solid ${tag.color}66`, borderRadius: 100, padding: '5px 13px', fontSize: 11.5, fontWeight: 600, color: active?.id === tag.id ? '#fff' : tag.color, cursor: 'pointer', fontFamily: 'var(--font-b)', whiteSpace: 'nowrap', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', zIndex: Math.round(tag.depth * 20) + 2, transition: 'background .2s, color .2s, box-shadow .2s', boxShadow: active?.id === tag.id ? `0 0 20px ${tag.color}66` : 'none' }}>
              {tag.label}
            </button>
          ))}
          {active && (
            <div style={{ position: 'absolute', ...getTooltipStyle(active), transform: 'translate(-50%,-50%)', background: 'rgba(12,19,36,0.95)', border: `1px solid ${active.color}55`, borderRadius: 16, padding: '16px 20px', textAlign: 'center', zIndex: 100, boxShadow: `0 20px 50px rgba(0,0,0,.5), 0 0 30px ${active.color}22`, minWidth: 190, maxWidth: 240, animation: 'slideUp .2s ease', backdropFilter: 'blur(16px)' }}>
              <button onClick={() => setActive(null)} aria-label="Close tooltip" style={{ position: 'absolute', top: 8, right: 10, background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: 'var(--fg3)', lineHeight: 1, padding: 2 }}>✕</button>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: active.color, margin: '0 auto 10px', boxShadow: `0 0 12px ${active.color}` }} aria-hidden="true" />
              <div style={{ fontSize: 15, fontWeight: 700, color: active.color, marginBottom: 8 }}>{active.label}</div>
              <div style={{ fontSize: 12.5, color: 'var(--fg2)', lineHeight: 1.55 }}>{active.desc}</div>
            </div>
          )}
        </div>
        <p style={{ textAlign: 'center', marginTop: 16, fontSize: 12.5, color: 'var(--fg3)' }}>Drag to rotate · Click any tag to learn more</p>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   8. INTEGRATIONS MARQUEE (fixed double-track)
   ══════════════════════════════════════════════════════════════ */
function IntegrationsMarquee() {
  const items = ['WhatsApp','Facebook','Instagram','Web Chat','Gmail','AI Auto-Reply','Lead Scoring','Smart Routing','BANT Analysis','Follow-up Drip','Workflow Builder','Sentiment AI','Broadcasting','Knowledge Base','Identity Unify','Analytics AI','Escalation AI'];
  const trackStyle = { display: 'flex', gap: 12, animation: 'marquee 28s linear infinite', whiteSpace: 'nowrap', flexShrink: 0 };
  const pill = (name, i) => (
    <div key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 18px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 100, fontSize: 13, fontWeight: 500, color: 'var(--fg2)', whiteSpace: 'nowrap', flexShrink: 0 }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--accent)', opacity: .7 }} aria-hidden="true" />
      {name}
    </div>
  );
  return (
    <section style={{ padding: '40px 0', background: 'var(--bg2)', borderTop: '1px solid var(--border)', overflow: 'hidden' }} aria-label="Integrations">
      <div style={{ textAlign: 'center', marginBottom: 20 }}>
        <span style={{ fontSize: 12, color: 'var(--fg3)', letterSpacing: '.1em', textTransform: 'uppercase', fontFamily: 'var(--font-m)' }}>Connects with 40+ tools your team already uses</span>
      </div>
      {/* two identical tracks → seamless loop */}
      <div style={{ display: 'flex', overflow: 'hidden' }}>
        <div style={trackStyle}>{items.map((n,i) => pill(n,i))}</div>
        <div style={trackStyle} aria-hidden="true">{items.map((n,i) => pill(n,i+'b'))}</div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   9. TESTIMONIALS
   ══════════════════════════════════════════════════════════════ */
function TestimonialsSection() {
  const quotes = [
    { text: 'Pulse Engine cut our response time from hours to under 2 minutes. The AI handles 70% of queries automatically — our team finally has time for actual sales work.', name: 'Sarah M.',  role: 'VP Sales',      company: 'TechCorp',  avatar: 'S' },
    { text: "The multi-channel inbox is game-changing. WhatsApp, Instagram, Email — all in one place with AI routing. We've doubled our lead conversion rate in 60 days.",      name: 'James K.',  role: 'Head of Growth', company: 'Finova',    avatar: 'J' },
    { text: "Best investment we've made this year. The AI agents are incredibly smart and the workflow automation saved us 40 hours a week of manual work.",                      name: 'Priya S.',  role: 'CEO',            company: 'LaunchPad', avatar: 'P' },
  ];
  return (
    <section className="section" style={{ background: 'var(--bg2)', borderTop: '1px solid var(--border)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: 48 }}>
          <span className="label" style={{ display: 'block', marginBottom: 10 }}>Customer Stories</span>
          <h2 className="h2" style={{ color: 'var(--fg)' }}>Loved by Teams Worldwide</h2>
        </div>
        <div className="testimonials-grid">
          {quotes.map((q, i) => (
            <div key={i} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', gap: 2, marginBottom: 14 }}>
                {[1,2,3,4,5].map(s => <svg key={s} width="14" height="14" viewBox="0 0 24 24" fill="#f59e0b" stroke="none" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>)}
              </div>
              <p style={{ fontSize: 14, color: 'var(--fg2)', lineHeight: 1.7, flex: 1, marginBottom: 20, fontStyle: 'italic' }}>"{q.text}"</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {/* improved avatar — pravatar for demo */}
                <img
                  src={`https://i.pravatar.cc/80?u=${q.name}`}
                  alt={q.name}
                  width={38} height={38}
                  style={{ borderRadius: '50%', border: '2px solid var(--border2)', flexShrink: 0 }}
                  onError={e => { e.currentTarget.style.display='none'; }}
                />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg)' }}>{q.name}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--fg3)' }}>{q.role}, {q.company}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   10. TEAM SECTION (RESTORED)
   ══════════════════════════════════════════════════════════════ */
function TeamSection() {
  const team = [
    { name: 'Amir Hassan',    role: 'CEO & Co-Founder',         bio: 'Ex-Salesforce. Building the future of AI-powered CRM.',     initials: 'AH', color: '#6366f1' },
    { name: 'Lena Müller',    role: 'CTO & Co-Founder',         bio: '10 years in conversational AI and distributed systems.',    initials: 'LM', color: '#3b82f6' },
    { name: 'Rohan Desai',    role: 'Head of AI',               bio: 'PhD in NLP. Trained models that power our smart routing.', initials: 'RD', color: '#22c55e' },
    { name: 'Sofia Reyes',    role: 'Head of Product',          bio: 'Former PM at Intercom. Obsessed with inbox zero.',          initials: 'SR', color: '#f59e0b' },
    { name: 'Marcus Webb',    role: 'Head of Sales',            bio: 'Closed $50M ARR in previous roles. Customer-first always.', initials: 'MW', color: '#ec4899' },
    { name: 'Priya Sharma',   role: 'Head of Customer Success', bio: 'Scaled CS at 3 startups from 0 to enterprise.',             initials: 'PS', color: '#14b8a6' },
  ];

  return (
    <section id="team" className="section" style={{ background: 'var(--bg)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: 52 }}>
          <span className="label" style={{ display: 'block', marginBottom: 10 }}>Our Team</span>
          <h2 className="h2" style={{ color: 'var(--fg)', marginBottom: 14 }}>The People Behind Pulse Engine</h2>
          <p className="body-lg" style={{ maxWidth: 480, margin: '0 auto' }}>A team obsessed with making customer conversations smarter.</p>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 20 }}>
          {team.map((m, i) => (
            <div key={i} className="card" style={{ textAlign: 'center', padding: 28 }}>
              <div style={{ width: 68, height: 68, borderRadius: '50%', background: `${m.color}22`, border: `2px solid ${m.color}44`, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px', fontSize: 22, fontWeight: 800, color: m.color, fontFamily: 'var(--font-d)' }}>
                {m.initials}
              </div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--fg)', marginBottom: 4 }}>{m.name}</div>
              <div style={{ fontSize: 12, fontWeight: 600, color: m.color, marginBottom: 10 }}>{m.role}</div>
              <p style={{ fontSize: 12.5, color: 'var(--fg3)', lineHeight: 1.55 }}>{m.bio}</p>
              <a href="#" aria-label={`${m.name} on LinkedIn`} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 14, fontSize: 11.5, fontWeight: 600, color: 'var(--fg3)', textDecoration: 'none', transition: 'color .2s' }}
                onMouseEnter={e => e.currentTarget.style.color = 'var(--accent2)'}
                onMouseLeave={e => e.currentTarget.style.color = 'var(--fg3)'}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M16 8a6 6 0 016 6v7h-4v-7a2 2 0 00-2-2 2 2 0 00-2 2v7h-4v-7a6 6 0 016-6z"/>
                  <rect x="2" y="9" width="4" height="12"/>
                  <circle cx="4" cy="4" r="2"/>
                </svg>
                LinkedIn
              </a>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   11. PRICING SECTION
   ══════════════════════════════════════════════════════════════ */
function PricingSection() {
  const [annual,  setAnnual]  = useState(true);
  const [visible, setVisible] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) setVisible(true); }, { threshold: .1 });
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  /* kept exactly as original — per Part 8 */
  const plans = [
    { name: 'Starter',      monthly: 0,   annual: 0,   color: 'var(--fg)',    cta: 'Get Started Free',  popular: false,
      features: ['Up to 3 team members','500 conversations/mo','2 channels (WhatsApp + 1)','Basic AI auto-replies','Email support'] },
    { name: 'Professional', monthly: 49,  annual: 39,  color: 'var(--accent2)', cta: 'Start Free Trial', popular: true,
      features: ['Up to 15 team members','5,000 conversations/mo','All 5 channels','Advanced AI agents','Workflow automation','Lead scoring + CRM sync','Priority support'] },
    { name: 'Enterprise',   monthly: 129, annual: 99,  color: '#22c55e',      cta: 'Contact Sales',     popular: false,
      features: ['Unlimited team members','Unlimited conversations','All channels + custom','Custom AI agents','White-label options','SSO + advanced security','Dedicated account manager'] },
  ];

  return (
    <section id="pricing" ref={ref} className="section" style={{ background: 'var(--bg)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: 52, opacity: visible ? 1 : 0, transform: visible ? 'translateY(0)' : 'translateY(24px)', transition: 'all .7s cubic-bezier(.4,0,.2,1)' }}>
          <span className="label" style={{ display: 'block', marginBottom: 10 }}>Pricing</span>
          <h2 className="h2" style={{ color: 'var(--fg)', marginBottom: 14 }}>Pricing That Scales With You</h2>
          <p className="body-lg" style={{ maxWidth: 440, margin: '0 auto 28px' }}>Start free, upgrade when you need it. All plans include a 14-day trial of Professional.</p>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 12, padding: '6px 8px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12 }}>
            <button onClick={() => setAnnual(false)} style={{ padding: '7px 16px', borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600, background: !annual ? 'var(--accent)' : 'transparent', color: !annual ? '#fff' : 'var(--fg2)', transition: 'all .2s', fontFamily: 'var(--font-b)' }} aria-pressed={!annual}>Monthly</button>
            <button onClick={() => setAnnual(true)}  style={{ padding: '7px 16px', borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600, background: annual ? 'var(--accent)' : 'transparent', color: annual ? '#fff' : 'var(--fg2)', transition: 'all .2s', fontFamily: 'var(--font-b)' }} aria-pressed={annual}>Annual <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 100, background: 'rgba(34,197,94,.2)', color: '#22c55e', marginLeft: 4 }}>-20%</span></button>
          </div>
        </div>

        <div className="pricing-grid" style={{ opacity: visible ? 1 : 0, transition: 'opacity .8s .2s' }}>
          {plans.map((p, i) => (
            <div key={p.name}
              className={p.popular ? 'pricing-popular-card' : ''}
              style={{ background: 'var(--surface)', border: `1px solid ${p.popular ? 'var(--accent)' : 'var(--border)'}`, borderRadius: 20, padding: 28, display: 'flex', flexDirection: 'column', position: 'relative', transform: p.popular ? 'scale(1.03)' : 'scale(1)', boxShadow: p.popular ? '0 0 40px rgba(99,102,241,.2), inset 0 1px 0 rgba(255,255,255,.08)' : 'inset 0 1px 0 rgba(255,255,255,.04)', transition: 'transform .2s, box-shadow .2s' }}
              onMouseEnter={e => { if (!p.popular) { e.currentTarget.style.borderColor = 'var(--border2)'; e.currentTarget.style.transform = 'translateY(-4px)'; } }}
              onMouseLeave={e => { if (!p.popular) { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = 'translateY(0)'; } }}>
              {p.popular && <div style={{ position: 'absolute', top: -13, left: '50%', transform: 'translateX(-50%)', background: 'linear-gradient(135deg,var(--accent),var(--blue))', color: '#fff', fontSize: 11, fontWeight: 700, padding: '4px 14px', borderRadius: 100, whiteSpace: 'nowrap', letterSpacing: '.04em' }}>MOST POPULAR</div>}
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--fg3)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '.08em' }}>{p.name}</div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, minHeight: 54 }}>
                  {/* FIX: show $0/mo for Starter to keep baseline aligned */}
                  <span style={{ fontFamily: 'var(--font-d)', fontSize: 42, fontWeight: 800, color: p.color, lineHeight: 1 }}>
                    {annual && p.annual > 0 ? `$${p.annual}` : p.monthly === 0 ? '$0' : `$${p.monthly}`}
                  </span>
                  <span style={{ fontSize: 14, color: 'var(--fg3)' }}>/mo</span>
                </div>
                {annual && p.annual > 0 && <div style={{ fontSize: 11.5, color: 'var(--fg3)', marginTop: 4 }}>billed annually</div>}
              </div>
              <ul style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: 9, flex: 1, marginBottom: 24 }}>
                {p.features.map(f => (
                  <li key={f} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, fontSize: 13.5, color: 'var(--fg2)' }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={p.popular ? 'var(--accent2)' : '#22c55e'} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
                    {f}
                  </li>
                ))}
              </ul>
              <button style={{ width: '100%', padding: '12px', borderRadius: 10, border: `1px solid ${p.popular ? 'var(--accent)' : 'var(--border2)'}`, background: p.popular ? 'var(--accent)' : 'transparent', color: p.popular ? '#fff' : 'var(--fg2)', fontSize: 13.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'var(--font-b)', transition: 'all .2s' }}
                onMouseEnter={e => { if (!p.popular) { e.currentTarget.style.background = 'var(--surface2)'; e.currentTarget.style.color = 'var(--fg)'; } else { e.currentTarget.style.filter = 'brightness(1.1)'; } }}
                onMouseLeave={e => { if (!p.popular) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg2)'; } else { e.currentTarget.style.filter = 'none'; } }}>
                {p.cta}
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   12. FAQ SECTION
   ══════════════════════════════════════════════════════════════ */
function FAQSection() {
  const [open, setOpen] = useState(null);
  const answerRefs = useRef({});

  const faqs = [
    { q: 'Which channels does Pulse Engine support?',        a: 'WhatsApp Business API, Facebook Messenger, Instagram DM, Email (IMAP/SMTP), and an embeddable Web Chat widget. Additional channels are available via webhooks.' },
    { q: 'How does the AI know when to escalate to a human?',a: 'Our AI monitors sentiment, detects frustration signals, and recognises intent patterns beyond its confidence threshold. It hands off seamlessly with full conversation context attached.' },
    { q: 'Can multiple agents work on the same inbox?',      a: 'Yes. The shared inbox has real-time collision detection so two agents never respond to the same conversation simultaneously. You can set routing rules by team, skill, or workload.' },
    { q: 'Is there a free trial?',                           a: 'Every new account gets a 14-day Professional trial, no credit card required. After the trial you can continue on the free Starter plan or upgrade anytime.' },
    { q: 'How is my data secured?',                          a: 'All data is encrypted at rest (AES-256) and in transit (TLS 1.3). We support JWT authentication, role-based access control, and are fully GDPR compliant. Enterprise plans support on-premise deployment.' },
    { q: 'Can I export my data?',                            a: 'Absolutely. You can export conversations, contacts, and analytics in CSV or JSON at any time. Your data is yours — always.' },
  ];

  return (
    <section id="faq" className="section" style={{ background: 'var(--bg)' }}>
      <div className="container" style={{ maxWidth: 720 }}>
        <div style={{ textAlign: 'center', marginBottom: 48 }}>
          <span className="label" style={{ display: 'block', marginBottom: 10 }}>FAQ</span>
          <h2 className="h2" style={{ color: 'var(--fg)' }}>Frequently Asked Questions</h2>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {faqs.map((f, i) => (
            <div key={i} style={{ background: 'var(--surface)', border: `1px solid ${open === i ? 'var(--accent)' : 'var(--border)'}`, borderRadius: 14, overflow: 'hidden', transition: 'border-color .2s' }}>
              <button
                onClick={() => setOpen(open === i ? null : i)}
                aria-expanded={open === i}
                aria-controls={`faq-answer-${i}`}
                id={`faq-btn-${i}`}
                style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 22px', background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', gap: 16 }}>
                {/* FIX: removed invalid fontSpread property */}
                <span style={{ fontSize: 14.5, fontWeight: 600, color: 'var(--fg)', fontFamily: 'var(--font-b)' }}>{f.q}</span>
                <span style={{ flexShrink: 0, width: 22, height: 22, borderRadius: '50%', background: open === i ? 'var(--accent-dim)' : 'var(--surface2)', border: `1px solid ${open === i ? 'var(--accent)' : 'var(--border)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all .2s' }} aria-hidden="true">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={open === i ? 'var(--accent2)' : 'var(--fg3)'} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform .3s', transform: open === i ? 'rotate(45deg)' : 'rotate(0deg)' }}>
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                </span>
              </button>
              {/* FIX: use 999px instead of 300px to avoid clipping long answers */}
              <div id={`faq-answer-${i}`} role="region" aria-labelledby={`faq-btn-${i}`} style={{ maxHeight: open === i ? 999 : 0, overflow: 'hidden', transition: 'max-height .45s cubic-bezier(.4,0,.2,1)' }}>
                <p style={{ padding: '0 22px 20px', fontSize: 14, color: 'var(--fg2)', lineHeight: 1.7, margin: 0 }}>{f.a}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   13. CTA BANNER
   ══════════════════════════════════════════════════════════════ */
function CTABanner() {
  return (
    <section style={{ padding: '80px 24px', background: 'linear-gradient(135deg,#020817 0%,#0a0f2e 40%,#0d1635 100%)', position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', top: -80, right: -80, width: 500, height: 500, borderRadius: '50%', background: 'radial-gradient(circle,rgba(99,102,241,.18) 0%,transparent 70%)', pointerEvents: 'none' }} aria-hidden="true" />
      <div style={{ position: 'absolute', bottom: -60, left: -60, width: 380, height: 380, borderRadius: '50%', background: 'radial-gradient(circle,rgba(37,99,235,.14) 0%,transparent 70%)', pointerEvents: 'none' }} aria-hidden="true" />
      {/* subtle animated grid */}
      <div style={{ position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(99,102,241,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(99,102,241,.05) 1px,transparent 1px)', backgroundSize: '50px 50px', opacity: .4 }} aria-hidden="true" />
      <div className="noise-overlay" aria-hidden="true" />
      <div className="container" style={{ maxWidth: 680, textAlign: 'center', position: 'relative', zIndex: 1 }}>
        <h2 style={{ fontFamily: 'var(--font-d)', fontSize: 'clamp(28px,4vw,48px)', fontWeight: 800, color: '#fff', marginBottom: 16, lineHeight: 1.1, letterSpacing: '-.03em' }}>
          Ready to Transform Your<br />Customer Conversations?
        </h2>
        <p style={{ fontSize: 17, color: 'rgba(255,255,255,.65)', marginBottom: 36, lineHeight: 1.6 }}>
          Start for free — no credit card, no setup fees. Your AI workforce is ready in minutes.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'center' }}>
          <a href="#" className="btn btn-primary btn-lg"
            onMouseEnter={e => { e.currentTarget.style.transform='translateY(-2px)'; }}
            onMouseLeave={e => { e.currentTarget.style.transform='translateY(0)'; }}>
            Start Free Trial
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
          </a>
          <a href="#" style={{ display:'inline-flex', alignItems:'center', gap:8, padding:'14px 24px', background:'rgba(255,255,255,.08)', border:'1px solid rgba(255,255,255,.2)', color:'rgba(255,255,255,.9)', borderRadius:12, fontWeight:600, fontSize:15, textDecoration:'none', transition:'all .2s' }}
            onMouseEnter={e=>e.currentTarget.style.background='rgba(255,255,255,.14)'}
            onMouseLeave={e=>e.currentTarget.style.background='rgba(255,255,255,.08)'}>
            Book a Demo
          </a>
        </div>
        <div style={{ display:'flex', flexWrap:'wrap', justifyContent:'center', gap:24, marginTop:28, fontSize:12.5, color:'rgba(255,255,255,.4)' }}>
          {['Free 14-day trial','No credit card','Cancel anytime','Setup in 5 minutes'].map(t => (
            <span key={t} style={{ display:'flex', alignItems:'center', gap:5 }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
              {t}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════
   14. FOOTER
   ══════════════════════════════════════════════════════════════ */
function PulseFooter() {
  const cols = [
    { heading: 'Product',   links: ['AI Inbox','Automation','Lead Intelligence','Analytics','API & Webhooks','Changelog'] },
    { heading: 'Solutions', links: ['Sales Teams','Support Teams','Marketing','E-commerce','Agencies','Enterprise'] },
    { heading: 'Resources', links: ['Documentation','Help Center','Blog','Webinars','Case Studies','Status'] },
    { heading: 'Company',   links: ['About Us','Careers','Press','Partners','Contact','Security'] },
  ];

  /* FIX: dynamic copyright year */
  const year = new Date().getFullYear();

  return (
    <footer style={{ background: 'var(--bg2)', borderTop: '1px solid var(--border)', padding: '60px 24px 32px' }}>
      <div className="container">
        <div className="footer-grid">
          <div>
            <div style={{ marginBottom: 16 }}>
              <PlatformLogo textColor="var(--fg)" imageWidth={30} fontSize={16} fontWeight={800} />
            </div>
            <p style={{ fontSize: 13.5, color: 'var(--fg3)', lineHeight: 1.65, marginBottom: 20, maxWidth: 220 }}>AI-powered CRM for modern teams that want to grow faster and work smarter.</p>
            <div style={{ display: 'flex', gap: 8 }}>
              {/* X/Twitter */}
              <a href="#" aria-label="Pulse Engine on X / Twitter" style={{ width:32, height:32, borderRadius:8, border:'1px solid var(--border)', background:'var(--surface)', display:'flex', alignItems:'center', justifyContent:'center', color:'var(--fg3)', textDecoration:'none', transition:'all .2s' }}
                onMouseEnter={e=>{e.currentTarget.style.borderColor='var(--accent)';e.currentTarget.style.color='var(--accent2)';}}
                onMouseLeave={e=>{e.currentTarget.style.borderColor='var(--border)';e.currentTarget.style.color='var(--fg3)';}}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M23 3a10.9 10.9 0 01-3.14 1.53 4.48 4.48 0 00-7.86 3v1A10.66 10.66 0 013 4s-4 9 5 13a11.64 11.64 0 01-7 2c9 5 20 0 20-11.5a4.5 4.5 0 00-.08-.83A7.72 7.72 0 0023 3z"/></svg>
              </a>
              {/* LinkedIn — FIX: split into two path elements for correct rendering */}
              <a href="#" aria-label="Pulse Engine on LinkedIn" style={{ width:32, height:32, borderRadius:8, border:'1px solid var(--border)', background:'var(--surface)', display:'flex', alignItems:'center', justifyContent:'center', color:'var(--fg3)', textDecoration:'none', transition:'all .2s' }}
                onMouseEnter={e=>{e.currentTarget.style.borderColor='var(--accent)';e.currentTarget.style.color='var(--accent2)';}}
                onMouseLeave={e=>{e.currentTarget.style.borderColor='var(--border)';e.currentTarget.style.color='var(--fg3)';}}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M16 8a6 6 0 016 6v7h-4v-7a2 2 0 00-2-2 2 2 0 00-2 2v7h-4v-7a6 6 0 016-6z"/>
                  <rect x="2" y="9" width="4" height="12"/>
                  <circle cx="4" cy="4" r="2"/>
                </svg>
              </a>
            </div>
          </div>
          {cols.map(col => (
            <div key={col.heading}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg3)', textTransform: 'uppercase', letterSpacing: '.12em', marginBottom: 14 }}>{col.heading}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                {col.links.map(l => (
                  <a key={l} href="#" style={{ fontSize: 13.5, color: 'var(--fg3)', textDecoration: 'none', transition: 'color .15s' }}
                    onMouseEnter={e=>e.currentTarget.style.color='var(--fg)'}
                    onMouseLeave={e=>e.currentTarget.style.color='var(--fg3)'}>{l}</a>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div style={{ paddingTop: 24, borderTop: '1px solid var(--border)', display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
          {/* FIX: dynamic year */}
          <span style={{ fontSize: 12.5, color: 'var(--fg3)' }}>© {year} Pulse Engine. All rights reserved.</span>
          <div style={{ display: 'flex', gap: 20 }}>
            {['Privacy Policy','Terms of Service','Cookie Settings'].map(t => (
              <a key={t} href="#" style={{ fontSize: 12.5, color: 'var(--fg3)', textDecoration: 'none', transition: 'color .15s' }}
                onMouseEnter={e=>e.currentTarget.style.color='var(--fg)'}
                onMouseLeave={e=>e.currentTarget.style.color='var(--fg3)'}>{t}</a>
            ))}
          </div>
        </div>
      </div>
    </footer>
  );
}

/* ══════════════════════════════════════════════════════════════
   15. DEMO MODAL (bugs fixed)
   ══════════════════════════════════════════════════════════════ */
function DemoModal({ open, onClose }) {
  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  if (!open) return null;
  return (
    <div onClick={onClose} role="dialog" aria-modal="true" aria-label="Platform demo" style={{ position: 'fixed', inset: 0, zIndex: 99999, background: 'rgba(0,0,0,.88)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, backdropFilter: 'blur(8px)', animation: 'fadeIn .25s ease' }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 900, background: 'var(--surface)', borderRadius: 20, border: '1px solid var(--border)', overflow: 'hidden', boxShadow: '0 40px 120px rgba(0,0,0,.7)', position: 'relative' }}>
        {/* FIX: removed invalid justifySpread property */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid var(--border)', background: 'var(--bg2)' }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--fg)', fontFamily: 'var(--font-d)' }}>Pulse Engine — Platform Demo</span>
          <button onClick={onClose} aria-label="Close demo" style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--fg2)', borderRadius: 8, width: 30, height: 30, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>×</button>
        </div>
        {/* FIX: iframe replaced with branded placeholder — no 404 local file */}
        <div style={{ padding: 0, background: '#020817', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: 16 }}>
          <div style={{ width: 72, height: 72, borderRadius: 20, background: 'linear-gradient(135deg,#6366f1,#3b82f6)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 40px rgba(99,102,241,.5)' }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
          <p style={{ color: 'var(--fg2)', fontSize: 16, fontWeight: 600 }}>Demo video coming soon</p>
          <p style={{ color: 'var(--fg3)', fontSize: 13 }}>Book a live walkthrough with our team instead →</p>
          <a href="mailto:demo@pulseengine.io" className="btn btn-primary" style={{ marginTop: 8 }}>Book Live Demo</a>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════
   16. TWEAKS PANEL (accent cascade + no theme toggle)
   ══════════════════════════════════════════════════════════════ */
const TweaksPanel = memo(function TweaksPanel({ visible, onClose }) {
  if (!visible) return null;

  /* FIX: update all three accent tokens together */
  const setAccent = (base, light, dim) => {
    const root = document.documentElement;
    root.style.setProperty('--accent',     base);
    root.style.setProperty('--accent2',    light);
    root.style.setProperty('--accent-dim', dim);
  };

  const accents = [
    { color: '#6366f1', light: '#818cf8', dim: 'rgba(99,102,241,0.12)',  name: 'Indigo'  },
    { color: '#0ea5e9', light: '#38bdf8', dim: 'rgba(14,165,233,0.12)',  name: 'Cyan'    },
    { color: '#22c55e', light: '#4ade80', dim: 'rgba(34,197,94,0.12)',   name: 'Emerald' },
  ];

  return (
    <div style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 9998, background: 'var(--surface)', border: '1px solid var(--border2)', borderRadius: 16, padding: 20, minWidth: 240, boxShadow: '0 20px 60px rgba(0,0,0,.5)', animation: 'slideUp .25s ease' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
        <span style={{ fontFamily: 'var(--font-d)', fontWeight: 700, fontSize: 13, color: 'var(--fg)' }}>Tweaks</span>
        <button onClick={onClose} aria-label="Close tweaks panel" style={{ background: 'none', border: 'none', color: 'var(--fg3)', cursor: 'pointer', fontSize: 18, padding: 0, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg3)', letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 8 }}>Accent Color</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {accents.map(a => (
              <button key={a.color} onClick={() => setAccent(a.color, a.light, a.dim)} title={a.name} aria-label={`Set ${a.name} accent`}
                style={{ flex: 1, height: 28, borderRadius: 6, background: a.color, border: '2px solid rgba(255,255,255,.15)', cursor: 'pointer', transition: 'transform .15s' }}
                onMouseEnter={e=>e.currentTarget.style.transform='scale(1.08)'}
                onMouseLeave={e=>e.currentTarget.style.transform='scale(1)'} />
            ))}
          </div>
        </div>
        {/* FIX: local Inbox.html replaced with real route */}
        <a href="/inbox" style={{ display:'flex', alignItems:'center', justifyContent:'center', gap:6, padding:'9px', borderRadius:9, border:'1px solid var(--border)', background:'var(--surface2)', color:'var(--fg2)', textDecoration:'none', fontSize:12.5, fontWeight:600, transition:'all .2s' }}
          onMouseEnter={e=>{e.currentTarget.style.borderColor='var(--accent)';e.currentTarget.style.color='var(--fg)';}}
          onMouseLeave={e=>{e.currentTarget.style.borderColor='var(--border)';e.currentTarget.style.color='var(--fg2)';}}>
          💬 Open Inbox →
        </a>
      </div>
    </div>
  );
});

/* ══════════════════════════════════════════════════════════════
   17. APP — main composition
   ══════════════════════════════════════════════════════════════ */
function App() {
  /* FIX: localStorage SSR guard */
  const [demoOpen,   setDemoOpen]   = useState(false);
  const [tweaksOpen, setTweaksOpen] = useState(false);

  /* Always enforce dark theme on mount */
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', 'dark');
  }, []);

  useEffect(() => {
    const els = () => document.querySelectorAll('.reveal');
    const obs = new IntersectionObserver(entries => {
      entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('in'); });
    }, { threshold: .1 });
    const timer = setTimeout(() => { els().forEach(el => obs.observe(el)); }, 300);
    return () => { clearTimeout(timer); obs.disconnect(); };
  }, []);

  useEffect(() => {
    const handler = e => {
      if (e.data?.type === '__activate_edit_mode')   setTweaksOpen(true);
      if (e.data?.type === '__deactivate_edit_mode') setTweaksOpen(false);
    };
    window.addEventListener('message', handler);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', handler);
  }, []);

  return (
    <>
      <GlobalStyles />
      <ScrollProgress />
      {/* Dark theme is always active — no theme toggle per user request */}
      <PulseNav />
      <main>
        <HeroSection         onWatchDemo={() => setDemoOpen(true)} />
        <TrustBar />
        <StatsSection />
        <FeaturesSection />
        <GlobeSection />
        <IntegrationsMarquee />
        <TestimonialsSection />
        <TeamSection />
        <PricingSection />
        <FAQSection />
        <CTABanner />
      </main>
      <PulseFooter />
      <DemoModal  open={demoOpen}   onClose={() => setDemoOpen(false)} />
      <TweaksPanel visible={tweaksOpen} onClose={() => { setTweaksOpen(false); window.parent.postMessage({ type: '__edit_mode_dismissed' }, '*'); }} />
    </>
  );
}

/* ══════════════════════════════════════════════════════════════
   RENDER
   ══════════════════════════════════════════════════════════════ */
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
