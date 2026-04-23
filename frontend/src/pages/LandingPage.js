import { Link } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import {
  Bot,
  BarChart3,
  Users,
  Zap,
  ArrowRight,
  Check,
  Globe,
  Star,
  TrendingUp,
  Clock,
  Inbox,
  Target,
  BookOpen,
  Play,
  ChevronDown,
  Cpu,
  PieChart,
  Layers,
  Mail,
  FileText,
  HelpCircle,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { getLandingPricingPlans } from '@/data/publicPricingPlans';
import {
  NavBrandIconWhatsApp,
  NavBrandIconInstagram,
  NavBrandIconFacebook,
  NavBrandIconEmail,
} from '@/components/landing/ChannelBrandNavIcons';

/* ─── Scroll-in-view hook ─── */
function useInView(options = {}) {
  const ref = useRef(null);
  const [isInView, setIsInView] = useState(false);
  const { once = true, root = null, rootMargin = '0px', threshold = 0.1 } = options;
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setIsInView(true);
        if (once) observer.disconnect();
      }
    }, { threshold, root, rootMargin });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, [once, root, rootMargin, threshold]);
  return [ref, isInView];
}

/* ─── Animated counter ─── */
function AnimatedCounter({ value, suffix = '', duration = 2000 }) {
  const [count, setCount] = useState(0);
  const [ref, isInView] = useInView();
  const numericValue = parseFloat(value.replace(/[^0-9.]/g, '')) || 0;
  const hasDecimal = value.includes('.');
  useEffect(() => {
    if (!isInView) return;
    let start = 0;
    const increment = numericValue / (duration / 16);
    const timer = setInterval(() => {
      start += increment;
      if (start >= numericValue) { setCount(numericValue); clearInterval(timer); }
      else setCount(hasDecimal ? Math.round(start * 10) / 10 : Math.floor(start));
    }, 16);
    return () => clearInterval(timer);
  }, [isInView, numericValue, duration, hasDecimal]);
  return <span ref={ref}>{hasDecimal ? count.toFixed(1) : count}{suffix}</span>;
}

/* ─── FAQ item ─── */
function FaqItem({ q, a }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 14, overflow: 'hidden', marginBottom: 8 }}>
      <button onClick={() => setOpen(!open)}
        style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 22px', background: open ? '#f8fafc' : '#fff', cursor: 'pointer', border: 'none', textAlign: 'left', transition: 'background 0.2s' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', fontFamily: 'DM Sans' }}>{q}</span>
        <span style={{ color: open ? '#2563eb' : '#94a3b8', transition: 'transform 0.3s', transform: open ? 'rotate(45deg)' : 'rotate(0deg)', fontSize: 20, flexShrink: 0, lineHeight: 1 }}>+</span>
      </button>
      <div style={{ maxHeight: open ? 200 : 0, overflow: 'hidden', transition: 'max-height 0.4s cubic-bezier(0.4,0,0.2,1)' }}>
        <p style={{ padding: '0 22px 18px', fontSize: 13.5, color: '#64748b', lineHeight: 1.7, fontFamily: 'DM Sans', margin: 0 }}>{a}</p>
      </div>
    </div>
  );
}

/* ─── Typewriter headline (no cursor) ─── */
const PHRASES = [
  'Win More Customers.',
  'Close Deals Faster.',
  'Delight at Scale.',
  'Next-Gen Intelligence.',
];
function TypewriterHeadline() {
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [displayed, setDisplayed] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) {
      const t = setTimeout(() => { setPaused(false); setDeleting(true); }, 1600);
      return () => clearTimeout(t);
    }
    const phrase = PHRASES[phraseIdx];
    if (!deleting) {
      if (displayed.length < phrase.length) {
        const t = setTimeout(() => setDisplayed(phrase.slice(0, displayed.length + 1)), 55);
        return () => clearTimeout(t);
      } else { setPaused(true); }
    } else {
      if (displayed.length > 0) {
        const t = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 28);
        return () => clearTimeout(t);
      } else { setDeleting(false); setPhraseIdx((phraseIdx + 1) % PHRASES.length); }
    }
  }, [displayed, deleting, phraseIdx, paused]);

  return (
    <span style={{
      background: 'linear-gradient(135deg, #2563eb, #6366f1)',
      WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
      backgroundClip: 'text', display: 'inline-block', minWidth: 10,
    }}>
      {displayed || '\u00A0'}
    </span>
  );
}

/* ─── Image flip carousel ─── */
function FlipImageCarousel({ images, interval = 3000, animationType = 'flipY' }) {
  const [idx, setIdx] = useState(0);
  const [flipping, setFlipping] = useState(false);
  const flippingRef = useRef(false);

  const triggerFlip = useCallback(() => {
    if (flippingRef.current) return;
    flippingRef.current = true;
    setFlipping(true);
    setTimeout(() => {
      setIdx(i => (i + 1) % images.length);
      setFlipping(false);
      flippingRef.current = false;
    }, 500);
  }, [images.length]);

  useEffect(() => {
    const t = setInterval(() => {
      triggerFlip();
    }, interval);
    return () => clearInterval(t);
  }, [interval, triggerFlip]);

  const transforms = {
    flipY: flipping ? 'rotateY(90deg)' : 'rotateY(0deg)',
    flipX: flipping ? 'rotateX(90deg)' : 'rotateX(0deg)',
    spinZoom: flipping ? 'scale(0.1) rotate(180deg)' : 'scale(1) rotate(0deg)',
    swirl: flipping ? 'scale(0.05) rotate(360deg)' : 'scale(1) rotate(0deg)',
  };

  return (
    <div style={{ perspective: 800, width: '100%', height: '100%' }} onMouseEnter={triggerFlip}>
      <img
        src={images[idx]}
        alt=""
        style={{
          width: '100%', height: '100%', objectFit: 'cover', borderRadius: 16,
          transform: transforms[animationType],
          transition: 'transform 0.5s cubic-bezier(0.4, 0, 0.2, 1)',
          backfaceVisibility: 'hidden',
          transformOrigin: 'center center',
        }}
      />
    </div>
  );
}

