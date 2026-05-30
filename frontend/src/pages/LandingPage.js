import { Link } from 'react-router-dom';import {
  AnimatePresence,
  motion,
  useMotionValue,
  useScroll,
  useSpring,
  useTransform,
} from 'framer-motion';
import { gsap } from 'gsap';
import Lenis from 'lenis';
import {
  ArrowRight,
  Bot,
  BookOpen,
  Check,
  ChevronDown,
  Clock,
  Cpu,
  FileText,
  Globe,
  HelpCircle,
  Inbox,
  Layers,
  Mail,
  Menu,
  Star,
  Target,
  TrendingUp,
  Users,
  X,
  Zap,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import PlatformLogo from '@/components/PlatformLogo';
import { getLandingPricingPlans } from '@/data/publicPricingPlans';
import {
  NavBrandIconWhatsApp,
  NavBrandIconInstagram,
  NavBrandIconFacebook,
  NavBrandIconEmail,
} from '@/components/landing/ChannelBrandNavIcons';

const MotionLink = motion(Link);

const HERO_HEADLINES = [
  'Your Autonomous AI CRM',
  'AI-Powered Customer Intelligence',
  'Turn Conversations Into Revenue Automatically',
  'An AI Operating System For Revenue Teams',
];

const HERO_STATS = [
  { value: 70, suffix: '%', label: 'AI resolution' },
  { value: 3, suffix: 'x', label: 'faster first response' },
  { value: 40, suffix: '%', label: 'more qualified pipeline' },
];

const HERO_FLOATING_CARDS = [
  {
    title: 'Lead heat spike',
    body: 'Enterprise inquiry crossed buying intent threshold.',
    badge: '91 / 100 score',
    className: 'left-0 top-20 w-52 lg:-left-12',
  },
  {
    title: 'AI summary ready',
    body: 'Cross-channel profile merged with product and billing history.',
    badge: '12 signals synced',
    className: 'right-0 top-4 w-56 lg:-right-10',
  },
  {
    title: 'Revenue forecast',
    body: 'Pipeline probability improved after automated follow-up.',
    badge: '+$18.4k this week',
    className: 'right-2 bottom-24 w-60 lg:-right-8',
  },
  {
    title: 'Workflow branch',
    body: 'WhatsApp lead routed to human owner after price objection.',
    badge: '2-second handoff',
    className: 'left-4 bottom-16 w-48 lg:-left-6',
  },
];

const FEATURES_DROPDOWN = [
  {
    label: 'AI Inbox',
    desc: 'Unified omnichannel command center',
    href: '#dashboard',
    icon: <Inbox size={15} color="#2563eb" />,
    color: '#eff6ff',
  },
  {
    label: 'AI Automation',
    desc: 'Scoring, nurture, routing, and follow-up',
    href: '#workflow',
    icon: <Cpu size={15} color="#4f46e5" />,
    color: '#eef2ff',
  },
  {
    label: 'Lead Intelligence',
    desc: 'Intent, heat scoring, and qualification',
    href: '#analytics',
    icon: <Target size={15} color="#7c3aed" />,
    color: '#f5f3ff',
  },
  {
    label: 'AI Ecosystem',
    desc: '360 degree operating model',
    href: '#ecosystem',
    icon: <Layers size={15} color="#0284c7" />,
    color: '#f0f9ff',
  },
];

const CHANNELS_DROPDOWN = [
  { label: 'WhatsApp', desc: 'Business automation', href: '#channels', icon: <NavBrandIconWhatsApp />, color: '#f0fdf4' },
  { label: 'Instagram', desc: 'DM and story replies', href: '#channels', icon: <NavBrandIconInstagram />, color: '#fdf4ff' },
  { label: 'Facebook', desc: 'Messenger and page inbox', href: '#channels', icon: <NavBrandIconFacebook />, color: '#eff6ff' },
  { label: 'Email', desc: 'Inbound and outbound sync', href: '#channels', icon: <NavBrandIconEmail />, color: '#f8fafc' },
  { label: 'Web Chat', desc: 'Embeddable live widget', href: '#channels', icon: <Globe size={16} color="#64748b" />, color: '#f8fafc' },
];

const RESOURCES_DROPDOWN = [
  { label: 'Workflow Tour', desc: 'See automation end to end', href: '#workflow', icon: <BookOpen size={15} color="#2563eb" />, color: '#eff6ff' },
  { label: 'AI Analytics', desc: 'Forecasts, intent, and signals', href: '#analytics', icon: <FileText size={15} color="#4f46e5" />, color: '#eef2ff' },
  { label: 'Help Center', desc: 'Answers before rollout', href: '#faq', icon: <HelpCircle size={15} color="#0284c7" />, color: '#f0f9ff' },
  { label: 'Contact Us', desc: 'Talk to our team', href: '/contact', icon: <Mail size={15} color="#059669" />, color: '#ecfdf5' },
];

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
  if (!ctx || R <= 0) return;
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

const WORKFLOW_STEPS = [
  {
    title: 'Lead enters',
    body: 'Website chat, WhatsApp, DM, or email lands in one queue.',
    icon: Inbox,
    accent: 'from-blue-500/20 to-blue-500/6',
  },
  {
    title: 'AI analyzes',
    body: 'Intent, urgency, source, and channel history are parsed instantly.',
    icon: Cpu,
    accent: 'from-indigo-500/20 to-indigo-500/6',
  },
  {
    title: 'AI scores',
    body: 'BANT and conversion probability update in real time.',
    icon: Target,
    accent: 'from-violet-500/20 to-violet-500/6',
  },
  {
    title: 'AI nurtures',
    body: 'Personalized responses and follow-up paths launch automatically.',
    icon: Zap,
    accent: 'from-sky-500/20 to-sky-500/6',
  },
  {
    title: 'Human handoff',
    body: 'Reps receive a crisp AI brief only when human nuance matters.',
    icon: Users,
    accent: 'from-slate-700/14 to-slate-700/5',
  },
  {
    title: 'Deal closes',
    body: 'Revenue forecast, activity history, and next steps stay in sync.',
    icon: TrendingUp,
    accent: 'from-blue-500/20 to-indigo-500/6',
  },
];

const CHANNEL_STREAMS = [
  {
    title: 'WhatsApp',
    subtitle: 'Expansion request',
    body: 'Need pricing for three seats and onboarding this month.',
    icon: NavBrandIconWhatsApp,
    accent: 'bg-emerald-500/10 text-emerald-700',
    glow: 'shadow-[0_20px_70px_-36px_rgba(16,185,129,0.55)]',
  },
  {
    title: 'Instagram',
    subtitle: 'Product discovery',
    body: 'Can your AI route conversations to different teams automatically?',
    icon: NavBrandIconInstagram,
    accent: 'bg-fuchsia-500/10 text-fuchsia-700',
    glow: 'shadow-[0_20px_70px_-36px_rgba(217,70,239,0.45)]',
  },
  {
    title: 'Email',
    subtitle: 'Pricing review',
    body: 'Please summarize all open conversations before our executive meeting.',
    icon: NavBrandIconEmail,
    accent: 'bg-slate-500/10 text-slate-700',
    glow: 'shadow-[0_20px_70px_-36px_rgba(15,23,42,0.35)]',
  },
];

const ANALYTICS_METRICS = [
  { label: 'Intent prediction', value: 82, suffix: '%', sub: 'average deal readiness confidence' },
  { label: 'Forecast accuracy', value: 94, suffix: '%', sub: 'week-over-week revenue projection stability' },
  { label: 'Average response', value: 19, suffix: 's', sub: 'blended across all active channels' },
  { label: 'CSAT trend', value: 4.9, suffix: '/5', sub: 'customer satisfaction after AI triage', decimals: 1 },
];

const TESTIMONIALS = [
  {
    name: 'Ayesha Khan',
    role: 'Revenue Operations Lead',
    company: 'Northstar Health',
    metric: '41% more qualified pipeline',
    quote: 'Pulse Engine stopped our team from context-switching across channels. The AI now does the first layer of thinking before reps ever touch the deal.',
  },
  {
    name: 'Daniel Moore',
    role: 'VP of Growth',
    company: 'Atlas Commerce',
    metric: '3.2x faster first response',
    quote: 'The omnichannel inbox feels less like a support tool and more like a live operating system for pipeline generation.',
  },
  {
    name: 'Sana Rahman',
    role: 'Customer Success Director',
    company: 'Ventra Cloud',
    metric: '70% of inbound handled by AI',
    quote: 'What impressed leadership most was not the automation alone, but how calm and enterprise-ready the experience feels.',
  },
];

const FAQS = [
  {
    q: 'Which channels does Pulse Engine support?',
    a: 'Pulse Engine supports WhatsApp Business, Facebook Messenger, Instagram Direct, Email, and embeddable web chat out of the box. Additional channel events can be connected through webhooks.',
  },
  {
    q: 'How does the AI know when to escalate to a human?',
    a: 'The AI watches for sentiment shifts, low-confidence answers, buying signals, and workflow thresholds. When a handoff is needed, the assigned teammate receives a summary, score, and recommended next action.',
  },
  {
    q: 'Can multiple agents work from the same inbox?',
    a: 'Yes. Pulse Engine supports shared queue ownership, collision prevention, role-based routing, and AI-to-human transitions without losing conversation memory.',
  },
  {
    q: 'Is there a free trial?',
    a: 'Yes. Teams can start with a 14-day free trial and no card required. Pro and Enterprise can continue self-serve, while Custom is handled directly with our team.',
  },
  {
    q: 'How is customer data secured?',
    a: 'Data is encrypted in transit and at rest, access is role-aware, and enterprise workflows can be configured around governance requirements. The product is designed to be operationally trustworthy as usage scales.',
  },
  {
    q: 'Can we export our data and reporting?',
    a: 'Absolutely. Conversation history, customer profiles, and analytics can be exported in standard formats so your team always retains control of its operational data.',
  },
];

const FOOTER_COLUMNS = [
  {
    title: 'Product',
    links: [
      ['Ecosystem', '#ecosystem'],
      ['Workflow', '#workflow'],
      ['Channels', '#channels'],
      ['Pricing', '#pricing'],
    ],
  },
  {
    title: 'Resources',
    links: [
      ['AI Analytics', '#analytics'],
      ['Testimonials', '#testimonials'],
      ['FAQ', '#faq'],
      ['Contact Us', '/contact'],
    ],
  },
  {
    title: 'Company',
    links: [
      ['Privacy Policy', '/privacy'],
      ['Terms of Service', '/terms'],
      ['Talk to Sales', '/contact'],
    ],
  },
];



const revealTransition = {
  duration: 0.8,
  ease: [0.22, 1, 0.36, 1],
};

function cn(...classes) {
  return classes.filter(Boolean).join(' ');
}

function cleanCopy(text) {
  return text.replace(/â€”|—/g, ' - ');
}



function useInView(options = {}) {
  const ref = useRef(null);
  const [isInView, setIsInView] = useState(false);
  const { once = true, root = null, rootMargin = '0px', threshold = 0.2 } = options;

  useEffect(() => {
    if (!ref.current) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsInView(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setIsInView(false);
        }
      },
      { threshold, root, rootMargin },
    );

    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [once, root, rootMargin, threshold]);

  return [ref, isInView];
}

function useReducedMotionPreference() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;

    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mediaQuery.matches);

    onChange();
    mediaQuery.addEventListener('change', onChange);
    return () => mediaQuery.removeEventListener('change', onChange);
  }, []);

  return reduced;
}

function useRotatingHeadline(items, delay = 2800) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setIndex((current) => (current + 1) % items.length);
    }, delay);

    return () => window.clearInterval(timer);
  }, [delay, items.length]);

  return items[index];
}

function SmartLink({ href, className, children, onAnchorClick, onClick, ...rest }) {
  if (href.startsWith('#')) {
    return (
      <a
        href={href}
        className={className}
        onClick={(event) => {
          onClick?.(event);
          onAnchorClick?.(event, href);
        }}
        {...rest}
      >
        {children}
      </a>
    );
  }

  return (
    <Link
      to={href}
      className={className}
      onClick={onClick}
      {...rest}
    >
      {children}
    </Link>
  );
}

function ParallaxLayer({ depth, x, y, className, style, children, ...rest }) {
  const translateX = useTransform(x, (value) => value * depth);
  const translateY = useTransform(y, (value) => value * depth);

  return (
    <motion.div
      className={className}
      style={{ x: translateX, y: translateY, ...style }}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

function AnimatedCounter({ value, suffix = '', decimals = 0, duration = 1600 }) {
  const [displayValue, setDisplayValue] = useState(0);
  const [ref, inView] = useInView({ threshold: 0.45 });

  useEffect(() => {
    if (!inView) return undefined;

    let startTime = null;
    let frameId = null;

    const step = (timestamp) => {
      if (!startTime) startTime = timestamp;
      const progress = Math.min((timestamp - startTime) / duration, 1);
      const nextValue = value * progress;
      setDisplayValue(nextValue);

      if (progress < 1) {
        frameId = window.requestAnimationFrame(step);
      }
    };

    frameId = window.requestAnimationFrame(step);
    return () => {
      if (frameId) window.cancelAnimationFrame(frameId);
    };
  }, [duration, inView, value]);

  const output = decimals > 0 ? displayValue.toFixed(decimals) : Math.round(displayValue).toString();

  return (
    <span ref={ref}>
      {output}
      {suffix}
    </span>
  );
}

function SectionHeading({ eyebrow, title, description, align = 'center', dark = false }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={revealTransition}
      className={cn(
        'max-w-3xl',
        align === 'center' ? 'mx-auto text-center' : 'max-w-2xl',
      )}
    >
      <div
        className={cn(
          'inline-flex items-center gap-2 rounded-full border px-4 py-2 text-[0.72rem] font-semibold uppercase tracking-[0.32em] backdrop-blur-xl',
          dark
            ? 'border-white/10 bg-white/5 text-slate-300 shadow-[0_20px_60px_-46px_rgba(255,255,255,0.06)]'
            : 'border-blue-500/12 bg-white/80 text-blue-700 shadow-[0_20px_60px_-46px_rgba(37,99,235,0.6)]',
          align === 'center' && 'mx-auto',
        )}
      >
        <span className="h-2 w-2 rounded-full bg-gradient-to-r from-blue-500 to-indigo-500" />
        {eyebrow}
      </div>
      <h2 className={cn(
        'mt-6 text-balance text-3xl font-semibold tracking-[-0.04em] sm:text-4xl lg:text-[3rem]',
        dark ? 'text-white' : 'text-slate-950'
      )}>
        {title}
      </h2>
      <p className={cn(
        'mt-5 text-pretty text-base leading-8 sm:text-lg',
        dark ? 'text-slate-400' : 'text-slate-600'
      )}>
        {description}
      </p>
    </motion.div>
  );
}

function MagneticAction({ variant = 'primary', className, children, to, onClick, href }) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const springX = useSpring(x, { stiffness: 190, damping: 16, mass: 0.45 });
  const springY = useSpring(y, { stiffness: 190, damping: 16, mass: 0.45 });

  const baseClasses = 'group relative inline-flex items-center justify-center gap-2 overflow-hidden rounded-2xl px-6 py-3 text-sm font-semibold tracking-[0.01em] transition-[box-shadow,background-color,color,transform] duration-300 ease-out';
  const variantClasses = {
    primary: 'bg-gradient-to-r from-blue-600 via-blue-600 to-indigo-600 text-white shadow-[0_24px_60px_-30px_rgba(37,99,235,0.8)]',
    secondary: 'border border-slate-200/80 bg-white/80 text-slate-700 shadow-[0_18px_48px_-34px_rgba(15,23,42,0.32)] backdrop-blur-xl',
    light: 'border border-white/30 bg-white/14 text-white backdrop-blur-xl shadow-[0_18px_48px_-34px_rgba(15,23,42,0.4)]',
  };

  const sharedProps = {
    className: cn(baseClasses, variantClasses[variant], className),
    style: { x: springX, y: springY },
    whileTap: { scale: 0.98 },
    onMouseMove: (event) => {
      const rect = event.currentTarget.getBoundingClientRect();
      const offsetX = event.clientX - rect.left - rect.width / 2;
      const offsetY = event.clientY - rect.top - rect.height / 2;
      x.set(offsetX * 0.11);
      y.set(offsetY * 0.11);
    },
    onMouseLeave: () => {
      x.set(0);
      y.set(0);
    },
  };

  const overlay = (
    <span className="pointer-events-none absolute inset-0 rounded-2xl bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.32),transparent_58%)] opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
  );

  if (to) {
    return (
      <MotionLink to={to} {...sharedProps}>
        {overlay}
        <span className="relative z-[1] inline-flex items-center gap-2">{children}</span>
      </MotionLink>
    );
  }

  if (href) {
    return (
      <motion.a href={href} {...sharedProps}>
        {overlay}
        <span className="relative z-[1] inline-flex items-center gap-2">{children}</span>
      </motion.a>
    );
  }

  return (
    <motion.button type="button" onClick={onClick} {...sharedProps}>
      {overlay}
      <span className="relative z-[1] inline-flex items-center gap-2">{children}</span>
    </motion.button>
  );
}