/* ─── Nav Dropdown ─── */
function NavDropdown({ label, items }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} style={{ position: 'relative' }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button style={{
        display: 'flex', alignItems: 'center', gap: 4,
        fontSize: 14, color: open ? '#0f172a' : '#64748b',
        background: 'none', border: 'none', cursor: 'pointer', padding: '6px 2px',
        fontFamily: 'DM Sans', fontWeight: open ? 600 : 400,
        transition: 'color 0.2s',
        position: 'relative',
      }}>
        <span style={{
          position: 'relative',
        }}>
          {label}
          <span style={{
            position: 'absolute', bottom: -2, left: 0, right: 0,
            height: 2, background: 'linear-gradient(90deg,#2563eb,#6366f1)',
            borderRadius: 2,
            transform: open ? 'scaleX(1)' : 'scaleX(0)',
            transformOrigin: 'left',
            transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)',
          }} />
        </span>
        <ChevronDown size={14} style={{ transition: 'transform 0.25s', transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }} />
      </button>

      <div style={{
        position: 'absolute', top: 'calc(100% + 8px)', left: '50%',
        transform: open ? 'translateX(-50%) translateY(0) scale(1)' : 'translateX(-50%) translateY(-8px) scale(0.96)',
        opacity: open ? 1 : 0, pointerEvents: open ? 'all' : 'none',
        transition: 'all 0.22s cubic-bezier(0.4,0,0.2,1)',
        background: '#fff', border: '1px solid #e2e8f0',
        borderRadius: 14, boxShadow: '0 16px 40px rgba(0,0,0,0.12)', padding: '8px',
        minWidth: 240, zIndex: 100,
      }}>
        <div style={{ position: 'absolute', top: -5, left: '50%', transform: 'translateX(-50%)', width: 10, height: 10, background: '#fff', border: '1px solid #e2e8f0', borderRight: 'none', borderBottom: 'none', rotate: '45deg' }} />
        {items.map((item, i) => (
          <a key={i} href={item.href}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 12px',
              borderRadius: 10, textDecoration: 'none', color: 'inherit',
              transition: 'background 0.15s',
              cursor: 'pointer',
            }}
            onMouseEnter={e => e.currentTarget.style.background = '#f1f5f9'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >
            <div style={{
              width: 32, height: 32, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: item.color || '#eff6ff', flexShrink: 0, marginTop: 1,
            }}>
              {item.icon}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#0f172a', fontFamily: 'DM Sans' }}>{item.label}</div>
              {item.desc && <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 1, fontFamily: 'DM Sans' }}>{item.desc}</div>}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