function NavDropdown({ label, items, onAnchorClick }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const closeTimerRef = useRef(null);

  const clearCloseTimer = () => {
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const handleOpen = () => {
    clearCloseTimer();
    setOpen(true);
  };

  const handleClose = () => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      closeTimerRef.current = null;
    }, 110);
  };

  useEffect(() => {
    const onMouseDown = (event) => {
      if (ref.current && !ref.current.contains(event.target)) {
        clearCloseTimer();
        setOpen(false);
      }
    };

    document.addEventListener('mousedown', onMouseDown);
    return () => {
      clearCloseTimer();
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, []);

  return (
    <div
      ref={ref}
      className="relative hidden lg:block"
      onMouseEnter={handleOpen}
      onMouseLeave={handleClose}
    >
      <button
        type="button"
        onFocus={handleOpen}
        className="group flex items-center gap-1.5 bg-transparent px-1 py-2 text-sm font-medium text-slate-600 transition-colors hover:text-slate-950"
      >
        <span className="relative">
          {label}
          <span className={cn(
            'absolute inset-x-0 -bottom-1 h-[2px] origin-left rounded-full bg-gradient-to-r from-blue-600 to-indigo-600 transition-transform duration-300',
            open ? 'scale-x-100' : 'scale-x-0',
          )} />
        </span>
        <ChevronDown size={15} className={cn('transition-transform duration-300', open && 'rotate-180')} />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -10, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.96 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className="absolute left-1/2 top-[calc(100%+14px)] z-[90] w-[270px] -translate-x-1/2 rounded-3xl border border-slate-200/90 bg-white p-3 shadow-[0_32px_90px_-42px_rgba(15,23,42,0.28)]"
          >
            <div className="absolute -top-1.5 left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-l border-t border-slate-200/90 bg-white" />
            {items.map((item) => (
              <SmartLink
                key={item.label}
                href={item.href}
                onAnchorClick={onAnchorClick}
                onClick={() => setOpen(false)}
                className="flex items-start gap-3 rounded-2xl px-3 py-3 transition-colors hover:bg-slate-50"
              >
                <div
                  className="mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-2xl"
                  style={{ background: item.color }}
                >
                  {item.icon}
                </div>
                <div>
                  <div className="text-sm font-semibold text-slate-950">{item.label}</div>
                  <div className="mt-1 text-xs leading-5 text-slate-500">{item.desc}</div>
                </div>
              </SmartLink>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function NavLink({ href, children, onAnchorClick }) {
  return (
    <SmartLink
      href={href}
      onAnchorClick={onAnchorClick}
      className="group relative hidden px-1 py-2 text-sm font-medium text-slate-600 transition-colors hover:text-slate-950 lg:inline-flex"
    >
      {children}
      <span className="absolute inset-x-0 -bottom-1 h-[2px] origin-left scale-x-0 rounded-full bg-gradient-to-r from-blue-600 to-indigo-600 transition-transform duration-300 group-hover:scale-x-100" />
    </SmartLink>
  );
}

function FaqItem({ q, a, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.16 }}
      transition={revealTransition}
      className="overflow-hidden rounded-[26px] border border-white/70 bg-white/84 shadow-[0_22px_60px_-42px_rgba(15,23,42,0.24)] backdrop-blur-xl"
    >
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center justify-between gap-6 px-6 py-5 text-left sm:px-7"
      >
        <span className="text-sm font-semibold text-slate-950 sm:text-base">{q}</span>
        <span
          className={cn(
            'flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition-all duration-300',
            open && 'rotate-180 border-blue-200 bg-blue-50 text-blue-700',
          )}
        >
          <ChevronDown size={17} />
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.24, ease: 'easeInOut' }}
          >
            <p className="px-6 pb-6 text-sm leading-7 text-slate-600 sm:px-7 sm:text-[0.95rem]">
              {a}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}



function HeroEcosystem({ mouseX, mouseY, onMouseMove, onMouseLeave }) {
  const [hoveredCardIndex, setHoveredCardIndex] = useState(null);

  return (
    <div
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      className="relative mx-auto aspect-[0.94] w-full max-w-[680px]"
    >
      <div className="landing-mesh absolute inset-[6%] rounded-[38px] bg-[radial-gradient(circle_at_20%_20%,rgba(59,130,246,0.2),transparent_30%),radial-gradient(circle_at_85%_18%,rgba(99,102,241,0.22),transparent_28%),radial-gradient(circle_at_55%_82%,rgba(14,165,233,0.14),transparent_28%),linear-gradient(135deg,rgba(255,255,255,0.9),rgba(244,247,255,0.82))] blur-3xl" />
      <div className="absolute inset-[4%] rounded-[42px] border border-white/70 bg-white/42 backdrop-blur-[2px]" />

      <div className="pointer-events-none absolute inset-0">
        <div data-signal-line className="signal-track absolute left-[11%] top-[27%] h-px w-[26%] bg-gradient-to-r from-transparent via-blue-500/45 to-transparent">
          <span className="absolute left-0 top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-blue-400" />
        </div>
        <div data-signal-line className="signal-track absolute right-[13%] top-[18%] h-px w-[18%] bg-gradient-to-r from-transparent via-indigo-500/45 to-transparent">
          <span className="absolute left-0 top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-indigo-400" />
        </div>
        <div data-signal-line className="signal-track absolute right-[10%] bottom-[31%] h-px w-[24%] bg-gradient-to-r from-transparent via-sky-500/45 to-transparent">
          <span className="absolute left-0 top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-sky-400" />
        </div>
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 680 640" fill="none">
          <defs>
            <linearGradient id="hero-path" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(37,99,235,0.06)" />
              <stop offset="50%" stopColor="rgba(79,70,229,0.38)" />
              <stop offset="100%" stopColor="rgba(37,99,235,0.06)" />
            </linearGradient>
          </defs>
          <path d="M150 180C220 140 288 142 346 194" stroke="url(#hero-path)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M520 108C486 132 462 166 430 214" stroke="url(#hero-path)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M536 420C476 402 432 378 384 338" stroke="url(#hero-path)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M128 452C194 418 252 378 302 336" stroke="url(#hero-path)" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </div>

      {[
        'left-[14%] top-[11%]',
        'left-[24%] top-[74%]',
        'right-[18%] top-[18%]',
        'right-[12%] bottom-[18%]',
        'left-[62%] top-[70%]',
        'left-[48%] top-[10%]',
        'right-[28%] top-[62%]',
      ].map((position, index) => (
        <span
          key={position}
          data-particle-dot
          className={cn(
            'floating-orb absolute h-2.5 w-2.5 rounded-full bg-gradient-to-r from-blue-500 to-indigo-500 opacity-70 blur-[0.3px]',
            position,
          )}
          style={{ animationDelay: `${index * 0.3}s` }}
        />
      ))}

      {HERO_FLOATING_CARDS.map((card, index) => {
        const isHovered = hoveredCardIndex === index;
        return (
          <ParallaxLayer
            key={card.title}
            depth={0.65 + index * 0.07}
            x={mouseX}
            y={mouseY}
            data-float-layer
            className={cn(
              'absolute',
              card.className,
              index < 2 ? 'hidden lg:block' : 'hidden 2xl:block',
            )}
            style={{ zIndex: isHovered ? 12 : 2 }}
          >
            {/* Stationary transparent hit area to capture and maintain stable hovers without jitter */}
            <div
              onMouseEnter={() => setHoveredCardIndex(index)}
              onMouseLeave={() => setHoveredCardIndex(null)}
              className="absolute inset-[-14px] z-10 cursor-pointer bg-transparent"
            />
            {/* Visual presentation layer with pointer-events disabled to prevent element-shifting collision */}
            <motion.div
              animate={{
                scale: isHovered ? 1.12 : 1,
                y: isHovered ? -8 : 0,
              }}
              transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1] }}
              className={cn(
                "pointer-events-none relative z-0 glass-panel rounded-[24px] border p-4 shadow-[0_26px_70px_-46px_rgba(15,23,42,0.24)] transition-all duration-300",
                isHovered
                  ? "border-blue-500/36 shadow-[0_34px_80px_-40px_rgba(15,23,42,0.38)] bg-white/95"
                  : "border-white/80 bg-white/85"
              )}
            >
              <div className="text-[0.68rem] font-semibold uppercase tracking-[0.28em] text-slate-400">{card.title}</div>
              <p className="mt-2 text-sm leading-6 text-slate-700">{card.body}</p>
              <div className="mt-3 inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-[0.72rem] font-semibold text-blue-700">
                {card.badge}
              </div>
            </motion.div>
          </ParallaxLayer>
        );
      })}

      <ParallaxLayer
        depth={0.22}
        x={mouseX}
        y={mouseY}
        data-float-layer
        className="absolute inset-x-4 bottom-6 top-20 z-[3] sm:inset-x-6 sm:bottom-8 sm:top-16 lg:inset-x-8"
      >
        <div className="glass-panel soft-ring flex h-full flex-col overflow-hidden rounded-[34px] border border-white/80 p-5 shadow-[0_28px_80px_-56px_rgba(15,23,42,0.28)] sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.34em] text-slate-400">
                Autonomous CRM Core
              </p>
              <h3 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-slate-950 sm:text-2xl">
                Live AI orchestration
              </h3>
            </div>
            <div className="inline-flex items-center rounded-full bg-gradient-to-r from-blue-500/10 to-indigo-500/10 px-3 py-1 text-xs font-semibold text-blue-700">
              12 agents active
            </div>
          </div>

          <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
              ['Lead score', '91 / 100'],
              ['Next action', 'Review pricing'],
              ['Response SLA', '19 seconds'],
            ].map(([label, value]) => (
              <div key={label} className="rounded-[22px] border border-white/70 bg-white/76 px-4 py-3 shadow-[0_20px_60px_-44px_rgba(15,23,42,0.35)]">
                <div className="text-[0.68rem] uppercase tracking-[0.26em] text-slate-400">{label}</div>
                <div className="mt-2 text-sm font-semibold text-slate-950 sm:text-base">{value}</div>
              </div>
            ))}
          </div>

          <div className="mt-5 grid flex-1 gap-4 lg:grid-cols-[1.08fr_0.92fr]">
            <div className="rounded-[28px] border border-white/70 bg-white/82 p-4 shadow-[0_26px_80px_-52px_rgba(15,23,42,0.38)]">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-slate-950">Sarah Malik</div>
                  <div className="mt-1 text-xs text-slate-500">Expansion opportunity · 5 channels connected</div>
                </div>
                <div className="inline-flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1 text-[0.72rem] font-semibold text-emerald-700">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  Live
                </div>
              </div>

              <div className="mt-4 flex items-center gap-2">
                {[
                  NavBrandIconWhatsApp,
                  NavBrandIconInstagram,
                  NavBrandIconFacebook,
                  NavBrandIconEmail,
                ].map((IconComponent, index) => (
                  <span key={index} className="flex h-9 w-9 items-center justify-center rounded-2xl border border-white/70 bg-slate-50/90">
                    <IconComponent />
                  </span>
                ))}
                <span className="flex h-9 items-center rounded-2xl border border-white/70 bg-slate-50/90 px-3 text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-500">
                  Web Chat
                </span>
              </div>

              <div className="mt-5 space-y-3">
                <div className="max-w-[78%] rounded-[22px] rounded-bl-md bg-slate-100 px-4 py-3 text-sm leading-6 text-slate-700">
                  We need rollout pricing for three reps and AI follow-up for WhatsApp.
                </div>
                <div className="ml-auto max-w-[82%] rounded-[22px] rounded-br-md bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-3 text-sm leading-6 text-white shadow-[0_18px_44px_-26px_rgba(37,99,235,0.75)]">
                  I can help with that. I have already grouped your last Instagram and email messages into this same opportunity and prepared a pricing brief for the rep.
                </div>
              </div>

              <div className="mt-4 flex items-center gap-3 rounded-[22px] border border-blue-500/10 bg-blue-50/70 px-4 py-3 text-sm text-blue-800">
                <Bot size={16} />
                AI summary ready for handoff
              </div>
            </div>

            <div className="flex flex-col gap-4">
              <div className="glass-deep rounded-[28px] p-4 text-white shadow-[0_34px_90px_-56px_rgba(15,23,42,0.8)]">
                <div className="flex items-center justify-between">
                  <div className="text-[0.68rem] font-semibold uppercase tracking-[0.28em] text-white/55">Conversion probability</div>
                  <TrendingUp size={16} className="text-sky-300" />
                </div>
                <div className="mt-4 flex items-end justify-between gap-4">
                  <div>
                    <div className="text-4xl font-semibold tracking-[-0.05em]">82%</div>
                    <div className="mt-2 text-sm text-white/65">Up after AI qualification and pricing intent detection.</div>
                  </div>
                  <div className="grid w-24 grid-cols-4 items-end gap-2">
                    {[42, 58, 72, 92].map((height) => (
                      <span
                        key={height}
                        className="rounded-t-full bg-gradient-to-t from-sky-400 to-blue-200"
                        style={{ height }}
                      />
                    ))}
                  </div>
                </div>
              </div>

              <div className="glass-panel rounded-[28px] border border-white/70 p-4">
                <div className="text-[0.68rem] font-semibold uppercase tracking-[0.28em] text-slate-400">
                  AI workflow decisions
                </div>
                <div className="mt-4 space-y-3">
                  {[
                    'Merged five customer identifiers into one profile',
                    'Detected pricing intent and urgency from WhatsApp',
                    'Drafted handoff note with objections and plan fit',
                  ].map((item) => (
                    <div key={item} className="flex items-start gap-3 rounded-[20px] border border-slate-100 bg-white/88 px-4 py-3 text-sm text-slate-700">
                      <span className="mt-1 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-blue-500/10">
                        <Check size={12} color="#2563eb" />
                      </span>
                      {item}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
['Identity graph', '5 profiles stitched'],
              ['AI follow-up', '2 scheduled nudges'],
              ['Forecast impact', '+14% close rate'],
            ].map(([label, value]) => (
              <div key={label} className="rounded-[22px] border border-white/70 bg-white/76 px-4 py-3">
                <div className="text-[0.68rem] uppercase tracking-[0.26em] text-slate-400">{label}</div>
                <div className="mt-2 text-sm font-semibold text-slate-950">{value}</div>
              </div>
            ))}
          </div>
        </div>
      </ParallaxLayer>
    </div>
  );
}