/* ─── Simple nav link with underline ─── */
function NavLink({ href, children }) {
  const [hovered, setHovered] = useState(false);
  return (
    <a href={href} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      style={{ position: 'relative', fontSize: 14, color: hovered ? '#0f172a' : '#64748b', textDecoration: 'none', fontFamily: 'DM Sans', fontWeight: hovered ? 600 : 400, padding: '6px 2px', transition: 'color 0.2s' }}>
      {children}
      <span style={{ position: 'absolute', bottom: -2, left: 0, right: 0, height: 2, background: 'linear-gradient(90deg,#2563eb,#6366f1)', borderRadius: 2, transform: hovered ? 'scaleX(1)' : 'scaleX(0)', transformOrigin: 'left', transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)' }} />
    </a>
  );
}

/* ═══════════════════════════════════════════════════
   MAIN COMPONENT
═══════════════════════════════════════════════════ */
export default function LandingPage() {
  const [scrolled, setScrolled] = useState(false);
  const [isMobile, setIsMobile] = useState(() => (typeof window !== 'undefined' ? window.innerWidth < 768 : false));
  const [heroRef, heroInView] = useInView();
  const [metricsRef, metricsInView] = useInView();
  const [featuresRef, featuresInView] = useInView();
  const [pricingRef, pricingInView] = useInView();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  /* ── Image sets per feature block ── */
  const featureImages = [
    [
      'https://images.unsplash.com/photo-1531746790731-6c087fecd65a?w=600&q=80',
      'https://images.unsplash.com/photo-1677442135703-1787eea5ce01?w=600&q=80',
    ],
    [
      'https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=600&q=80',
      'https://images.unsplash.com/photo-1563986768494-4dee2763ff3f?w=600&q=80',
    ],
    [
      'https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=600&q=80',
      'https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=600&q=80',
    ],
    [
      'https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=600&q=80',
      'https://images.unsplash.com/photo-1543286386-713bdd548da4?w=600&q=80',
    ],
  ];

  const singleFlipType = 'flipY';

  const featureDeepDives = [
    {
      icon: Bot, badge: 'AI Intelligence',
      title: 'Let AI Handle the Routine, So Your Team Can Do More',
      desc: 'Pulse Engine\'s built-in AI automatically handles FAQs, qualification questions, and status updates — with sentiment analysis and smart escalation when a human touch is needed.',
      bullets: ['Auto-resolve up to 70% of queries without agent involvement', 'Sentiment-aware escalation to the right team member', 'Continuous learning from every conversation'],
      color: 'blue',
    },
    {
      icon: Inbox, badge: 'Unified Inbox',
      title: 'Every Channel. One Inbox. Zero Chaos.',
      desc: 'Stop juggling apps. Pulse Engine consolidates WhatsApp, Instagram, Facebook Messenger, Email, and Web Chat into a single, lightning-fast inbox that your whole team can work from.',
      bullets: ['Shared team inbox with real-time collision detection', 'Identity unification — one customer profile across every channel', 'Multi-agent AI orchestration with automatic human handoff'],
      color: 'indigo',
    },
    {
      icon: Target, badge: 'Lead Intelligence',
      title: 'Turn Conversations into Qualified Leads Automatically',
      desc: 'Every chat is an opportunity. Pulse Engine captures contact data, scores leads using BANT criteria, and feeds your pipeline — so nothing falls through the cracks.',
      bullets: ['Automatic lead capture from every incoming message', 'AI-powered BANT scoring in real time', 'CRM-ready export and native integrations'],
      color: 'violet',
    },
    {
      icon: BarChart3, badge: 'Analytics',
      title: 'Know Exactly What\'s Working — and What Isn\'t',
      desc: 'Real-time dashboards surface conversation volume, response times, CSAT scores, and conversion funnels. Share reports with stakeholders in one click.',
      bullets: ['Live dashboards updated every 30 seconds', 'Channel-by-channel breakdown and comparison', 'Exportable reports for any time range'],
      color: 'sky',
    },
  ];

  const pricingPlans = getLandingPricingPlans();

  const faqs = [
    { q: 'Which channels does Pulse Engine support?', a: 'Pulse Engine supports WhatsApp Business, Facebook Messenger, Instagram Direct, Email (IMAP + SMTP/Brevo), and an embeddable Web Chat widget out of the box. Additional channels can be added via webhooks.' },
    { q: 'How does the AI know when to escalate to a human?', a: 'The AI monitors sentiment, detects frustration signals, and recognises intent patterns that go beyond its confidence threshold. It hands off seamlessly with full conversation context.' },
    { q: 'Can multiple agents work on the same inbox?', a: 'Yes. The shared inbox includes real-time collision detection so two agents never respond to the same conversation simultaneously. You can also set routing rules by team or skill.' },
    { q: 'Is there a free trial?', a: 'When your environment offers a trial, you can start without a card. Pro ($29/mo) and Enterprise ($99/mo) are the same self-serve plans as on signup; Custom is arranged with our sales team.' },
    { q: 'How is my data secured?', a: 'All data is encrypted at rest and in transit. We offer JWT-based authentication, role-based access control, and are fully GDPR compliant. Enterprise plans support on-premise deployment.' },
    { q: 'Can I export my data?', a: 'Absolutely. You can export conversations, contacts, and analytics reports at any time in CSV or JSON format. We believe your data is yours.' },
  ];

  const colorMap = {
    blue:   { bg: '#eff6ff', text: '#2563eb', badge: 'rgba(37,99,235,0.1)', badgeText: '#1d4ed8' },
    indigo: { bg: '#eef2ff', text: '#4f46e5', badge: 'rgba(79,70,229,0.1)', badgeText: '#4338ca' },
    violet: { bg: '#f5f3ff', text: '#7c3aed', badge: 'rgba(124,58,237,0.1)', badgeText: '#6d28d9' },
    sky:    { bg: '#f0f9ff', text: '#0284c7', badge: 'rgba(2,132,199,0.1)',  badgeText: '#0369a1' },
  };

  /* ── Nav dropdown data ── */
  const featuresDropdown = [
    { label: 'AI Inbox', desc: 'Unified multi-channel inbox', href: '#features', icon: <Inbox size={15} color="#2563eb" />, color: '#eff6ff' },
    { label: 'AI Automation', desc: 'Auto-resolve & smart routing', href: '#features', icon: <Cpu size={15} color="#7c3aed" />, color: '#f5f3ff' },
    { label: 'Lead Scoring', desc: 'BANT-powered pipeline', href: '#features', icon: <Target size={15} color="#059669" />, color: '#ecfdf5' },
    { label: 'Analytics', desc: 'Real-time dashboards', href: '#features', icon: <PieChart size={15} color="#0284c7" />, color: '#f0f9ff' },
  ];
  const channelsDropdown = [
    { label: 'WhatsApp', desc: 'Business API integration', href: '#channels', icon: <NavBrandIconWhatsApp />, color: '#f0fdf4' },
    { label: 'Instagram', desc: 'DM & story replies', href: '#channels', icon: <NavBrandIconInstagram />, color: '#fdf4ff' },
    { label: 'Facebook', desc: 'Messenger & page inbox', href: '#channels', icon: <NavBrandIconFacebook />, color: '#eff6ff' },
    { label: 'Email', desc: 'Send/Revice Emails', href: '#channels', icon: <NavBrandIconEmail />, color: '#f1f5f9' },
    { label: 'Web Chat', desc: 'Embeddable widget', href: '#channels', icon: <Globe size={16} color="#64748b" strokeWidth={1.75} />, color: '#f8fafc' },
  ];
  const resourcesDropdown = [
    { label: 'Documentation', desc: 'Guides & API reference', href: '#', icon: <BookOpen size={15} color="#2563eb" />, color: '#eff6ff' },
    { label: 'Blog', desc: 'Tips & best practices', href: '#', icon: <FileText size={15} color="#7c3aed" />, color: '#f5f3ff' },
    { label: 'Help Center', desc: 'Answers to common questions', href: '#faq', icon: <HelpCircle size={15} color="#0284c7" />, color: '#f0f9ff' },
    { label: 'Contact Us', desc: 'Talk to our team', href: '#', icon: <Mail size={15} color="#059669" />, color: '#ecfdf5' },
  ];

  const s = {
    page: { minHeight: '100vh', background: '#fff', fontFamily: 'DM Sans, sans-serif' },
    /* header */
    header: {
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
      transition: 'all 0.3s',
      background: scrolled ? 'rgba(255,255,255,0.92)' : 'transparent',
      backdropFilter: scrolled ? 'blur(14px)' : 'none',
      borderBottom: scrolled ? '1px solid #f1f5f9' : '1px solid transparent',
      boxShadow: scrolled ? '0 2px 20px rgba(0,0,0,0.06)' : 'none',
    },
    headerInner: { maxWidth: 1280, margin: '0 auto', padding: '0 24px', height: 64, display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
    logo: { display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' },
    logoIcon: { width: 34, height: 34, borderRadius: 10, background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center' },
    logoText: { fontSize: 17, fontWeight: 800, color: '#0f172a', letterSpacing: '-0.5px', fontFamily: 'Syne' },
    nav: { display: 'flex', alignItems: 'center', gap: 28 },
    authBtns: { display: 'flex', alignItems: 'center', gap: 10 },
    signIn: { fontSize: 13.5, fontWeight: 500, color: '#475569', textDecoration: 'none', padding: '7px 14px', borderRadius: 8, transition: 'all 0.2s', fontFamily: 'DM Sans' },
    signUp: { fontSize: 13.5, fontWeight: 600, color: '#fff', background: 'linear-gradient(135deg,#2563eb,#6366f1)', padding: '8px 18px', borderRadius: 9, textDecoration: 'none', boxShadow: '0 4px 14px rgba(37,99,235,0.35)', transition: 'all 0.2s', fontFamily: 'DM Sans' },
  };

  return (
    <div className="landing-page-root" style={s.page}>
      <style>{`
        .landing-page-root,
        .landing-page-root * {
          font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
        }
        .landing-pricing-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 22px;
          align-items: stretch;
          max-width: 1180px;
          margin: 0 auto;
        }
        @media (max-width: 960px) {
          .landing-pricing-grid { grid-template-columns: 1fr; }
        }
      `}</style>
      {/* ═══ HEADER ═══ */}
      <header style={s.header}>
        <div style={s.headerInner}>
          <PlatformLogo fontWeight={800} />

          <nav style={s.nav}>
            <NavDropdown label="Features" items={featuresDropdown} />
            <NavDropdown label="Channels" items={channelsDropdown} />
            <NavLink href="#pricing">Pricing</NavLink>
            <NavDropdown label="Resources" items={resourcesDropdown} />
          </nav>

          <div style={s.authBtns}>
            <Link to="/signin" style={s.signIn}
              onMouseEnter={e => { e.target.style.background = '#f1f5f9'; e.target.style.color = '#0f172a'; }}
              onMouseLeave={e => { e.target.style.background = 'transparent'; e.target.style.color = '#475569'; }}>
              Sign In
            </Link>
            <Link to="/signup" style={s.signUp}
              onMouseEnter={e => { e.target.style.opacity = '0.88'; e.target.style.transform = 'translateY(-1px)'; }}
              onMouseLeave={e => { e.target.style.opacity = '1'; e.target.style.transform = 'translateY(0)'; }}>
              Get Started Free
            </Link>
          </div>
        </div>
      </header>

      {/* ═══ HERO ═══ */}
      <section ref={heroRef} style={{ position: 'relative', paddingTop: 120, paddingBottom: 72, paddingLeft: 24, paddingRight: 24, overflow: 'hidden' }}>
        {/* bg blobs */}
        <div style={{ position: 'absolute', inset: 0, zIndex: 0, pointerEvents: 'none', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', top: -120, right: -120, width: 560, height: 560, background: 'radial-gradient(circle, rgba(99,102,241,0.12) 0%, transparent 70%)', borderRadius: '50%' }} />
          <div style={{ position: 'absolute', top: 80, left: -80, width: 380, height: 380, background: 'radial-gradient(circle, rgba(37,99,235,0.1) 0%, transparent 70%)', borderRadius: '50%' }} />
          <div style={{ position: 'absolute', bottom: -40, left: '40%', width: 280, height: 280, background: 'radial-gradient(circle, rgba(124,58,237,0.07) 0%, transparent 70%)', borderRadius: '50%' }} />
        </div>

        <div style={{ maxWidth: 760, margin: '0 auto', textAlign: 'center', position: 'relative', zIndex: 1 }}>
          {/* Headline */}
          <h1 style={{
            fontFamily: 'Syne', fontWeight: 800, fontSize: 'clamp(30px, 4.8vw, 52px)',
            color: '#0f172a', lineHeight: 1.12, letterSpacing: '-1.5px', marginBottom: 22,
            opacity: heroInView ? 1 : 0, transform: heroInView ? 'translateY(0)' : 'translateY(20px)',
            transition: 'all 0.7s ease 0.1s',
          }}>
            <TypewriterHeadline />
            <br />
            <span style={{ color: '#0f172a' }}>All from One Inbox.</span>
          </h1>

          <p style={{
            fontSize: 17, color: '#64748b', maxWidth: 580, margin: '0 auto 28px',
            lineHeight: 1.65,
            opacity: heroInView ? 1 : 0, transform: heroInView ? 'translateY(0)' : 'translateY(20px)',
            transition: 'all 0.7s ease 0.2s',
          }}>
            Unify WhatsApp, Instagram, Facebook, Email, and Web Chat into a single AI-powered inbox. Resolve queries faster, capture leads automatically, and recognise every customer across channels.
          </p>

          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'center', marginBottom: 18,
            opacity: heroInView ? 1 : 0, transform: heroInView ? 'translateY(0)' : 'translateY(20px)',
            transition: 'all 0.7s ease 0.3s',
          }}>
            <Link to="/signup"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '13px 26px', background: 'linear-gradient(135deg,#2563eb,#6366f1)', color: '#fff', borderRadius: 12, fontWeight: 700, fontSize: 14, textDecoration: 'none', boxShadow: '0 8px 24px rgba(37,99,235,0.3)', transition: 'all 0.2s' }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 14px 32px rgba(37,99,235,0.4)'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(37,99,235,0.3)'; }}>
              Start Free — No Card Needed <ArrowRight size={16} />
            </Link>
            <button style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '13px 22px', background: '#fff', color: '#334155', border: '1.5px solid #e2e8f0', borderRadius: 12, fontWeight: 600, fontSize: 14, cursor: 'pointer', transition: 'all 0.2s', fontFamily: 'DM Sans' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#cbd5e1'; e.currentTarget.style.background = '#f8fafc'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#fff'; }}>
              <Play size={14} style={{ fill: '#334155' }} /> Watch Demo (2 min)
            </button>
          </div>

          <div style={{
            display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 20, fontSize: 12.5, color: '#94a3b8',
            opacity: heroInView ? 1 : 0, transition: 'all 0.7s ease 0.4s',
          }}>
            {['Free 14-day trial', 'No credit card required', 'Cancel anytime'].map(t => (
              <span key={t} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <Check size={13} color="#22c55e" /> {t}
              </span>
            ))}
          </div>
        </div>

        {/* Fake dashboard */}
        <div style={{
          maxWidth: 860, margin: '44px auto 0', position: 'relative', zIndex: 1,
          opacity: heroInView ? 1 : 0, transform: heroInView ? 'translateY(0)' : 'translateY(40px)',
          transition: 'all 1s ease 0.5s',
        }}>
          <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e2e8f0', boxShadow: '0 32px 80px rgba(15,23,42,0.12), 0 0 0 1px rgba(37,99,235,0.06)', overflow: 'hidden' }}>
            {/* browser chrome */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 16px', background: '#f8fafc', borderBottom: '1px solid #f1f5f9' }}>
              <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#fc5c57' }} />
              <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#fdbc2c' }} />
              <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#34c84a' }} />
              <div style={{ marginLeft: 12, flex: 1, background: '#fff', border: '1px solid #e2e8f0', borderRadius: 6, padding: '4px 10px', fontSize: 11, color: '#94a3b8' }}>app.pulseengine.io/inbox</div>
            </div>
            {/* mock inbox */}
            <div style={{ display: 'flex', height: 220 }}>
              {/* sidebar */}
              <div style={{ width: 180, borderRight: '1px solid #f1f5f9', background: '#fafafa', padding: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {[['💬','WhatsApp','12',true],['📘','Facebook','5',false],['📸','Instagram','3',false],['✉️','Email','4',false],['🌐','Web Chat','8',false]].map(([ico,name,count,active]) => (
                  <div key={name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 10px', borderRadius: 8, background: active ? '#fff' : 'transparent', boxShadow: active ? '0 1px 4px rgba(0,0,0,0.06)' : 'none', fontSize: 11.5, color: active ? '#0f172a' : '#94a3b8', fontWeight: active ? 600 : 400, fontFamily: 'DM Sans' }}>
                    <span>{ico} {name}</span>
                    <span style={{ padding: '2px 6px', borderRadius: 10, fontSize: 10, fontWeight: 700, background: active ? '#2563eb' : '#e2e8f0', color: active ? '#fff' : '#64748b' }}>{count}</span>
                  </div>
                ))}
              </div>
              {/* convo list */}
              <div style={{ width: 210, borderRight: '1px solid #f1f5f9', padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {[['Sarah M.','Hey, I need help with my order…','2m'],['James K.','Is the premium plan available?','8m'],['Priya S.','Thanks! That worked perfectly 🎉','15m']].map(([name,msg,time]) => (
                  <div key={name} style={{ padding: '8px 10px', borderRadius: 8, cursor: 'pointer' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#0f172a', fontFamily: 'DM Sans' }}>{name}</span>
                      <span style={{ fontSize: 10, color: '#94a3b8', fontFamily: 'DM Sans' }}>{time}</span>
                    </div>
                    <p style={{ fontSize: 11, color: '#94a3b8', margin: 0, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis', fontFamily: 'DM Sans' }}>{msg}</p>
                  </div>
                ))}
              </div>
              {/* chat */}
              <div style={{ flex: 1, padding: '14px 16px', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: 8 }}>
                <div style={{ alignSelf: 'flex-start', background: '#f1f5f9', borderRadius: '18px 18px 18px 4px', padding: '8px 13px', fontSize: 12, color: '#334155', maxWidth: '72%', fontFamily: 'DM Sans' }}>Hey, I need help with my order — it hasn't arrived yet.</div>
                <div style={{ alignSelf: 'flex-end', background: 'linear-gradient(135deg,#2563eb,#6366f1)', borderRadius: '18px 18px 4px 18px', padding: '8px 13px', fontSize: 12, color: '#fff', maxWidth: '72%', fontFamily: 'DM Sans' }}>Hi Sarah! Looking into that now. Could you share your order number? 😊</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#94a3b8', fontFamily: 'DM Sans' }}>
                  <Bot size={11} color="#2563eb" /> AI responded in 4s
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ═══ METRICS ═══ */}
      <section ref={metricsRef} style={{ padding: '72px 24px', background: 'linear-gradient(to bottom, #f8fafc, #fff)' }}>
        <div style={{ maxWidth: 1000, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 48, opacity: metricsInView ? 1 : 0, transform: metricsInView ? 'translateY(0)' : 'translateY(20px)', transition: 'all 0.7s' }}>
            <h2 style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#0f172a', marginBottom: 8, letterSpacing: '-0.5px' }}>Results Our Customers See</h2>
            <p style={{ fontSize: 15, color: '#64748b', maxWidth: 460, margin: '0 auto' }}>Based on aggregated data from businesses using Pulse Engine for 90+ days.</p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 20 }}>
            {[
              { icon: Bot, value: '70', suffix: '%', label: 'AI Resolution Rate', sub: 'of queries resolved without a human', color: 'blue' },
              { icon: Clock, value: '3', suffix: 'x', label: 'Faster Response', sub: 'compared to manual handling', color: 'indigo' },
              { icon: TrendingUp, value: '40', suffix: '%', label: 'More Leads', sub: 'captured from inbound conversations', color: 'violet' },
              { icon: Star, value: '4.9', suffix: '/5', label: 'Avg CSAT Score', sub: 'up from an industry avg of 3.8', color: 'sky' },
            ].map((m, i) => {
              const c = colorMap[m.color];
              return (
                <div key={m.label} style={{
                  background: '#fff', border: '1px solid #f1f5f9', borderRadius: 20, padding: '28px 20px', textAlign: 'center',
                  boxShadow: '0 2px 12px rgba(0,0,0,0.04)',
                  opacity: metricsInView ? 1 : 0, transform: metricsInView ? 'translateY(0)' : 'translateY(24px)',
                  transition: `all 0.7s ease ${i * 100}ms`,
                }}>
                  <div style={{ width: 42, height: 42, background: c.bg, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
                    <m.icon size={20} color={c.text} />
                  </div>
                  <p style={{ fontSize: 40, fontWeight: 800, color: c.text, marginBottom: 4, fontFamily: 'Syne', lineHeight: 1 }}>
                    <AnimatedCounter value={m.value} suffix={m.suffix} duration={1800 + i * 200} />
                  </p>
                  <p style={{ fontSize: 13.5, fontWeight: 600, color: '#0f172a', marginBottom: 4 }}>{m.label}</p>
                  <p style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.4 }}>{m.sub}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ═══ FEATURE DEEP-DIVES ═══ */}
      <section id="features" ref={featuresRef} style={{ padding: '72px 24px' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 52 }}>
            <h2 style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#0f172a', letterSpacing: '-0.5px', marginBottom: 10 }}>Everything Your Team Needs</h2>
            <p style={{ fontSize: 15, color: '#64748b', maxWidth: 460, margin: '0 auto' }}>Built for speed, designed for scale — features that actually move the needle.</p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 64 }}>
            {featureDeepDives.map((f, i) => {
              const c = colorMap[f.color];
              const isEven = i % 2 === 0;
              return (
                <div key={f.title} style={{
                  display: 'flex', flexDirection: isMobile ? 'column' : isEven ? 'row' : 'row-reverse',
                  alignItems: 'center', gap: 48,
                  opacity: featuresInView ? 1 : 0, transform: featuresInView ? 'translateY(0)' : 'translateY(32px)',
                  transition: `all 0.8s ease ${i * 150}ms`,
                }}>
                  {/* Text */}
                  <div style={{ flex: 1 }}>
                    <span style={{ display: 'inline-block', padding: '4px 12px', borderRadius: 100, fontSize: 12, fontWeight: 600, background: c.badge, color: c.badgeText, marginBottom: 14 }}>{f.badge}</span>
                    <h3 style={{ fontFamily: 'Syne', fontSize: 26, fontWeight: 800, color: '#0f172a', marginBottom: 14, lineHeight: 1.2, letterSpacing: '-0.3px' }}>{f.title}</h3>
                    <p style={{ fontSize: 15, color: '#64748b', marginBottom: 20, lineHeight: 1.65 }}>{f.desc}</p>
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {f.bullets.map(b => (
                        <li key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 14, color: '#334155' }}>
                          <span style={{ width: 20, height: 20, borderRadius: '50%', background: '#dcfce7', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1 }}><Check size={12} color="#16a34a" /></span>
                          {b}
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* Image flip card */}
                  <div style={{ flex: 1, height: 320, borderRadius: 20, overflow: 'hidden', background: c.bg, flexShrink: 0, position: 'relative' }}>
                    <FlipImageCarousel
                      images={featureImages[i]}
                      interval={3500 + i * 400}
                      animationType={singleFlipType}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ═══ CHANNELS ═══ */}
      <style>{`
        .ch-card { transition: transform 0.35s cubic-bezier(.34,1.56,.64,1), box-shadow 0.35s ease; }
        .ch-card:hover { transform: translateY(-10px) scale(1.03); }
        .wa-icon { position: relative; transition: transform 0.4s cubic-bezier(.34,1.56,.64,1); }
        .ch-card:hover .wa-icon { transform: scale(1.15); }
        .wa-ring { position:absolute; inset:-4px; border-radius:18px; border: 2px solid #25D366; opacity:0; pointer-events:none; }
        .ch-card:hover .wa-ring-1 { animation: wa-sonar 1s ease-out 0s forwards; }
        .ch-card:hover .wa-ring-2 { animation: wa-sonar 1s ease-out 0.25s forwards; }
        .ch-card:hover .wa-ring-3 { animation: wa-sonar 1s ease-out 0.5s forwards; }
        @keyframes wa-sonar { 0%{opacity:.7;transform:scale(1)} 100%{opacity:0;transform:scale(1.9)} }
        .fb-icon-wrap { perspective: 400px; }
        .fb-icon { transition: transform 0.6s cubic-bezier(.645,.045,.355,1); transform-style: preserve-3d; }
        .ch-card:hover .fb-icon { transform: rotateY(360deg); }
        .ig-icon { position: relative; overflow: hidden; transition: transform 0.4s cubic-bezier(.34,1.56,.64,1); }
        .ch-card:hover .ig-icon { transform: scale(1.12); }
        .ig-glare { position:absolute; inset:0; border-radius:14px; background: linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.55) 50%, transparent 60%); transform: translateX(-100%); }
        .ch-card:hover .ig-glare { animation: ig-sweep 0.6s ease-out forwards; }
        @keyframes ig-sweep { 0%{transform:translateX(-100%)} 100%{transform:translateX(200%)} }
        .wc-icon { transition: transform 0.4s cubic-bezier(.34,1.56,.64,1); }
        .ch-card:hover .wc-icon { animation: wc-spin 1.2s linear infinite; }
        @keyframes wc-spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
        .wc-orbit { position:absolute; inset:-8px; border-radius:22px; border: 1.5px dashed rgba(100,116,139,0.4); opacity:0; transition: opacity 0.3s; }
        .ch-card:hover .wc-orbit { opacity:1; animation: wc-orbit-spin 3s linear infinite; }
        @keyframes wc-orbit-spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
      `}</style>
      <section id="channels" style={{ padding: '72px 24px', background: '#f8fafc' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 44 }}>
            <h2 style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#0f172a', marginBottom: 8, letterSpacing: '-0.5px' }}>One Platform. Every Channel.</h2>
            <p style={{ fontSize: 15, color: '#64748b', maxWidth: 400, margin: '0 auto' }}>Your customers are everywhere. Meet them where they are.</p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 18 }}>
            {/* WhatsApp */}
            <div className="ch-card" style={{ background: '#fff', border: '1px solid #bbf7d0', borderRadius: 20, padding: '28px 20px', textAlign: 'center', cursor: 'pointer', boxShadow: '0 2px 12px rgba(37,211,102,0.07)' }}>
              <div className="wa-icon" style={{ width: 56, height: 56, background: '#25D366', borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px', position: 'relative' }}>
                <div className="wa-ring wa-ring-1" /><div className="wa-ring wa-ring-2" /><div className="wa-ring wa-ring-3" />
                <svg viewBox="0 0 24 24" style={{ width: 30, height: 30, fill: '#fff', position: 'relative', zIndex: 1 }}><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
              </div>
              <p style={{ fontSize: 13.5, fontWeight: 700, color: '#0f172a', marginBottom: 4, fontFamily: 'DM Sans' }}>WhatsApp</p>
              <p style={{ fontSize: 11.5, color: '#94a3b8', fontFamily: 'DM Sans' }}>Business API</p>
            </div>

            {/* Facebook */}
            <div className="ch-card" style={{ background: '#fff', border: '1px solid #bfdbfe', borderRadius: 20, padding: '28px 20px', textAlign: 'center', cursor: 'pointer', boxShadow: '0 2px 12px rgba(24,119,242,0.07)' }}>
              <div className="fb-icon-wrap" style={{ width: 56, height: 56, margin: '0 auto 14px' }}>
                <div className="fb-icon" style={{ width: 56, height: 56, background: '#1877F2', borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg viewBox="0 0 24 24" style={{ width: 30, height: 30, fill: '#fff' }}><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
                </div>
              </div>
              <p style={{ fontSize: 13.5, fontWeight: 700, color: '#0f172a', marginBottom: 4, fontFamily: 'DM Sans' }}>Facebook</p>
              <p style={{ fontSize: 11.5, color: '#94a3b8', fontFamily: 'DM Sans' }}>Messenger & Pages</p>
            </div>

            {/* Instagram */}
            <div className="ch-card ig-card" style={{ background: '#fff', border: '1px solid #fbcfe8', borderRadius: 20, padding: '28px 20px', textAlign: 'center', cursor: 'pointer', boxShadow: '0 2px 12px rgba(214,36,159,0.07)' }}>
              <div className="ig-icon" style={{ width: 56, height: 56, borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px', background: 'radial-gradient(circle at 30% 107%, #fdf497 0%, #fdf497 5%, #fd5949 45%, #d6249f 60%, #285AEB 90%)' }}>
                <div className="ig-glare" />
                <svg viewBox="0 0 24 24" style={{ width: 30, height: 30, fill: '#fff', position: 'relative', zIndex: 1 }}><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/></svg>
              </div>
              <p style={{ fontSize: 13.5, fontWeight: 700, color: '#0f172a', marginBottom: 4, fontFamily: 'DM Sans' }}>Instagram</p>
              <p style={{ fontSize: 11.5, color: '#94a3b8', fontFamily: 'DM Sans' }}>DM & Story Replies</p>
            </div>

            {/* Email */}
            <div className="ch-card" style={{ background: '#fff', border: '1px solid #bae6fd', borderRadius: 20, padding: '28px 20px', textAlign: 'center', cursor: 'pointer', boxShadow: '0 2px 12px rgba(2,132,199,0.07)' }}>
              <div style={{ width: 56, height: 56, background: 'linear-gradient(135deg,#0ea5e9,#0284c7)', borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
                <Mail size={28} color="#fff" />
              </div>
              <p style={{ fontSize: 13.5, fontWeight: 700, color: '#0f172a', marginBottom: 4, fontFamily: 'DM Sans' }}>Email</p>
              <p style={{ fontSize: 11.5, color: '#94a3b8', fontFamily: 'DM Sans' }}>IMAP & SMTP / Brevo</p>
            </div>

            {/* Web Chat */}
            <div className="ch-card" style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 20, padding: '28px 20px', textAlign: 'center', cursor: 'pointer', boxShadow: '0 2px 12px rgba(100,116,139,0.07)' }}>
              <div style={{ width: 56, height: 56, margin: '0 auto 14px', position: 'relative' }}>
                <div className="wc-orbit" />
                <div style={{ width: 56, height: 56, background: '#1e293b', borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Globe size={28} className="wc-icon" color="#fff" />
                </div>
              </div>
              <p style={{ fontSize: 13.5, fontWeight: 700, color: '#0f172a', marginBottom: 4, fontFamily: 'DM Sans' }}>Web Chat</p>
              <p style={{ fontSize: 11.5, color: '#94a3b8', fontFamily: 'DM Sans' }}>Embeddable Widget</p>
            </div>
          </div>
        </div>
      </section>

      {/* ═══ CAPABILITIES STRIP ═══ */}
      <section id="capabilities" style={{ padding: '72px 24px', background: '#fff' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 44 }}>
            <h2 style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#0f172a', marginBottom: 8, letterSpacing: '-0.5px' }}>Built for Real Conversations at Scale</h2>
            <p style={{ fontSize: 15, color: '#64748b', maxWidth: 520, margin: '0 auto' }}>Four pillars that turn every message — from any channel — into an intelligent, unified experience.</p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 18 }}>
            {[
              {
                icon: Users, color: 'blue',
                title: 'Identity Unification',
                desc: 'Every phone number, email, social ID, and web session is stitched into a single customer profile — automatically.',
              },
              {
                icon: Cpu, color: 'indigo',
                title: 'Multi-Agent AI',
                desc: 'Specialised agents (qualification, support, sales) collaborate on each conversation and hand off when a human is needed.',
              },
              {
                icon: Layers, color: 'violet',
                title: 'Conversation Memory',
                desc: 'Short-term + long-term memory means the AI remembers what was said yesterday — and last month.',
              },
              {
                icon: Zap, color: 'sky',
                title: 'Real-time Delivery',
                desc: 'WebSocket-powered inbox, sub-second AI replies, and live sentiment on every incoming message.',
              },
            ].map((cap, i) => {
              const c = colorMap[cap.color];
              return (
                <div key={cap.title} style={{
                  background: '#fff', border: '1px solid #f1f5f9', borderRadius: 18, padding: '24px 20px',
                  boxShadow: '0 2px 12px rgba(0,0,0,0.04)', transition: 'all 0.3s ease',
                }}
                  onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.boxShadow = '0 14px 32px rgba(15,23,42,0.08)'; }}
                  onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 2px 12px rgba(0,0,0,0.04)'; }}
                >
                  <div style={{ width: 42, height: 42, background: c.bg, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}>
                    <cap.icon size={20} color={c.text} />
                  </div>
                  <p style={{ fontSize: 15, fontWeight: 700, color: '#0f172a', marginBottom: 6, fontFamily: 'DM Sans' }}>{cap.title}</p>
                  <p style={{ fontSize: 13, color: '#64748b', lineHeight: 1.6, fontFamily: 'DM Sans' }}>{cap.desc}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ═══ PRICING ═══ */}
      <section id="pricing" ref={pricingRef} style={{ padding: '72px 24px', background: '#fff' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 48, opacity: pricingInView ? 1 : 0, transform: pricingInView ? 'translateY(0)' : 'translateY(20px)', transition: 'all 0.7s' }}>
            <h2 style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#0f172a', marginBottom: 8, letterSpacing: '-0.5px' }}>Pricing That Scales With You</h2>
            <p style={{ fontSize: 15, color: '#64748b', maxWidth: 560, margin: '0 auto' }}>
              Pro, Enterprise, and Custom share the same feature story—limits and support scale with each tier.
            </p>
          </div>
          <div className="landing-pricing-grid">
            {pricingPlans.map((plan, i) => (
              <div key={plan.code} style={{
                position: 'relative', borderRadius: 22, padding: '32px 28px',
                background: plan.popular ? 'linear-gradient(145deg, #2563eb, #4f46e5)' : '#fff',
                border: plan.popular ? 'none' : '1px solid #e2e8f0',
                boxShadow: plan.popular ? '0 24px 60px rgba(37,99,235,0.35)' : '0 2px 12px rgba(0,0,0,0.04)',
                transform: plan.popular ? 'scale(1.05)' : 'scale(1)',
                opacity: pricingInView ? 1 : 0, transition: `all 0.7s ease ${i * 150}ms`,
              }}>
                {plan.popular && (
                  <div style={{ position: 'absolute', top: -14, left: '50%', transform: 'translateX(-50%)', padding: '5px 16px', background: '#fbbf24', color: '#78350f', fontSize: 11, fontWeight: 800, borderRadius: 100, whiteSpace: 'nowrap' }}>Most Popular</div>
                )}
                <div style={{ marginBottom: 16 }}>
                  <h3 style={{ fontFamily: 'Syne', fontSize: 22, fontWeight: 800, color: plan.popular ? '#fff' : '#0f172a', marginBottom: 4 }}>{plan.name}</h3>
                  <p style={{ fontSize: 13, color: plan.popular ? 'rgba(255,255,255,0.7)' : '#94a3b8' }}>{plan.description}</p>
                </div>
                <div style={{ marginBottom: 22 }}>
                  <span style={{ fontSize: 42, fontWeight: 800, color: plan.popular ? '#fff' : '#0f172a', fontFamily: 'Syne' }}>{plan.price}</span>
                  {plan.period && <span style={{ fontSize: 14, color: plan.popular ? 'rgba(255,255,255,0.6)' : '#94a3b8' }}>{plan.period}</span>}
                </div>
                <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 24px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {plan.features.map((f, fi) => (
                    <li key={fi} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, fontSize: 12.75, lineHeight: 1.45, color: plan.popular ? 'rgba(255,255,255,0.88)' : '#475569', fontFamily: 'DM Sans' }}>
                      <Check size={14} color={plan.popular ? 'rgba(255,255,255,0.75)' : '#22c55e'} style={{ flexShrink: 0, marginTop: 2 }} /> {f}
                    </li>
                  ))}
                </ul>
                <Link to={plan.href} style={{
                  display: 'block', width: '100%', padding: '13px 0', textAlign: 'center',
                  background: plan.popular ? '#fff' : plan.code === 'custom' ? '#fff' : 'linear-gradient(135deg,#2563eb,#6366f1)',
                  color: plan.popular ? '#2563eb' : plan.code === 'custom' ? '#0f172a' : '#fff',
                  borderRadius: 12, fontWeight: 700, fontSize: 14, textDecoration: 'none',
                  transition: 'all 0.2s', fontFamily: 'DM Sans',
                  border: plan.code === 'custom' ? '2px solid #cbd5e1' : 'none',
                  boxShadow: plan.popular ? 'none' : plan.code === 'custom' ? 'none' : '0 4px 14px rgba(37,99,235,0.3)',
                }}>
                  {plan.cta}
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ═══ FAQ ═══ */}
      <section id="faq" style={{ padding: '72px 24px', background: '#f8fafc' }}>
        <div style={{ maxWidth: 680, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 40 }}>
            <h2 style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#0f172a', marginBottom: 8, letterSpacing: '-0.5px' }}>Frequently Asked Questions</h2>
            <p style={{ fontSize: 15, color: '#64748b' }}>Everything you need to know before getting started.</p>
          </div>
          {faqs.map(f => <FaqItem key={f.q} q={f.q} a={f.a} />)}
        </div>
      </section>

      {/* ═══ FINAL CTA ═══ */}
      <section style={{ position: 'relative', padding: '88px 24px', background: 'linear-gradient(135deg, #1e3a8a 0%, #2563eb 40%, #4f46e5 70%, #6d28d9 100%)', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <div style={{ position: 'absolute', top: -60, right: -40, width: 320, height: 320, background: 'rgba(255,255,255,0.07)', borderRadius: '50%' }} />
          <div style={{ position: 'absolute', bottom: -80, left: -40, width: 280, height: 280, background: 'rgba(255,255,255,0.05)', borderRadius: '50%' }} />
        </div>
        <div style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center', position: 'relative', zIndex: 1 }}>
          <h2 style={{ fontFamily: 'Syne', fontSize: 'clamp(28px, 5vw, 44px)', fontWeight: 800, color: '#fff', marginBottom: 14, letterSpacing: '-0.5px' }}>Start Closing More. Faster.</h2>
          <p style={{ fontSize: 16, color: 'rgba(255,255,255,0.75)', marginBottom: 36, maxWidth: 480, margin: '0 auto 36px' }}>Join thousands of teams using Pulse Engine to turn conversations into customers.</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12 }}>
            <Link to="/signup" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '14px 28px', background: '#fff', color: '#2563eb', borderRadius: 12, fontWeight: 700, fontSize: 14, textDecoration: 'none', boxShadow: '0 8px 24px rgba(0,0,0,0.18)', transition: 'all 0.2s', fontFamily: 'DM Sans' }}>
              Get Started Free <ArrowRight size={16} />
            </Link>
            <Link to="/contact" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '14px 24px', border: '1.5px solid rgba(255,255,255,0.3)', color: '#fff', borderRadius: 12, fontWeight: 600, fontSize: 14, textDecoration: 'none', transition: 'all 0.2s', fontFamily: 'DM Sans' }}>
              Talk to Sales
            </Link>
          </div>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', marginTop: 20 }}>No credit card · 14-day free trial · Cancel anytime</p>
        </div>
      </section>

      {/* ═══ FOOTER ═══ */}
      <footer style={{ padding: '60px 24px 32px', background: '#0f172a', color: '#94a3b8' }}>
        <div style={{ maxWidth: 1280, margin: '0 auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr', gap: 40, marginBottom: 48 }}>
            <div>
              <div style={{ marginBottom: 14 }}>
                <PlatformLogo textColor="#fff" imageWidth={30} fontSize={16} fontWeight={800} />
              </div>
              <p style={{ fontSize: 13.5, lineHeight: 1.65, maxWidth: 260 }}>AI-powered customer engagement for modern teams. Unify every channel in one intelligent inbox.</p>
            </div>
            {[
              { title: 'Product', links: [['Features','#features'],['Channels','#channels'],['Pricing','#pricing'],['FAQ','#faq']] },
              { title: 'Resources', links: [['Knowledge Base','/knowledge'],['Analytics','/analytics'],['Support','/contact']] },
              { title: 'Company', links: [['Privacy Policy','/privacy'],['Terms of Service','/terms'],['Contact Us','/contact']] },
            ].map(col => (
              <div key={col.title}>
                <p style={{ fontSize: 11, fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 16, fontFamily: 'DM Sans' }}>{col.title}</p>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {col.links.map(([label, href]) => (
                    <li key={label}><a href={href} style={{ fontSize: 13.5, color: '#94a3b8', textDecoration: 'none', transition: 'color 0.2s', fontFamily: 'DM Sans' }} onMouseEnter={e => e.target.style.color = '#fff'} onMouseLeave={e => e.target.style.color = '#94a3b8'}>{label}</a></li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div style={{ borderTop: '1px solid #1e293b', paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <p style={{ fontSize: 12.5, fontFamily: 'DM Sans' }}>© 2026 Pulse Engine. All rights reserved.</p>
            <p style={{ fontSize: 12, color: '#334155', fontFamily: 'DM Sans' }}>Manage leads, conversations, and growth in one intelligent inbox.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