// 7. GLOBE SECTION
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
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
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
      if (!ctx) { raf.current = requestAnimationFrame(frame); return; }
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
    <div ref={wrapRef} style={{ position: 'relative', maxWidth: 700, margin: '0 auto', cursor: 'grab' }}>
      <canvas ref={canvasRef} style={{ width: '100%', display: 'block' }} aria-label="Interactive globe showing AI integrations" />
      {/* soft vignette overlay */}
      <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at center, transparent 50%, #020617 90%)', pointerEvents: 'none', zIndex: 1 }} aria-hidden="true" />
      {positions.map(tag => (
        <button key={tag.id}
          className="globe-tag"
          onClick={e => { e.stopPropagation(); setActive(prev => prev?.id === tag.id ? null : tag); }}
          aria-pressed={active?.id === tag.id}
          aria-label={`${tag.label}: ${tag.desc}`}
          style={{
            position: 'absolute',
            left: tag.px,
            top: tag.py,
            transform: `translate(-50%,-50%) scale(${tag.scale})`,
            opacity: tag.opacity,
            background: active?.id === tag.id ? tag.color : 'rgba(12,19,36,0.75)',
            border: `1px solid ${tag.color}66`,
            borderRadius: 100,
            padding: '5px 13px',
            fontSize: '11.5px',
            fontWeight: 600,
            color: active?.id === tag.id ? '#fff' : tag.color,
            cursor: 'pointer',
            fontFamily: 'var(--font-b)',
            whiteSpace: 'nowrap',
            backdropFilter: 'blur(8px)',
            WebkitBackdropFilter: 'blur(8px)',
            zIndex: Math.round(tag.depth * 20) + 2,
            transition: 'background .2s, color .2s, box-shadow .2s',
            boxShadow: active?.id === tag.id ? `0 0 20px ${tag.color}66` : 'none'
          }}>
          {tag.label}
        </button>
      ))}
      {active && (() => {
        const liveTag = positions.find(t => t.id === active.id) || active;
        return (
          <div style={{
            position: 'absolute',
            ...getTooltipStyle(liveTag),
            transform: 'translate(-50%,-50%)',
            background: 'rgba(12,19,36,0.95)',
            border: `1px solid ${liveTag.color}55`,
            borderRadius: 16,
            padding: '16px 20px',
            textAlign: 'center',
            zIndex: 100,
            boxShadow: `0 20px 50px rgba(0,0,0,.5), 0 0 30px ${liveTag.color}22`,
            minWidth: 190,
            maxWidth: 240,
            animation: 'slideUp .2s ease',
            backdropFilter: 'blur(16px)'
          }}>
            <button onClick={() => setActive(null)} aria-label="Close tooltip" style={{ position: 'absolute', top: 8, right: 10, background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: 'var(--fg3)', lineHeight: 1, padding: 2 }}>✕</button>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: liveTag.color, margin: '0 auto 10px', boxShadow: `0 0 12px ${liveTag.color}` }} aria-hidden="true" />
            <div style={{ fontSize: 15, fontWeight: 700, color: liveTag.color, marginBottom: 8 }}>{liveTag.label}</div>
            <div style={{ fontSize: 12.5, color: '#94a3b8', lineHeight: 1.55 }}>{liveTag.desc}</div>
          </div>
        );
      })()}
    </div>
  );
}

export default function LandingPage() {
  const pageRef = useRef(null);
  const lenisRef = useRef(null);

  const [scrolled, setScrolled] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const reducedMotion = useReducedMotionPreference();
  const currentHeadline = useRotatingHeadline(HERO_HEADLINES, 3000);
  const pricingPlans = useMemo(
    () =>
      getLandingPricingPlans().map((plan) => ({
        ...plan,
        description: cleanCopy(plan.description),
        features: plan.features.map(cleanCopy),
      })),
    [],
  );

  const heroMouseX = useMotionValue(0);
  const heroMouseY = useMotionValue(0);
  const { scrollYProgress } = useScroll();
  const progressScale = useSpring(scrollYProgress, { stiffness: 120, damping: 28, restDelta: 0.001 });

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 18);

    onScroll();

    window.addEventListener('scroll', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll);
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    const lenis = new Lenis({
      duration: 1.15,
      lerp: 0.08,
      smoothWheel: true,
      syncTouch: false,
    });

    lenisRef.current = lenis;

    let frameId = null;
    const raf = (time) => {
      lenis.raf(time);
      frameId = window.requestAnimationFrame(raf);
    };

    frameId = window.requestAnimationFrame(raf);

    return () => {
      if (frameId) window.cancelAnimationFrame(frameId);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (reducedMotion || !pageRef.current) return undefined;

    const context = gsap.context(() => {
      gsap.to('[data-float-layer]', {
        y: (index) => (index % 2 === 0 ? -8 : 10),
        x: (index) => (index % 3 === 0 ? 6 : -5),
        duration: (index) => 7.2 + index * 0.7,
        repeat: -1,
        yoyo: true,
        ease: 'sine.inOut',
        stagger: 0.2,
      });

      gsap.to('[data-particle-dot]', {
        y: (index) => (index % 2 === 0 ? -10 : 9),
        x: (index) => (index % 3 === 0 ? 7 : -6),
        duration: (index) => 6.4 + index * 0.35,
        repeat: -1,
        yoyo: true,
        ease: 'sine.inOut',
        stagger: 0.1,
      });

      gsap.fromTo('[data-signal-line] span', { x: 0, opacity: 0.45 }, {
        x: 118,
        opacity: 0.9,
        duration: 2.8,
        ease: 'none',
        repeat: -1,
        stagger: 0.24,
      });
    }, pageRef);

    return () => context.revert();
  }, [reducedMotion]);

  const handleAnchorClick = (event, href) => {
    event.preventDefault();
    setMobileNavOpen(false);

    const target = document.querySelector(href);
    if (!target) return;

    if (lenisRef.current) {
      lenisRef.current.scrollTo(target, { offset: -92 });
      return;
    }

    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleHeroMouseMove = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const normalizedX = (event.clientX - rect.left) / rect.width - 0.5;
    const normalizedY = (event.clientY - rect.top) / rect.height - 0.5;

    heroMouseX.set(normalizedX * 34);
    heroMouseY.set(normalizedY * 30);
  };

  const handleHeroMouseLeave = () => {
    heroMouseX.set(0);
    heroMouseY.set(0);
  };

  return (
    <div ref={pageRef} className="landing-shell relative overflow-x-hidden bg-white text-slate-950">
      <style>{`
        .landing-shell {
          background:
            radial-gradient(circle at 12% 8%, rgba(59, 130, 246, 0.12), transparent 24%),
            radial-gradient(circle at 86% 12%, rgba(99, 102, 241, 0.14), transparent 28%),
            linear-gradient(180deg, #f7fbff 0%, #ffffff 16%, #ffffff 100%);
        }
        .hero-visual-wrapper {
          width: 680px;
          flex-shrink: 0;
          transform-origin: center;
          transition: all 0.3s ease;
          transform: scale(0.44);
          margin-top: -190px;
          margin-bottom: -190px;
        }
        @media (min-width: 380px) {
          .hero-visual-wrapper {
            transform: scale(0.53);
            margin-top: -150px;
            margin-bottom: -150px;
          }
        }
        @media (min-width: 480px) {
          .hero-visual-wrapper {
            transform: scale(0.66);
            margin-top: -100px;
            margin-bottom: -100px;
          }
        }
        @media (min-width: 640px) {
          .hero-visual-wrapper {
            transform: scale(0.85);
            margin-top: -40px;
            margin-bottom: -40px;
          }
        }
        @media (min-width: 768px) {
          .hero-visual-wrapper {
            transform: scale(1);
            margin-top: 0;
            margin-bottom: 0;
          }
        }
        .landing-shell::before {
          content: '';
          position: fixed;
          inset: 0;
          pointer-events: none;
          background:
            linear-gradient(rgba(15, 23, 42, 0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(15, 23, 42, 0.02) 1px, transparent 1px);
          background-size: 92px 92px;
          mask-image: radial-gradient(circle at center, black 36%, transparent 92%);
          opacity: 0.42;
        }
        .glass-panel {
          background: rgba(255, 255, 255, 0.88);
          backdrop-filter: blur(18px);
        }
        .glass-deep {
          background:
            radial-gradient(circle at top, rgba(37, 99, 235, 0.22), transparent 34%),
            linear-gradient(180deg, rgba(15, 23, 42, 0.94), rgba(15, 23, 42, 0.84));
          backdrop-filter: blur(22px);
        }
        .soft-ring {
          box-shadow:
            0 0 0 1px rgba(255, 255, 255, 0.86),
            0 28px 90px -64px rgba(37, 99, 235, 0.5);
        }
        .landing-mesh {
          animation: meshShift 14s ease-in-out infinite alternate;
        }
        .floating-orb {
          animation: floatY 6s ease-in-out infinite;
        }
        .signal-track {
          overflow: hidden;
        }
        .orbit-spin-slow {
          animation: orbitClockwise 42s linear infinite;
        }
        .orbit-spin-medium {
          animation: orbitClockwise 34s linear infinite;
        }
        .orbit-spin-fast {
          animation: orbitClockwise 28s linear infinite;
        }
        .orbit-reverse-slow {
          animation: orbitCounter 42s linear infinite;
        }
        .orbit-reverse-medium {
          animation: orbitCounter 34s linear infinite;
        }
        .orbit-reverse-fast {
          animation: orbitCounter 28s linear infinite;
        }
        .typing-dot {
          animation: typingPulse 1.2s ease-in-out infinite;
        }
        .typing-dot:nth-child(2) {
          animation-delay: 0.16s;
        }
        .typing-dot:nth-child(3) {
          animation-delay: 0.32s;
        }
        .workflow-beam {
          animation: signalTravel 2.9s linear infinite;
        }
        .chart-glow {
          animation: shimmer 4.5s linear infinite;
          background-size: 200% 100%;
        }
        @keyframes meshShift {
          0% {
            transform: translate3d(0, 0, 0) scale(1);
          }
          100% {
            transform: translate3d(0, -18px, 0) scale(1.03);
          }
        }
        @keyframes floatY {
          0%, 100% {
            transform: translate3d(0, 0, 0);
          }
          50% {
            transform: translate3d(0, -12px, 0);
          }
        }
        @keyframes orbitClockwise {
          from {
            transform: translate(-50%, -50%) rotate(0deg);
          }
          to {
            transform: translate(-50%, -50%) rotate(360deg);
          }
        }
        @keyframes orbitCounter {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(-360deg);
          }
        }
        @keyframes typingPulse {
          0%, 100% {
            opacity: 0.28;
            transform: translateY(0);
          }
          50% {
            opacity: 1;
            transform: translateY(-3px);
          }
        }
        @keyframes signalTravel {
          0% {
            transform: translateX(0);
            opacity: 0;
          }
          20% {
            opacity: 1;
          }
          100% {
            transform: translateX(860%);
            opacity: 0;
          }
        }
        @keyframes shimmer {
          0% {
            background-position: 200% 0;
          }
          100% {
            background-position: -200% 0;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .landing-mesh,
          .floating-orb,
          .orbit-spin-slow,
          .orbit-spin-medium,
          .orbit-spin-fast,
          .orbit-reverse-slow,
          .orbit-reverse-medium,
          .orbit-reverse-fast,
          .typing-dot,
          .workflow-beam,
          .chart-glow {
            animation: none !important;
          }
        }
      `}</style>

      <motion.div
        className="fixed inset-x-0 top-0 z-[70] h-[2px] origin-left bg-gradient-to-r from-blue-500 via-indigo-500 to-sky-400"
        style={{ scaleX: progressScale }}
      />

      <header
        className={cn(
          'fixed inset-x-0 top-0 z-[60] transition-all duration-300',
          scrolled
            ? 'border-b border-white/70 bg-white/76 shadow-[0_20px_60px_-46px_rgba(15,23,42,0.35)] backdrop-blur-2xl'
            : 'border-b border-transparent bg-transparent',
        )}
      >
        <div className="mx-auto flex h-20 max-w-[1280px] items-center justify-between px-5 sm:px-6 lg:px-8">
          <PlatformLogo fontWeight={800} />

          <nav className="hidden items-center gap-8 lg:flex">
            <NavDropdown label="Features" items={FEATURES_DROPDOWN} onAnchorClick={handleAnchorClick} />
            <NavDropdown label="Channels" items={CHANNELS_DROPDOWN} onAnchorClick={handleAnchorClick} />
            <NavLink href="#pricing" onAnchorClick={handleAnchorClick}>Pricing</NavLink>
            <NavDropdown label="Resources" items={RESOURCES_DROPDOWN} onAnchorClick={handleAnchorClick} />
          </nav>

          <div className="hidden items-center gap-3 lg:flex">
            <Link
              to="/signin"
              className="rounded-2xl px-4 py-2.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950"
            >
              Sign In
            </Link>
            <MagneticAction to="/signup" className="px-5">
              Start Free
              <ArrowRight size={16} />
            </MagneticAction>
          </div>

          <div className="flex items-center gap-2 lg:hidden">
            <Link
              to="/signup"
              className="rounded-2xl bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-[0_18px_44px_-26px_rgba(37,99,235,0.75)]"
            >
              Start Free
            </Link>
            <button
              type="button"
              onClick={() => setMobileNavOpen((current) => !current)}
              className="flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200/80 bg-white/80 text-slate-700 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.25)] backdrop-blur-xl"
            >
              {mobileNavOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>
      </header>

      <AnimatePresence>
        {mobileNavOpen && (
          <motion.div
            initial={{ opacity: 0, y: -16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="fixed inset-x-4 top-24 z-[85] rounded-[30px] border border-slate-200/90 bg-white p-5 shadow-[0_34px_90px_-42px_rgba(15,23,42,0.22)] lg:hidden"
          >
            <div className="grid gap-4">
              {[
                ['Features', FEATURES_DROPDOWN],
                ['Channels', CHANNELS_DROPDOWN],
                ['Resources', RESOURCES_DROPDOWN],
              ].map(([title, items]) => (
                <div key={title}>
                  <div className="text-[0.7rem] font-semibold uppercase tracking-[0.3em] text-slate-400">{title}</div>
                  <div className="mt-3 grid gap-2">
                    {items.map((item) => (
                      <SmartLink
                        key={item.label}
                        href={item.href}
                        onAnchorClick={handleAnchorClick}
                        onClick={() => setMobileNavOpen(false)}
                        className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-slate-50/80 px-4 py-3"
                      >
                        <span className="flex h-10 w-10 items-center justify-center rounded-2xl" style={{ background: item.color }}>
                          {item.icon}
                        </span>
                        <span>
                          <span className="block text-sm font-semibold text-slate-950">{item.label}</span>
                          <span className="block text-xs text-slate-500">{item.desc}</span>
                        </span>
                      </SmartLink>
                    ))}
                  </div>
                </div>
              ))}

              <div className="grid gap-3 pt-2">
                <SmartLink
                  href="#pricing"
                  onAnchorClick={handleAnchorClick}
                  onClick={() => setMobileNavOpen(false)}
                  className="rounded-2xl border border-slate-100 bg-slate-50/80 px-4 py-3 text-sm font-semibold text-slate-950"
                >
                  Pricing
                </SmartLink>
                <Link
                  to="/signin"
                  onClick={() => setMobileNavOpen(false)}
                  className="rounded-2xl border border-slate-100 bg-slate-50/80 px-4 py-3 text-sm font-semibold text-slate-950"
                >
                  Sign In
                </Link>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <main>
        <section className="relative isolate min-h-screen overflow-hidden px-5 pb-16 pt-32 sm:px-6 lg:px-8 lg:pb-24 lg:pt-36">
          <div className="absolute inset-0 -z-10 overflow-hidden">
            <div className="landing-mesh absolute left-[-8%] top-[8%] h-[420px] w-[420px] rounded-full bg-[radial-gradient(circle,rgba(59,130,246,0.16),transparent_62%)] blur-3xl" />
            <div className="landing-mesh absolute right-[-4%] top-[2%] h-[460px] w-[460px] rounded-full bg-[radial-gradient(circle,rgba(99,102,241,0.18),transparent_62%)] blur-3xl" />
            <div className="landing-mesh absolute bottom-[-16%] left-[34%] h-[360px] w-[360px] rounded-full bg-[radial-gradient(circle,rgba(14,165,233,0.12),transparent_62%)] blur-3xl" />
          </div>

          <div className="mx-auto grid max-w-[1280px] items-center gap-16 xl:grid-cols-[0.94fr_1.06fr] xl:gap-10">
            <motion.div
              initial={{ opacity: 0, y: 28 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...revealTransition, duration: 0.95 }}
              className="relative z-[10] max-w-[620px]"
            >
              <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/12 bg-white/86 px-4 py-2 text-[0.72rem] font-semibold uppercase tracking-[0.32em] text-blue-700 shadow-[0_20px_60px_-46px_rgba(37,99,235,0.6)] backdrop-blur-xl">
                <span className="h-2 w-2 rounded-full bg-gradient-to-r from-blue-500 to-indigo-500" />
                AI revenue operating system
              </div>

              <div className="mt-8 min-h-[11.5rem] lg:min-h-[13rem]">
                <AnimatePresence mode="wait">
                  <motion.span
                    key={currentHeadline}
                    initial={{ opacity: 0, y: 18, filter: 'blur(10px)' }}
                    animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                    exit={{ opacity: 0, y: -18, filter: 'blur(10px)' }}
                    transition={{ duration: 0.45, ease: 'easeOut' }}
                    className="block text-balance text-5xl font-semibold tracking-[-0.065em] text-transparent sm:text-6xl lg:text-[5.4rem] lg:leading-[0.95] bg-gradient-to-r from-blue-600 via-blue-500 to-indigo-600 bg-clip-text"
                  >
                    {currentHeadline}
                  </motion.span>
                </AnimatePresence>
                <h1 className="mt-3 text-balance text-4xl font-semibold tracking-[-0.06em] text-slate-950 sm:text-5xl lg:text-[4.2rem] lg:leading-[0.96]">
                  for omnichannel customer intelligence and autonomous growth.
                </h1>
              </div>

              <p className="mt-8 max-w-[620px] text-pretty text-base leading-8 text-slate-600 sm:text-lg">
                Pulse Engine unifies WhatsApp, Instagram, Facebook, Email, and Web Chat into one AI-native CRM that scores leads, nurtures intent, routes work, and keeps every team moving with live customer context.
              </p>

              <div className="mt-10 flex flex-wrap gap-4">
                <MagneticAction to="/signup" className="px-12 py-4 text-base w-full sm:w-64 justify-center">
                  Start Free
                  <ArrowRight size={16} />
                </MagneticAction>
              </div>

              <div className="mt-10 flex flex-wrap gap-3 text-sm text-slate-500">
                {[
                  '14-day free trial',
                  'No credit card required',
                  'Enterprise-ready orchestration',
                ].map((item) => (
                  <span
                    key={item}
                    className="inline-flex items-center gap-2 rounded-full border border-white/80 bg-white/78 px-4 py-2 shadow-[0_18px_44px_-34px_rgba(15,23,42,0.26)] backdrop-blur-xl"
                  >
                    <Check size={14} color="#22c55e" />
                    {item}
                  </span>
                ))}
              </div>

              <div className="mt-12 grid gap-3 sm:grid-cols-3">
                {HERO_STATS.map((stat, index) => (
                  <motion.div
                    key={stat.label}
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ ...revealTransition, delay: 0.1 + index * 0.08 }}
                    className="rounded-[24px] border border-white/70 bg-white/80 px-5 py-4 shadow-[0_22px_60px_-44px_rgba(15,23,42,0.25)] backdrop-blur-xl"
                  >
                    <div className="text-[1.75rem] font-semibold tracking-[-0.05em] text-slate-950">
                      <AnimatedCounter value={stat.value} suffix={stat.suffix} decimals={stat.value % 1 !== 0 ? 1 : 0} />
                    </div>
                    <div className="mt-1 text-sm text-slate-500">{stat.label}</div>
                  </motion.div>
                ))}
              </div>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 36 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...revealTransition, duration: 1, delay: 0.08 }}
              className="relative z-[5] w-full flex items-center justify-center overflow-visible"
            >
              <div className="hero-visual-wrapper">
                <HeroEcosystem
                  mouseX={heroMouseX}
                  mouseY={heroMouseY}
                  onMouseMove={handleHeroMouseMove}
                  onMouseLeave={handleHeroMouseLeave}
                />
              </div>
            </motion.div>
          </div>
        </section>

        <section id="ecosystem" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28 bg-[#020617] text-white overflow-hidden">
          <div className="absolute inset-0 -z-10 bg-[#020617]" />
          <div className="mx-auto max-w-[1280px]">
            <SectionHeading
              eyebrow="360 degree AI ecosystem"
              title="A connected operating system for every customer signal, channel, and revenue motion."
              description="Inspired by the clarity of enterprise CRM platforms and rebuilt with AI-native depth, Pulse Engine turns your stack into one living command layer with channels, scoring, forecasting, and orchestration in orbit around a central intelligence core."
              dark={true}
            />

            <div className="mt-16">
              <GlobeSection />
            </div>

            <p style={{ textAlign: 'center', marginTop: 32, fontSize: 12.5, color: '#64748b' }}>
              Drag to rotate · Click any tag to learn more
            </p>
          </div>
        </section>

        <section id="workflow" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="mx-auto max-w-[1280px]">
            <SectionHeading
              eyebrow="AI workflow storytelling"
              title="From first message to closed revenue, the system keeps moving even when your team is not online."
              description="Each step in the funnel is watched, scored, summarized, and advanced automatically. Reps enter the loop only when the conversation needs judgment, not repetition."
            />

            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.16 }}
              transition={revealTransition}
              className="glass-panel mt-16 overflow-hidden rounded-[36px] border border-white/70 p-6 shadow-[0_34px_90px_-54px_rgba(15,23,42,0.32)] backdrop-blur-xl lg:p-10"
            >
              <div className="relative hidden lg:block">
                <div className="absolute left-14 right-14 top-16 h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent" />
                <div className="absolute left-14 top-[3.95rem] h-1.5 w-24 rounded-full bg-gradient-to-r from-blue-500 via-indigo-500 to-sky-400 blur-[0.5px] workflow-beam" />
                <div className="grid grid-cols-6 gap-4">
                  {WORKFLOW_STEPS.map((step, index) => {
                    const IconComponent = step.icon;
                    return (
                      <div key={step.title} className="relative">
                        <div className={cn('rounded-[28px] border border-white/70 bg-gradient-to-br p-5 shadow-[0_24px_70px_-52px_rgba(15,23,42,0.28)]', step.accent)}>
                          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/86 shadow-[0_18px_44px_-32px_rgba(15,23,42,0.24)]">
                            <IconComponent size={20} color="#0f172a" />
                          </div>
                          <div className="mt-5 text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">
                            Step {index + 1}
                          </div>
                          <div className="mt-2 text-lg font-semibold tracking-[-0.03em] text-slate-950">
                            {step.title}
                          </div>
                          <p className="mt-3 text-sm leading-7 text-slate-600">{step.body}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="grid gap-4 lg:hidden">
                {WORKFLOW_STEPS.map((step, index) => {
                  const IconComponent = step.icon;
                  return (
                    <div key={step.title} className={cn('rounded-[28px] border border-white/70 bg-gradient-to-br p-5 shadow-[0_24px_70px_-52px_rgba(15,23,42,0.28)]', step.accent)}>
                      <div className="flex items-center gap-4">
                        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/86">
                          <IconComponent size={20} color="#0f172a" />
                        </div>
                        <div>
                          <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">
                            Step {index + 1}
                          </div>
                          <div className="text-lg font-semibold tracking-[-0.03em] text-slate-950">{step.title}</div>
                        </div>
                      </div>
                      <p className="mt-4 text-sm leading-7 text-slate-600">{step.body}</p>
                    </div>
                  );
                })}
              </div>

              <div className="mt-10 grid gap-6 lg:grid-cols-[1.04fr_0.96fr]">
                <div className="rounded-[30px] border border-white/70 bg-white/88 p-5 shadow-[0_26px_80px_-54px_rgba(15,23,42,0.28)]">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">Conversation arc</div>
                      <div className="mt-2 text-xl font-semibold tracking-[-0.03em] text-slate-950">Lead enters, AI carries momentum</div>
                    </div>
                    <div className="inline-flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-700">
                      <span className="h-2 w-2 rounded-full bg-emerald-500" />
                      Automation live
                    </div>
                  </div>

                  <div className="mt-6 space-y-4">
                    <div className="max-w-[78%] rounded-[24px] rounded-bl-md border border-slate-100 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
                      Hi, we are comparing CRM options and need WhatsApp automation plus lead scoring this quarter.
                    </div>
                    <div className="ml-auto max-w-[86%] rounded-[24px] rounded-br-md bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-3 text-sm leading-6 text-white shadow-[0_18px_44px_-26px_rgba(37,99,235,0.75)]">
                      I have identified you as a mid-market buying team with rollout urgency. I can outline pricing, implementation flow, and next steps now.
                    </div>
                    <div className="flex max-w-[84%] items-start gap-3 rounded-[24px] border border-blue-500/10 bg-blue-50/70 px-4 py-3 text-sm text-blue-900">
                      <Bot size={16} className="mt-1 flex-shrink-0" />
                      <div>
                        <div className="font-semibold">AI brief for rep</div>
                        <div className="mt-1 leading-6 text-blue-800/80">
                          Intent: pricing review. Team size: 3 seats. Channel history merged from email and Instagram. Recommended next step: schedule demo and share enterprise workflow card.
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="grid gap-5">
                  <div className="rounded-[30px] border border-white/70 bg-gradient-to-br from-slate-950 to-slate-900 p-5 text-white shadow-[0_34px_90px_-54px_rgba(15,23,42,0.7)]">
                    <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-white/50">Live scoring panel</div>
                    <div className="mt-5 grid gap-4 sm:grid-cols-3">
                      {[
                        ['Budget', 'High'],
                        ['Urgency', 'This month'],
                        ['Fit', 'Enterprise'],
                      ].map(([label, value]) => (
                        <div key={label} className="rounded-[20px] border border-white/10 bg-white/5 px-4 py-4">
                          <div className="text-[0.68rem] uppercase tracking-[0.24em] text-white/45">{label}</div>
                          <div className="mt-2 text-sm font-semibold text-white">{value}</div>
                        </div>
                      ))}
                    </div>
                    <div className="mt-5 h-2 rounded-full bg-white/10">
                      <div className="chart-glow h-full w-[82%] rounded-full bg-[linear-gradient(90deg,#60a5fa,#818cf8,#38bdf8)]" />
                    </div>
                    <div className="mt-3 text-sm text-white/65">82% conversion probability after automated qualification.</div>
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    {[
                      {
                        title: 'Typing for follow-up',
                        text: 'AI is preparing a personalized WhatsApp follow-up.',
                      },
                      {
                        title: 'Revenue forecast',
                        text: 'Forecast updated after objection handling and plan fit.',
                      },
                    ].map((card) => (
                      <div key={card.title} className="rounded-[28px] border border-white/70 bg-white/88 p-5 shadow-[0_24px_70px_-52px_rgba(15,23,42,0.26)]">
                        <div className="text-sm font-semibold text-slate-950">{card.title}</div>
                        <div className="mt-3 text-sm leading-7 text-slate-600">{card.text}</div>
                        {card.title === 'Typing for follow-up' && (
                          <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2">
                            {[0, 1, 2].map((dot) => (
                              <span key={dot} className="typing-dot h-2.5 w-2.5 rounded-full bg-blue-500" />
                            ))}
                          </div>
                        )}
                        {card.title === 'Revenue forecast' && (
                          <div className="mt-4 inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700">
                            +$18.4k this week
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </motion.div>
          </div>
        </section>

        <section id="dashboard" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="mx-auto grid max-w-[1280px] gap-14 lg:grid-cols-[0.88fr_1.12fr] lg:items-center">
            <div>
              <SectionHeading
                eyebrow="Cinematic dashboard showcase"
                title="A dashboard that feels operational, not ornamental."
                description="Instead of static screenshots, Pulse Engine presents a live command surface with queue pressure, AI insights, handoff readiness, and pipeline movement all visible in a single cinematic workspace."
                align="left"
              />

              <div className="mt-10 grid gap-4">
                {[
                  'Layered glass cards keep the page premium without breaking enterprise clarity.',
                  'Notifications, charts, and opportunity signals reveal in controlled motion, not noisy animation.',
                  'Every visual reinforces one message: this CRM is actively thinking for the team.',
                ].map((item) => (
                  <div key={item} className="flex items-start gap-3 rounded-[24px] border border-white/70 bg-white/80 px-5 py-4 shadow-[0_22px_60px_-48px_rgba(15,23,42,0.24)]">
                    <span className="mt-1 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-blue-500/10">
                      <Check size={12} color="#2563eb" />
                    </span>
                    <span className="text-sm leading-7 text-slate-600">{item}</span>
                  </div>
                ))}
              </div>
            </div>

            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.16 }}
              transition={revealTransition}
              className="relative"
            >
              <div className="absolute -left-6 top-10 h-44 w-44 rounded-full bg-blue-500/12 blur-3xl" />
              <div className="absolute right-4 top-0 h-52 w-52 rounded-full bg-indigo-500/14 blur-3xl" />

              <div className="glass-panel relative overflow-hidden rounded-[38px] border border-white/80 p-5 shadow-[0_34px_90px_-48px_rgba(15,23,42,0.28)] sm:p-6">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <div className="text-[0.72rem] font-semibold uppercase tracking-[0.3em] text-slate-400">Revenue command center</div>
                    <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-slate-950">Operational overview</div>
                  </div>
                  <div className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                    <Clock size={14} />
                    Refreshing every 30s
                  </div>
                </div>

                <div className="mt-6 grid gap-4 sm:grid-cols-3">
                  {[
                    ['Open conversations', '128'],
                    ['High-intent leads', '36'],
                    ['AI handoffs today', '14'],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-[24px] border border-white/70 bg-white/84 px-4 py-4">
                      <div className="text-[0.68rem] uppercase tracking-[0.24em] text-slate-400">{label}</div>
                      <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-slate-950">{value}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-6 grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
                  <div className="rounded-[30px] border border-white/70 bg-white/84 p-5 shadow-[0_26px_80px_-56px_rgba(15,23,42,0.24)]">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <div className="text-sm font-semibold text-slate-950">Pipeline acceleration</div>
                        <div className="mt-1 text-xs text-slate-500">AI-assisted revenue influence across the week</div>
                      </div>
                      <TrendingUp size={18} color="#2563eb" />
                    </div>

                    <div className="mt-6 flex h-56 items-end gap-3">
                      {[48, 74, 68, 102, 126, 118, 144].map((height, index) => (
                        <motion.div
                          key={height}
                          initial={{ height: 18, opacity: 0 }}
                          whileInView={{ height, opacity: 1 }}
                          viewport={{ once: true }}
                          transition={{ duration: 0.6, delay: 0.08 * index }}
                          className="flex-1 rounded-t-[18px] bg-gradient-to-t from-blue-600 via-blue-500 to-indigo-400 shadow-[0_18px_44px_-24px_rgba(37,99,235,0.58)]"
                        />
                      ))}
                    </div>

                    <div className="mt-4 grid grid-cols-7 text-center text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">
                      {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => (
                        <span key={day}>{day}</span>
                      ))}
                    </div>
                  </div>

                  <div className="grid gap-4">
                    <div className="glass-deep rounded-[30px] p-5 text-white shadow-[0_34px_90px_-56px_rgba(15,23,42,0.76)]">
                      <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-white/45">AI insight</div>
                      <div className="mt-3 text-lg font-semibold tracking-[-0.03em]">Pipeline risk reduced</div>
                      <p className="mt-3 text-sm leading-7 text-white/70">
                        Pricing objections dropped after AI summarized prior email context before the rep entered.
                      </p>
                      <div className="mt-4 inline-flex rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-sky-200">
                        18% faster progression
                      </div>
                    </div>

                    <div className="rounded-[30px] border border-white/70 bg-white/88 p-5 shadow-[0_26px_80px_-56px_rgba(15,23,42,0.24)]">
                      <div className="text-sm font-semibold text-slate-950">WhatsApp conversation preview</div>
                      <div className="mt-4 space-y-3">
                        <div className="max-w-[82%] rounded-[20px] rounded-bl-md bg-slate-100 px-4 py-3 text-sm text-slate-700">
                          Can your AI qualify and assign leads automatically?
                        </div>
                        <div className="ml-auto max-w-[84%] rounded-[20px] rounded-br-md bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-3 text-sm text-white">
                          Yes. It scores intent, creates the lead, and routes the handoff with a live brief.
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <motion.div
                  data-float-layer
                  className="absolute right-6 top-24 hidden w-56 rounded-[26px] border border-white/80 bg-white p-4 shadow-[0_24px_70px_-48px_rgba(15,23,42,0.24)] 2xl:block"
                >
                  <div className="text-[0.68rem] font-semibold uppercase tracking-[0.28em] text-slate-400">AI notification</div>
                  <div className="mt-2 text-sm font-semibold text-slate-950">Lead moved to sales-ready</div>
                  <div className="mt-2 text-sm leading-6 text-slate-600">
                    Instagram and email history merged. Demo intent confirmed.
                  </div>
                </motion.div>
              </div>
            </motion.div>
          </div>
        </section>

        <section id="channels" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="absolute inset-0 -z-10 bg-[linear-gradient(180deg,rgba(248,250,252,0.88),rgba(255,255,255,0.45))]" />
          <div className="mx-auto max-w-[1280px]">
            <SectionHeading
              eyebrow="Communication hub"
              title="One identity across every channel, with AI replying, categorizing, scoring, and escalating in sync."
              description="The omnichannel layer is not just a shared inbox. It is a living profile engine that understands the same buyer whether they appear in WhatsApp, Instagram, Facebook, Email, or Web Chat."
            />

            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.16 }}
              transition={revealTransition}
              className="glass-panel mt-16 overflow-hidden rounded-[38px] border border-white/70 p-6 shadow-[0_34px_90px_-52px_rgba(15,23,42,0.32)] lg:p-10"
            >
              <div className="grid gap-6 lg:grid-cols-[0.86fr_1.12fr_0.9fr]">
                <div className="grid gap-4">
                  {CHANNEL_STREAMS.map((stream) => {
                    const IconComponent = stream.icon;
                    return (
                      <div
                        key={stream.title}
                        className={cn('rounded-[28px] border border-white/70 bg-white/86 p-5', stream.glow)}
                      >
                        <div className="flex items-center gap-3">
                          <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-50">
                            <IconComponent />
                          </span>
                          <div>
                            <div className="text-sm font-semibold text-slate-950">{stream.title}</div>
                            <div className={cn('mt-1 inline-flex rounded-full px-2.5 py-1 text-[0.68rem] font-semibold', stream.accent)}>
                              {stream.subtitle}
                            </div>
                          </div>
                        </div>
                        <p className="mt-4 text-sm leading-7 text-slate-600">{stream.body}</p>
                      </div>
                    );
                  })}
                </div>

                <div className="relative rounded-[34px] border border-white/70 bg-white/88 p-6 shadow-[0_28px_80px_-54px_rgba(15,23,42,0.28)]">
                  <div className="absolute inset-x-10 top-1/2 hidden -translate-y-1/2 border-t border-dashed border-blue-500/25 lg:block" />
                  <div className="absolute left-1/2 top-14 hidden h-[calc(100%-7rem)] -translate-x-1/2 border-l border-dashed border-blue-500/18 lg:block" />

                  <div className="mx-auto flex h-24 w-24 items-center justify-center rounded-full bg-[radial-gradient(circle,rgba(99,102,241,0.18),rgba(37,99,235,0.08))] shadow-[0_0_0_16px_rgba(37,99,235,0.05),0_0_0_38px_rgba(79,70,229,0.05)]">
                    <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 shadow-[0_24px_60px_-30px_rgba(37,99,235,0.75)]">
                      <Users size={24} color="#fff" />
                    </div>
                  </div>

                  <div className="mt-6 text-center">
                    <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">Unified customer identity</div>
                    <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-slate-950">Sarah Malik</div>
                    <div className="mt-2 text-sm text-slate-500">5 touchpoints merged, 1 live profile, 0 context loss</div>
                  </div>

                  <div className="mt-8 grid gap-4 sm:grid-cols-2">
                    {[
                      ['Lead score', '91 / 100'],
                      ['Channel affinity', 'WhatsApp + Email'],
                      ['Buying stage', 'Vendor evaluation'],
                      ['Last AI action', 'Shared pricing brief'],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-[24px] border border-slate-100 bg-slate-50/80 px-4 py-4">
                        <div className="text-[0.68rem] uppercase tracking-[0.22em] text-slate-400">{label}</div>
                        <div className="mt-2 text-sm font-semibold text-slate-950">{value}</div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="grid gap-4">
                  {[
                    ['AI reply generation', 'Drafted responses adapt tone, channel, and prior objections before sending.'],
                    ['Categorization', 'Intent, urgency, and ownership update continuously as new messages arrive.'],
                    ['Escalation logic', 'When a deal hits pricing, risk, or compliance thresholds, the right human is notified.'],
                  ].map(([title, body]) => (
                    <div key={title} className="rounded-[28px] border border-white/70 bg-white/88 p-5 shadow-[0_24px_70px_-52px_rgba(15,23,42,0.26)]">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-semibold text-slate-950">{title}</div>
                        <span className="flex h-9 w-9 items-center justify-center rounded-2xl bg-blue-500/10">
                          <Bot size={17} color="#2563eb" />
                        </span>
                      </div>
                      <p className="mt-3 text-sm leading-7 text-slate-600">{body}</p>
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          </div>
        </section>

        <section id="analytics" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="mx-auto max-w-[1280px]">
            <SectionHeading
              eyebrow="AI analytics and intelligence"
              title="Signals become prediction, prediction becomes action."
              description="Lead heat, conversion probability, intent, CSAT, response time, and revenue forecasting all move together so teams can act on what the system already knows."
            />

            <div className="mt-16 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {ANALYTICS_METRICS.map((metric, index) => (
                <motion.div
                  key={metric.label}
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, amount: 0.2 }}
                  transition={{ ...revealTransition, delay: index * 0.05 }}
                  className="rounded-[30px] border border-white/70 bg-white/84 p-6 shadow-[0_24px_70px_-50px_rgba(15,23,42,0.24)] backdrop-blur-xl"
                >
                  <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">{metric.label}</div>
                  <div className="mt-4 text-4xl font-semibold tracking-[-0.05em] text-slate-950">
                    <AnimatedCounter
                      value={metric.value}
                      suffix={metric.suffix}
                      decimals={metric.decimals || 0}
                      duration={1800 + index * 120}
                    />
                  </div>
                  <div className="mt-3 text-sm leading-7 text-slate-600">{metric.sub}</div>
                </motion.div>
              ))}
            </div>

            <div className="mt-10 grid gap-6 lg:grid-cols-[1.08fr_0.92fr]">
              <motion.div
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.16 }}
                transition={revealTransition}
                className="glass-panel rounded-[38px] border border-white/70 p-6 shadow-[0_34px_90px_-52px_rgba(15,23,42,0.3)]"
              >
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">Revenue forecasting</div>
                    <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-slate-950">$184k projected pipeline</div>
                  </div>
                  <div className="inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                    +14% from AI follow-up
                  </div>
                </div>

                <div className="mt-8 grid gap-4 sm:grid-cols-3">
                  {[
                    ['High intent', '36 leads'],
                    ['Average heat', '78 / 100'],
                    ['Forecast range', 'Confident'],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-[24px] border border-white/70 bg-white/84 px-4 py-4">
                      <div className="text-[0.68rem] uppercase tracking-[0.24em] text-slate-400">{label}</div>
                      <div className="mt-2 text-sm font-semibold text-slate-950">{value}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-8 overflow-hidden rounded-[30px] border border-slate-100 bg-slate-50/90 px-4 py-6">
                  <svg viewBox="0 0 640 250" className="h-[250px] w-full" fill="none" aria-hidden="true">
                    <defs>
                      <linearGradient id="forecast-area" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor="rgba(37,99,235,0.24)" />
                        <stop offset="100%" stopColor="rgba(37,99,235,0)" />
                      </linearGradient>
                      <linearGradient id="forecast-line" x1="0" x2="1" y1="0" y2="0">
                        <stop offset="0%" stopColor="#2563eb" />
                        <stop offset="100%" stopColor="#6366f1" />
                      </linearGradient>
                    </defs>
                    <path d="M24 212L120 176L216 164L312 122L408 110L504 86L616 40V212H24Z" fill="url(#forecast-area)" />
                    <path d="M24 212L120 176L216 164L312 122L408 110L504 86L616 40" stroke="url(#forecast-line)" strokeWidth="6" strokeLinecap="round" />
                    {[24, 120, 216, 312, 408, 504, 616].map((x, index) => (
                      <g key={x}>
                        <circle cx={x} cy={[212, 176, 164, 122, 110, 86, 40][index]} r="8" fill="#ffffff" />
                        <circle cx={x} cy={[212, 176, 164, 122, 110, 86, 40][index]} r="5" fill="#2563eb" />
                      </g>
                    ))}
                  </svg>
                </div>
              </motion.div>

              <div className="grid gap-6">
                {[
                  {
                    title: 'Intent prediction',
                    body: 'AI recognizes when curiosity turns into active vendor evaluation across every channel touchpoint.',
                    pill: 'Buyer intent detected',
                  },
                  {
                    title: 'Lead heat scoring',
                    body: 'Scoring updates every time a new reply, campaign touch, or support signal hits the customer profile.',
                    pill: '91 / 100 heat',
                  },
                  {
                    title: 'Customer satisfaction',
                    body: 'CSAT stays visible next to revenue metrics so automation does not come at the cost of trust.',
                    pill: '4.9 / 5 CSAT',
                  },
                ].map((card) => (
                  <motion.div
                    key={card.title}
                    initial={{ opacity: 0, y: 24 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true, amount: 0.18 }}
                    transition={revealTransition}
                    className="rounded-[30px] border border-white/70 bg-white/84 p-6 shadow-[0_24px_70px_-50px_rgba(15,23,42,0.24)]"
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="text-lg font-semibold tracking-[-0.03em] text-slate-950">{card.title}</div>
                      <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{card.pill}</span>
                    </div>
                    <p className="mt-3 text-sm leading-7 text-slate-600">{card.body}</p>
                  </motion.div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section id="pricing" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="absolute inset-0 -z-10 bg-[linear-gradient(180deg,rgba(248,250,252,0.85),rgba(255,255,255,0.5))]" />
          <div className="mx-auto max-w-[1280px]">
            <SectionHeading
              eyebrow="Pricing"
              title="Pricing that scales from fast-moving teams to fully orchestrated enterprise rollout."
              description="The product story stays consistent across plans. Limits, support depth, and rollout complexity scale with your operating model."
            />

            <div className="mt-16 grid gap-6 lg:grid-cols-3">
              {pricingPlans.map((plan, index) => (
                <motion.div
                  key={plan.code}
                  initial={{ opacity: 0, y: 28 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, amount: 0.18 }}
                  transition={{ ...revealTransition, delay: index * 0.05 }}
                  className={cn(
                    'relative rounded-[34px] border p-7 shadow-[0_34px_90px_-58px_rgba(15,23,42,0.32)] backdrop-blur-xl',
                    plan.popular
                      ? 'border-transparent bg-gradient-to-br from-blue-600 via-blue-600 to-indigo-600 text-white'
                      : 'border-white/70 bg-white/84 text-slate-950',
                  )}
                >
                  {plan.popular && (
                    <div className="absolute left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 rounded-full bg-amber-300 px-4 py-2 text-[0.72rem] font-semibold uppercase tracking-[0.24em] text-amber-900 shadow-[0_18px_44px_-26px_rgba(245,158,11,0.65)]">
                      Most Popular
                    </div>
                  )}

                  <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-current/60">{plan.code}</div>
                  <h3 className="mt-4 text-3xl font-semibold tracking-[-0.05em]">{plan.name}</h3>
                  <p className={cn('mt-3 text-sm leading-7', plan.popular ? 'text-white/72' : 'text-slate-600')}>{plan.description}</p>

                  <div className="mt-8 flex items-end gap-1">
                    <span className="text-5xl font-semibold tracking-[-0.06em]">{plan.price}</span>
                    {plan.period && <span className={cn('pb-1 text-base', plan.popular ? 'text-white/70' : 'text-slate-500')}>{plan.period}</span>}
                  </div>

                  <ul className="mt-8 space-y-3">
                    {plan.features.map((feature) => (
                      <li key={feature} className="flex items-start gap-3">
                        <span className={cn('mt-1 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full', plan.popular ? 'bg-white/10' : 'bg-blue-500/10')}>
                          <Check size={12} color={plan.popular ? '#ffffff' : '#2563eb'} />
                        </span>
                        <span className={cn('text-sm leading-7', plan.popular ? 'text-white/82' : 'text-slate-600')}>{feature}</span>
                      </li>
                    ))}
                  </ul>

                  <div className="mt-8">
                    <MagneticAction
                      to={plan.href.startsWith('/') ? plan.href : undefined}
                      href={plan.href.startsWith('#') ? plan.href : undefined}
                      variant={plan.popular ? 'secondary' : plan.code === 'custom' ? 'secondary' : 'primary'}
                      className={cn(
                        'w-full justify-center',
                        plan.popular && '!bg-white !text-blue-700',
                        plan.code === 'custom' && !plan.popular && '!border-slate-200 !bg-white !text-slate-950',
                      )}
                    >
                      {plan.cta}
                    </MagneticAction>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        <section id="testimonials" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="mx-auto max-w-[1280px]">
            <SectionHeading
              eyebrow="Customer proof"
              title="Trusted by teams that want AI to reduce operational drag, not increase it."
              description="The best feedback was not just about speed. It was about clarity, calm, and the sense that every customer interaction was finally connected."
            />

            <div className="mt-16 grid gap-6 lg:grid-cols-[1.16fr_0.92fr_0.92fr]">
              {TESTIMONIALS.map((item, index) => (
                <motion.div
                  key={item.name}
                  data-testimonial-card
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, amount: 0.18 }}
                  transition={{ ...revealTransition, delay: index * 0.05 }}
                  className="rounded-[34px] border border-white/70 bg-white/84 p-6 shadow-[0_28px_80px_-50px_rgba(15,23,42,0.26)] backdrop-blur-xl"
                >
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <div className="text-xl font-semibold tracking-[-0.03em] text-slate-950">{item.company}</div>
                      <div className="mt-1 text-sm text-slate-500">{item.role}</div>
                    </div>
                    <div className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{item.metric}</div>
                  </div>

                  <p className="mt-6 text-base leading-8 text-slate-700">"{item.quote}"</p>

                  <div className="mt-8 flex items-center gap-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 text-sm font-semibold text-white">
                      {item.name.split(' ').map((part) => part[0]).join('')}
                    </div>
                    <div>
                      <div className="text-sm font-semibold text-slate-950">{item.name}</div>
                      <div className="mt-1 flex items-center gap-1 text-amber-500">
                        {[0, 1, 2, 3, 4].map((star) => (
                          <Star key={star} size={14} fill="currentColor" />
                        ))}
                      </div>
                    </div>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        <section id="faq" className="relative px-5 py-24 sm:px-6 lg:px-8 lg:py-28">
          <div className="absolute inset-0 -z-10 bg-[linear-gradient(180deg,rgba(248,250,252,0.88),rgba(255,255,255,0.55))]" />
          <div className="mx-auto max-w-[920px]">
            <SectionHeading
              eyebrow="FAQ"
              title="Everything teams ask before they put AI in front of pipeline."
              description="Operational trust matters. These are the questions we hear most often from teams evaluating a serious AI-native CRM."
            />

            <div className="mt-14 space-y-4">
              {FAQS.map((item, index) => (
                <FaqItem key={item.q} q={item.q} a={item.a} defaultOpen={index === 0} />
              ))}
            </div>
          </div>
        </section>

        <section className="relative overflow-hidden px-5 pb-24 pt-8 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-[1240px]">
            <div className="relative overflow-hidden rounded-[40px] bg-gradient-to-br from-slate-950 via-blue-900 to-indigo-700 px-6 py-16 text-white shadow-[0_40px_120px_-56px_rgba(15,23,42,0.72)] sm:px-8 lg:px-12 lg:py-20">
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.2),transparent_26%),radial-gradient(circle_at_bottom_right,rgba(255,255,255,0.16),transparent_28%)]" />
              <div className="absolute -left-16 top-10 h-56 w-56 rounded-full border border-white/10" />
              <div className="absolute right-[-4rem] top-14 h-72 w-72 rounded-full border border-white/10" />
              <div className="absolute bottom-[-5rem] left-[38%] h-64 w-64 rounded-full border border-white/10" />
              {['left-[12%] top-[18%]', 'left-[24%] bottom-[20%]', 'right-[16%] top-[16%]', 'right-[24%] bottom-[16%]'].map((position, index) => (
                <span
                  key={position}
                  className={cn('floating-orb absolute h-3 w-3 rounded-full bg-white/50 blur-[0.4px]', position)}
                  style={{ animationDelay: `${index * 0.22}s` }}
                />
              ))}

              <div className="relative z-[1] mx-auto max-w-[760px] text-center">
                <div className="inline-flex items-center gap-2 rounded-full border border-white/16 bg-white/10 px-4 py-2 text-[0.72rem] font-semibold uppercase tracking-[0.32em] text-white/80 backdrop-blur-xl">
                  <span className="h-2 w-2 rounded-full bg-white" />
                  Final call to action
                </div>
                <h2 className="mt-8 text-balance text-4xl font-semibold tracking-[-0.06em] text-white sm:text-5xl lg:text-[4.1rem] lg:leading-[0.98]">
                  Let AI Run Your Sales Infrastructure
                </h2>
                <p className="mx-auto mt-6 max-w-[620px] text-pretty text-base leading-8 text-white/72 sm:text-lg">
                  Scale automation, customer intelligence, omnichannel routing, and pipeline visibility from one AI-native operating layer built to feel calm, premium, and enterprise-ready.
                </p>

                <div className="mt-10 flex flex-wrap justify-center gap-4">
                  <MagneticAction to="/signup" className="px-7 py-3.5">
                    Get Started Free
                    <ArrowRight size={16} />
                  </MagneticAction>
                  <MagneticAction variant="light" to="/contact" className="px-6 py-3.5">
                    Talk to Sales
                  </MagneticAction>
                </div>

                <p className="mt-6 text-sm text-white/55">
                  No credit card · 14-day free trial · Enterprise rollout support available
                </p>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-100 bg-white/90 px-5 py-12 backdrop-blur-xl sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[1280px]">
          <div className="grid gap-10 lg:grid-cols-[1.4fr_1fr_1fr_1fr]">
            <div className="max-w-[300px]">
              <PlatformLogo textColor="#0f172a" imageWidth={30} fontSize={16} fontWeight={800} />
              <p className="mt-5 text-sm leading-7 text-slate-600">
                AI-powered customer engagement for modern revenue teams. One intelligent inbox, one customer graph, one operating system for growth.
              </p>
            </div>

            {FOOTER_COLUMNS.map((column) => (
              <div key={column.title}>
                <div className="text-[0.72rem] font-semibold uppercase tracking-[0.28em] text-slate-400">{column.title}</div>
                <div className="mt-5 grid gap-3">
                  {column.links.map(([label, href]) => (
                    <SmartLink
                      key={label}
                      href={href}
                      onAnchorClick={handleAnchorClick}
                      className="text-sm text-slate-600 transition-colors hover:text-slate-950"
                    >
                      {label}
                    </SmartLink>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-10 flex flex-col gap-3 border-t border-slate-100 pt-6 text-sm text-slate-500 md:flex-row md:items-center md:justify-between">
            <p>© 2026 Pulse Engine. All rights reserved.</p>
            <p>Manage leads, conversations, and growth in one intelligent inbox.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}


