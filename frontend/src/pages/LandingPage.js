import { Link } from 'react-router-dom';
import PlatformLogo from '@/components/PlatformLogo';
import {
  Bot, BarChart3, Users, Zap, ArrowRight, Check, Globe, TrendingUp,
  Clock, Inbox, Target, ChevronDown, Cpu, PieChart, BookOpen, FileText,
  Mail, HelpCircle, Menu, X, MessageSquare,
  Sparkles, Shield, Activity, Play, ChevronRight, Star,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { getLandingPricingPlans } from '@/data/publicPricingPlans';
import {
  NavBrandIconWhatsApp,
  NavBrandIconInstagram,
  NavBrandIconFacebook,
  NavBrandIconEmail,
} from '@/components/landing/ChannelBrandNavIcons';

/* ─────────────────────────────────────────────────────────────────────────────
   UTILITIES
───────────────────────────────────────────────────────────────────────────── */

function useInView(options = {}) {
  const ref = useRef(null);
  const [isInView, setIsInView] = useState(false);
  const { once = true, threshold = 0.12 } = options;
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setIsInView(true); if (once) observer.disconnect(); }
    }, { threshold });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, [once, threshold]);
  return [ref, isInView];
}

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

/* ─────────────────────────────────────────────────────────────────────────────
   FAQ ITEM
───────────────────────────────────────────────────────────────────────────── */
function FaqItem({ q, a, dark = false }) {
  const [open, setOpen] = useState(false);
  const borderColor = dark ? 'rgba(255,255,255,0.08)' : '#e8ecf0';
  const bgOpen = dark ? 'rgba(255,255,255,0.04)' : '#f8fafc';
  const bgClosed = dark ? 'transparent' : '#fff';
  const questionColor = dark ? '#f1f5f9' : '#0f172a';
  const answerColor = dark ? '#94a3b8' : '#64748b';
  return (
    <div style={{ border: `1px solid ${borderColor}`, borderRadius: 14, overflow: 'hidden', marginBottom: 8 }}>
      <button onClick={() => setOpen(!open)}
        style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 24px', background: open ? bgOpen : bgClosed, cursor: 'pointer', border: 'none', textAlign: 'left', transition: 'background 0.2s' }}>
        <span style={{ fontSize: 14.5, fontWeight: 600, color: questionColor, lineHeight: 1.4 }}>{q}</span>
        <span style={{ color: open ? '#3b82f6' : (dark ? '#475569' : '#94a3b8'), transition: 'transform 0.3s', transform: open ? 'rotate(45deg)' : 'rotate(0deg)', fontSize: 22, flexShrink: 0, lineHeight: 1, marginLeft: 16 }}>+</span>
      </button>
      <div style={{ maxHeight: open ? 220 : 0, overflow: 'hidden', transition: 'max-height 0.4s cubic-bezier(0.4,0,0.2,1)' }}>
        <p style={{ padding: '0 24px 20px', fontSize: 14, color: answerColor, lineHeight: 1.75, margin: 0 }}>{a}</p>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────────
   NAV DROPDOWN
───────────────────────────────────────────────────────────────────────────── */
function NavDropdown({ label, items, dark }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const labelColor = dark ? (open ? '#fff' : 'rgba(255,255,255,0.7)') : (open ? '#0f172a' : '#64748b');

  return (
    <div ref={ref} style={{ position: 'relative' }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button style={{
        display: 'flex', alignItems: 'center', gap: 4, fontSize: 14,
        color: labelColor, background: 'none', border: 'none', cursor: 'pointer',
        padding: '6px 2px', fontWeight: open ? 600 : 400, transition: 'color 0.2s', position: 'relative',
      }}>
        <span style={{ position: 'relative' }}>
          {label}
          <span style={{ position: 'absolute', bottom: -2, left: 0, right: 0, height: 2, background: 'linear-gradient(90deg,#2563eb,#6366f1)', borderRadius: 2, transform: open ? 'scaleX(1)' : 'scaleX(0)', transformOrigin: 'left', transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)' }} />
        </span>
        <ChevronDown size={13} style={{ transition: 'transform 0.25s', transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }} />
      </button>
      <div style={{
        position: 'absolute', top: 'calc(100% + 10px)', left: '50%',
        transform: open ? 'translateX(-50%) translateY(0) scale(1)' : 'translateX(-50%) translateY(-8px) scale(0.96)',
        opacity: open ? 1 : 0, pointerEvents: open ? 'all' : 'none',
        transition: 'all 0.22s cubic-bezier(0.4,0,0.2,1)',
        background: '#fff', border: '1px solid #e8ecf0',
        borderRadius: 16, boxShadow: '0 20px 50px rgba(0,0,0,0.14)', padding: '8px',
        minWidth: 248, zIndex: 100,
      }}>
        <div style={{ position: 'absolute', top: -5, left: '50%', transform: 'translateX(-50%)', width: 10, height: 10, background: '#fff', border: '1px solid #e8ecf0', borderRight: 'none', borderBottom: 'none', rotate: '45deg' }} />
        {items.map((item, i) => (
          <a key={i} href={item.href}
            style={{ display: 'flex', alignItems: 'flex-start', gap: 11, padding: '10px 12px', borderRadius: 10, textDecoration: 'none', color: 'inherit', transition: 'background 0.15s', cursor: 'pointer' }}
            onMouseEnter={e => e.currentTarget.style.background = '#f5f7fa'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
            <div style={{ width: 34, height: 34, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', background: item.color || '#eff6ff', flexShrink: 0, marginTop: 1 }}>
              {item.icon}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#0f172a' }}>{item.label}</div>
              {item.desc && <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 1 }}>{item.desc}</div>}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

function NavLink({ href, children, dark }) {
  const [hovered, setHovered] = useState(false);
  const color = dark ? (hovered ? '#fff' : 'rgba(255,255,255,0.7)') : (hovered ? '#0f172a' : '#64748b');
  return (
    <a href={href} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      style={{ position: 'relative', fontSize: 14, color, textDecoration: 'none', fontWeight: hovered ? 600 : 400, padding: '6px 2px', transition: 'color 0.2s' }}>
      {children}
      <span style={{ position: 'absolute', bottom: -2, left: 0, right: 0, height: 2, background: 'linear-gradient(90deg,#2563eb,#6366f1)', borderRadius: 2, transform: hovered ? 'scaleX(1)' : 'scaleX(0)', transformOrigin: 'left', transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)' }} />
    </a>
  );
}

/* ─────────────────────────────────────────────────────────────────────────────
   HERO — INTERACTIVE CRM PIPELINE VISUALIZATION
───────────────────────────────────────────────────────────────────────────── */
function PipelineVisualization() {
  const [activeStage, setActiveStage] = useState(1);
  const [pulseCard, setPulseCard] = useState(null);

  const stages = [
    { id: 0, label: 'New', count: 12, color: '#64748b', bg: '#f1f5f9' },
    { id: 1, label: 'Qualified', count: 8, color: '#2563eb', bg: '#eff6ff' },
    { id: 2, label: 'Proposal', count: 5, color: '#7c3aed', bg: '#f5f3ff' },
    { id: 3, label: 'Closing', count: 3, color: '#059669', bg: '#ecfdf5' },
  ];

  const leads = [
    { name: 'Aisha Rahman', channel: 'wa', score: 87, stage: 1, value: '$4,200', tag: 'Hot' },
    { name: 'Marcus Chen', channel: 'ig', score: 72, stage: 1, value: '$1,800', tag: 'Warm' },
    { name: 'Sofia Kowalski', channel: 'email', score: 91, stage: 2, value: '$8,500', tag: 'Hot' },
    { name: 'James Okonkwo', channel: 'wa', score: 64, stage: 0, value: '$950', tag: 'New' },
    { name: 'Lena Müller', channel: 'ig', score: 95, stage: 3, value: '$12,000', tag: 'Won' },
  ];

  const channelColors = { wa: '#25D366', ig: '#E1306C', email: '#2563eb', fb: '#1877F2' };
  const channelLabels = { wa: 'WA', ig: 'IG', email: 'EM', fb: 'FB' };
  const tagColors = {
    Hot: { bg: '#fef3c7', color: '#92400e' },
    Warm: { bg: '#dbeafe', color: '#1e40af' },
    New: { bg: '#f1f5f9', color: '#475569' },
    Won: { bg: '#d1fae5', color: '#065f46' },
  };

  useEffect(() => {
    const t = setInterval(() => {
      setActiveStage(s => (s + 1) % 4);
      setPulseCard(Math.floor(Math.random() * leads.length));
      setTimeout(() => setPulseCard(null), 800);
    }, 2400);
    return () => clearInterval(t);
  }, [leads.length]);

  return (
    <div style={{ background: '#fff', borderRadius: 20, border: '1px solid #e8ecf0', boxShadow: '0 24px 64px rgba(15,23,42,0.10)', overflow: 'hidden' }}>
      {/* Browser chrome */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '11px 16px', background: '#f8fafc', borderBottom: '1px solid #f1f5f9' }}>
        <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#fc5c57' }} />
        <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#fdbc2c' }} />
        <div style={{ width: 11, height: 11, borderRadius: '50%', background: '#34c84a' }} />
        <div style={{ marginLeft: 12, flex: 1, background: '#fff', border: '1px solid #e8ecf0', borderRadius: 6, padding: '4px 12px', fontSize: 11, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#22c55e' }} />
          app.pulseengine.io/pipeline
        </div>
        <div style={{ display: 'flex', gap: 6, marginLeft: 12 }}>
          {['AI', 'Live'].map(t => (
            <span key={t} style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 5, background: t === 'AI' ? '#eff6ff' : '#ecfdf5', color: t === 'AI' ? '#2563eb' : '#059669' }}>{t}</span>
          ))}
        </div>
      </div>

      {/* Pipeline board */}
      <div style={{ padding: '16px 16px 14px' }}>
        {/* Stage headers */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 10 }}>
          {stages.map(s => (
            <div key={s.id} style={{ textAlign: 'center', padding: '7px 8px', borderRadius: 10, background: activeStage === s.id ? s.bg : '#fafafa', border: `1px solid ${activeStage === s.id ? s.color + '30' : '#f1f5f9'}`, transition: 'all 0.5s ease' }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: activeStage === s.id ? s.color : '#94a3b8', transition: 'color 0.5s' }}>{s.label}</div>
              <div style={{ fontSize: 16, fontWeight: 800, color: activeStage === s.id ? s.color : '#cbd5e1', transition: 'color 0.5s' }}>{s.count}</div>
            </div>
          ))}
        </div>

        {/* Lead cards */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {leads.map((lead, i) => {
            const tag = tagColors[lead.tag];
            const isPulsing = pulseCard === i;
            return (
              <div key={lead.name} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px',
                background: isPulsing ? '#f8fafc' : '#fff',
                borderRadius: 11, border: `1px solid ${isPulsing ? '#2563eb30' : '#f1f5f9'}`,
                transition: 'all 0.4s ease',
                transform: isPulsing ? 'translateX(3px)' : 'translateX(0)',
              }}>
                {/* Channel dot */}
                <div style={{ width: 28, height: 28, borderRadius: 8, background: channelColors[lead.channel] + '18', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, color: channelColors[lead.channel] }}>{channelLabels[lead.channel]}</span>
                </div>
                {/* Name */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#0f172a', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{lead.name}</div>
                  <div style={{ fontSize: 10.5, color: '#94a3b8' }}>{lead.value}</div>
                </div>
                {/* Score */}
                <div style={{ fontSize: 11, fontWeight: 700, color: lead.score >= 80 ? '#059669' : lead.score >= 65 ? '#2563eb' : '#94a3b8', background: lead.score >= 80 ? '#ecfdf5' : lead.score >= 65 ? '#eff6ff' : '#f8fafc', padding: '2px 7px', borderRadius: 6 }}>
                  {lead.score}
                </div>
                {/* Tag */}
                <div style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 6, background: tag.bg, color: tag.color, flexShrink: 0 }}>
                  {lead.tag}
                </div>
              </div>
            );
          })}
        </div>

        {/* AI activity bar */}
        <div style={{ marginTop: 10, padding: '8px 12px', background: 'linear-gradient(90deg, #eff6ff, #f5f3ff)', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 20, height: 20, borderRadius: 6, background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Bot size={11} color="#fff" />
          </div>
          <span style={{ fontSize: 11, color: '#475569', fontWeight: 500 }}>AI qualified 3 leads & sent 2 follow-ups in the last hour</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 3 }}>
            {[0, 1, 2].map(j => (
              <div key={j} style={{ width: 4, height: 4, borderRadius: '50%', background: '#2563eb', animation: `pulse-dot 1.4s ease-in-out ${j * 0.2}s infinite` }} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────────
   ROTATING TEXT SECTION
───────────────────────────────────────────────────────────────────────────── */
function RotatingWord({ words }) {
  const [idx, setIdx] = useState(0);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const cycle = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIdx(i => (i + 1) % words.length);
        setVisible(true);
      }, 350);
    }, 2500);
    return () => clearInterval(cycle);
  }, [words.length]);
  return (
    <span style={{
      display: 'inline-block',
      background: 'linear-gradient(135deg,#2563eb,#6366f1)',
      WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
      backgroundClip: 'text',
      opacity: visible ? 1 : 0,
      transform: visible ? 'translateY(0)' : 'translateY(8px)',
      transition: 'opacity 0.35s ease, transform 0.35s ease',
      minWidth: 180,
    }}>
      {words[idx]}
    </span>
  );
}

/* ─────────────────────────────────────────────────────────────────────────────
   GLOBE SECTION
───────────────────────────────────────────────────────────────────────────── */
function GlobeVisualization() {
  const canvasRef = useRef(null);
  const animRef = useRef(null);
  const timeRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const cx = W / 2;
    const cy = H / 2;
    const R = Math.min(W, H) * 0.38;

    // Connection points on globe surface (lat/lon pairs)
    const points = [
      { lat: 40.7, lon: -74 },   // New York
      { lat: 51.5, lon: -0.1 },  // London
      { lat: 48.8, lon: 2.3 },   // Paris
      { lat: 25.2, lon: 55.3 },  // Dubai
      { lat: 1.3, lon: 103.8 },  // Singapore
      { lat: 35.7, lon: 139.7 }, // Tokyo
      { lat: -23.5, lon: -46.6 },// Sao Paulo
      { lat: 19.4, lon: -99.1 }, // Mexico City
      { lat: -33.9, lon: 18.4 }, // Cape Town
      { lat: 55.7, lon: 37.6 },  // Moscow
    ];

    const connections = [
      [0, 1], [1, 2], [0, 6], [1, 3], [3, 4], [4, 5], [0, 7], [2, 9], [3, 8],
    ];

    const pulses = connections.map(([a, b]) => ({
      from: a, to: b, t: Math.random(), speed: 0.003 + Math.random() * 0.002,
    }));

    function latLonTo3D(lat, lon, rot) {
      const phi = (90 - lat) * Math.PI / 180;
      const theta = (lon + rot) * Math.PI / 180;
      return {
        x: R * Math.sin(phi) * Math.cos(theta),
        y: -R * Math.cos(phi),
        z: R * Math.sin(phi) * Math.sin(theta),
      };
    }

    function project(p3d) {
      const scale = (R * 2.2) / (R * 2.2 + p3d.z * 0.3);
      return { x: cx + p3d.x * scale, y: cy + p3d.y * scale, visible: p3d.z > -R * 0.3 };
    }

    function lerp3D(a, b, t) {
      // Great circle interpolation simplified via lerp + normalize
      return {
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
        z: a.z + (b.z - a.z) * t,
      };
    }

    function draw(time) {
      timeRef.current = time;
      const rot = (time * 0.012) % 360;
      ctx.clearRect(0, 0, W, H);

      // Globe circle
      const grd = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.3, R * 0.1, cx, cy, R);
      grd.addColorStop(0, 'rgba(37,99,235,0.08)');
      grd.addColorStop(1, 'rgba(99,102,241,0.04)');
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fillStyle = grd;
      ctx.fill();
      ctx.strokeStyle = 'rgba(37,99,235,0.12)';
      ctx.lineWidth = 1;
      ctx.stroke();

      // Latitude lines
      for (let lat = -60; lat <= 60; lat += 30) {
        ctx.beginPath();
        let first = true;
        for (let lon = -180; lon <= 180; lon += 5) {
          const p = project(latLonTo3D(lat, lon, rot));
          if (!p.visible) { first = true; continue; }
          if (first) { ctx.moveTo(p.x, p.y); first = false; }
          else ctx.lineTo(p.x, p.y);
        }
        ctx.strokeStyle = 'rgba(99,102,241,0.08)';
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }

      // Longitude lines
      for (let lon = 0; lon < 180; lon += 30) {
        ctx.beginPath();
        let first = true;
        for (let lat = -85; lat <= 85; lat += 5) {
          const p = project(latLonTo3D(lat, lon, rot));
          if (!p.visible) { first = true; continue; }
          if (first) { ctx.moveTo(p.x, p.y); first = false; }
          else ctx.lineTo(p.x, p.y);
        }
        ctx.strokeStyle = 'rgba(99,102,241,0.08)';
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }

      // Compute projected points
      const projected = points.map(p => {
        const p3d = latLonTo3D(p.lat, p.lon, rot);
        const pr = project(p3d);
        return { ...pr, p3d };
      });

      // Connection lines
      connections.forEach(([ai, bi]) => {
        const a = projected[ai], b = projected[bi];
        if (!a.visible || !b.visible) return;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = 'rgba(37,99,235,0.18)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
      });

      // Pulse dots
      pulses.forEach(pulse => {
        const a = projected[pulse.from], b = projected[pulse.to];
        if (!a.visible || !b.visible) return;
        const t = pulse.t;
        const px = a.x + (b.x - a.x) * t;
        const py = a.y + (b.y - a.y) * t;
        ctx.beginPath();
        ctx.arc(px, py, 3, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(37,99,235,0.9)';
        ctx.fill();
        pulse.t += pulse.speed;
        if (pulse.t > 1) pulse.t = 0;
      });

      // City dots
      projected.forEach(p => {
        if (!p.visible) return;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.2, 0, Math.PI * 2);
        ctx.fillStyle = '#2563eb';
        ctx.fill();
      });

      animRef.current = requestAnimationFrame(draw);
    }

    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, []);

  return <canvas ref={canvasRef} width={500} height={500} style={{ width: '100%', maxWidth: 460, height: 'auto', display: 'block', margin: '0 auto' }} />;
}

/* ─────────────────────────────────────────────────────────────────────────────
   FEATURE TABS SHOWCASE
───────────────────────────────────────────────────────────────────────────── */
const FEATURE_TABS = [
  {
    id: 'ai-sales',
    label: 'AI Sales',
    icon: Target,
    color: '#2563eb',
    bg: '#eff6ff',
    title: 'AI Sales Automation',
    subtitle: 'Qualify, follow up, and close — on autopilot.',
    bullets: [
      'Automatic BANT lead qualification from every conversation',
      'AI follow-up sequences triggered by behavior signals',
      'Pipeline stage movement with zero manual updates',
      'Revenue prediction and opportunity scoring',
    ],
    visual: 'sales',
  },
  {
    id: 'inbox',
    label: 'Inbox',
    icon: Inbox,
    color: '#7c3aed',
    bg: '#f5f3ff',
    title: 'Omnichannel Inbox',
    subtitle: 'Every channel. One inbox. Zero chaos.',
    bullets: [
      'WhatsApp, Instagram, Facebook, Email & Web Chat unified',
      'Real-time collision detection — no duplicate replies',
      'Identity unification across all channels automatically',
      'Smart routing by team, skill, or language',
    ],
    visual: 'inbox',
  },
  {
    id: 'agents',
    label: 'AI Agents',
    icon: Bot,
    color: '#059669',
    bg: '#ecfdf5',
    title: 'Specialized AI Agents',
    subtitle: 'Agents that work together so you don\'t have to.',
    bullets: [
      'Support agent resolves up to 70% of queries without humans',
      'Sales agent qualifies and nurtures inbound leads 24/7',
      'Sentiment-aware escalation with full conversation context',
      'Continuous learning from every interaction',
    ],
    visual: 'agents',
  },
  {
    id: 'crm',
    label: 'CRM',
    icon: Users,
    color: '#dc2626',
    bg: '#fef2f2',
    title: 'CRM Intelligence',
    subtitle: 'Know every customer. Miss nothing.',
    bullets: [
      'Unified customer profiles across every touchpoint',
      'Lead scoring, segmentation, and lifecycle tracking',
      'Full conversation history with context at a glance',
      'Custom fields, tags, and pipeline stages',
    ],
    visual: 'crm',
  },
  {
    id: 'automation',
    label: 'Automation',
    icon: Zap,
    color: '#d97706',
    bg: '#fffbeb',
    title: 'Automation Engine',
    subtitle: 'Build workflows that run while you sleep.',
    bullets: [
      'Visual workflow builder with 40+ trigger types',
      'Multi-step sequences for follow-ups and nurture',
      'Condition-based branching and time delays',
      'Native integrations with CRMs and Zapier',
    ],
    visual: 'automation',
  },
  {
    id: 'analytics',
    label: 'Analytics',
    icon: BarChart3,
    color: '#0284c7',
    bg: '#f0f9ff',
    title: 'Revenue Analytics',
    subtitle: 'See exactly what\'s driving growth.',
    bullets: [
      'Live dashboards updated every 30 seconds',
      'CSAT scores, response times & resolution rates',
      'Channel-by-channel conversion breakdown',
      'Exportable reports for any date range',
    ],
    visual: 'analytics',
  },
];

function FeatureVisual({ type, color, bg }) {
  if (type === 'sales') return (
    <div style={{ height: '100%', padding: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: 1, textTransform: 'uppercase' }}>AI Pipeline · Live</div>
      {[
        { name: 'Aisha Rahman', stage: 'Qualifying', score: 87, bar: 87 },
        { name: 'Marcus Chen', stage: 'Follow-up sent', score: 72, bar: 72 },
        { name: 'Sofia K.', stage: 'Proposal ready', score: 91, bar: 91 },
        { name: 'James O.', stage: 'New inquiry', score: 55, bar: 55 },
      ].map((lead, i) => (
        <div key={lead.name} style={{ background: '#fff', borderRadius: 12, padding: '10px 14px', border: '1px solid #f1f5f9', animation: `slideInRow 0.5s ease ${i * 80}ms both` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 7 }}>
            <div>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#0f172a' }}>{lead.name}</div>
              <div style={{ fontSize: 11, color: '#94a3b8' }}>{lead.stage}</div>
            </div>
            <div style={{ fontSize: 13, fontWeight: 800, color: lead.score >= 80 ? '#059669' : '#2563eb' }}>{lead.score}</div>
          </div>
          <div style={{ height: 4, background: '#f1f5f9', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${lead.bar}%`, background: `linear-gradient(90deg, ${color}, ${color}cc)`, borderRadius: 2 }} />
          </div>
        </div>
      ))}
    </div>
  );

  if (type === 'inbox') return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #f1f5f9', display: 'flex', gap: 8 }}>
        {[{ label: 'All', count: 32, active: true }, { label: 'WhatsApp', count: 12 }, { label: 'Instagram', count: 9 }, { label: 'Email', count: 11 }].map(tab => (
          <div key={tab.label} style={{ fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 7, background: tab.active ? color : '#f8fafc', color: tab.active ? '#fff' : '#64748b', cursor: 'pointer' }}>
            {tab.label} <span style={{ opacity: 0.7 }}>{tab.count}</span>
          </div>
        ))}
      </div>
      <div style={{ flex: 1, overflow: 'hidden', padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {[
          { name: 'Priya S.', msg: 'I need help with my order…', time: '2m', ch: '#25D366', unread: true },
          { name: 'Carlos M.', msg: 'Is this available in blue?', time: '5m', ch: '#E1306C', unread: true },
          { name: 'Emma W.', msg: 'Thanks! That worked 🙌', time: '12m', ch: '#2563eb', unread: false },
          { name: 'Li Wei', msg: 'Can I get a discount for bulk?', time: '18m', ch: '#7c3aed', unread: false },
        ].map(conv => (
          <div key={conv.name} style={{ display: 'flex', gap: 10, padding: '9px 10px', borderRadius: 10, background: conv.unread ? '#fafbff' : '#fff', border: `1px solid ${conv.unread ? '#e0e7ff' : '#f5f5f5'}`, alignItems: 'center' }}>
            <div style={{ width: 32, height: 32, borderRadius: 10, background: conv.ch + '22', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: conv.ch }} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: conv.unread ? 700 : 500, color: '#0f172a' }}>{conv.name}</div>
              <div style={{ fontSize: 11, color: '#94a3b8', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{conv.msg}</div>
            </div>
            <div style={{ fontSize: 10, color: '#cbd5e1', flexShrink: 0 }}>{conv.time}</div>
          </div>
        ))}
      </div>
    </div>
  );

  if (type === 'agents') return (
    <div style={{ height: '100%', padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 4 }}>Agent Collaboration</div>
      {[
        { agent: 'Support Agent', action: 'Resolved billing inquiry', time: '4s', icon: Shield },
        { agent: 'Sales Agent', action: 'Qualified lead — BANT score 84', time: '12s', icon: Target },
        { agent: 'Support Agent', action: 'Escalated to human (sentiment)', time: '28s', icon: MessageSquare },
        { agent: 'Sales Agent', action: 'Sent follow-up proposal link', time: '1m', icon: Zap },
      ].map((item, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 12px', background: '#fff', borderRadius: 11, border: '1px solid #f1f5f9' }}>
          <div style={{ width: 28, height: 28, borderRadius: 8, background: color + '18', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1 }}>
            <item.icon size={13} color={color} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: color }}>{item.agent}</div>
            <div style={{ fontSize: 12, color: '#475569' }}>{item.action}</div>
          </div>
          <div style={{ fontSize: 10, color: '#94a3b8', flexShrink: 0 }}>{item.time} ago</div>
        </div>
      ))}
      <div style={{ padding: '8px 12px', background: color + '10', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, animation: 'pulse-dot 1.5s infinite' }} />
        <span style={{ fontSize: 11, color: color, fontWeight: 600 }}>3 agents active · 47 conversations handled today</span>
      </div>
    </div>
  );

  if (type === 'crm') return (
    <div style={{ height: '100%', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: '14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <div style={{ width: 38, height: 38, borderRadius: 11, background: 'linear-gradient(135deg,#2563eb,#6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 14, fontWeight: 800 }}>A</div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#0f172a' }}>Aisha Rahman</div>
            <div style={{ fontSize: 11, color: '#94a3b8' }}>Enterprise · Dubai, UAE</div>
          </div>
          <div style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 7, background: '#d1fae5', color: '#065f46' }}>Score 91</div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[['Conversations', '24'], ['Open Deals', '$8,500'], ['Last Active', '2h ago'], ['Channels', 'WA, IG, EM']].map(([k, v]) => (
            <div key={k} style={{ padding: '8px 10px', background: '#fafafa', borderRadius: 9, border: '1px solid #f1f5f9' }}>
              <div style={{ fontSize: 10, color: '#94a3b8', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>{k}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#0f172a', marginTop: 2 }}>{v}</div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: '12px 14px' }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 }}>Activity Timeline</div>
        {['WhatsApp conversation — 12 min', 'Lead score updated to 91', 'Follow-up email sent by AI'].map((item, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
            <span style={{ fontSize: 11.5, color: '#475569' }}>{item}</span>
          </div>
        ))}
      </div>
    </div>
  );

  if (type === 'automation') return (
    <div style={{ height: '100%', padding: 20, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 4 }}>Workflow · Lead Nurture</div>
      {[
        { step: 'Trigger', label: 'New WhatsApp message received', icon: MessageSquare, active: true },
        { step: 'Action', label: 'AI qualifies lead via BANT', icon: Bot, active: true },
        { step: 'Condition', label: 'Score ≥ 70?', icon: ChevronRight, active: false },
        { step: 'Action', label: 'Send personalized follow-up', icon: Mail, active: false },
        { step: 'Wait', label: '2 days · if no reply → escalate', icon: Clock, active: false },
      ].map((step, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {i > 0 && (
            <div style={{ position: 'absolute', width: 1, height: 12, background: '#f1f5f9', left: 34, marginTop: -16 }} />
          )}
          <div style={{ width: 30, height: 30, borderRadius: 9, background: step.active ? color + '18' : '#f8fafc', border: `1px solid ${step.active ? color + '30' : '#f1f5f9'}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <step.icon size={13} color={step.active ? color : '#94a3b8'} />
          </div>
          <div style={{ flex: 1, padding: '8px 12px', background: step.active ? color + '08' : '#fff', borderRadius: 10, border: `1px solid ${step.active ? color + '20' : '#f1f5f9'}` }}>
            <div style={{ fontSize: 9.5, fontWeight: 700, color: step.active ? color : '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>{step.step}</div>
            <div style={{ fontSize: 12, color: '#0f172a', fontWeight: 500 }}>{step.label}</div>
          </div>
        </div>
      ))}
    </div>
  );

  // analytics
  return (
    <div style={{ height: '100%', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {[
          { label: 'Conversations', value: '2,847', change: '+18%', up: true },
          { label: 'Resolution Rate', value: '73%', change: '+5pt', up: true },
          { label: 'Avg Response', value: '4.2s', change: '-1.8s', up: true },
          { label: 'CSAT Score', value: '4.9/5', change: '+0.3', up: true },
        ].map(m => (
          <div key={m.label} style={{ background: '#fff', borderRadius: 12, padding: '12px 14px', border: '1px solid #f1f5f9' }}>
            <div style={{ fontSize: 10, color: '#94a3b8', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>{m.label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#0f172a', lineHeight: 1.2, marginTop: 3 }}>{m.value}</div>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#059669', marginTop: 3 }}>{m.change}</div>
          </div>
        ))}
      </div>
      {/* Simple bar chart */}
      <div style={{ background: '#fff', borderRadius: 12, padding: '12px 14px', border: '1px solid #f1f5f9', flex: 1 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', marginBottom: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>Weekly Conversations</div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 60 }}>
          {[55, 72, 61, 88, 94, 80, 99].map((v, i) => (
            <div key={i} style={{ flex: 1, background: `linear-gradient(to top, ${color}, ${color}88)`, borderRadius: '3px 3px 0 0', height: `${v}%`, transition: 'height 0.5s ease' }} />
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
          {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
            <span key={i} style={{ fontSize: 9, color: '#94a3b8', flex: 1, textAlign: 'center' }}>{d}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function FeatureTabs() {
  const [active, setActive] = useState(0);
  const tab = FEATURE_TABS[active];

  return (
    <div style={{ display: 'flex', gap: 0, minHeight: 480 }}>
      {/* Tab list */}
      <div style={{ width: 200, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 2, paddingRight: 20 }}>
        {FEATURE_TABS.map((t, i) => (
          <button key={t.id} onClick={() => setActive(i)}
            style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '11px 14px',
              borderRadius: 11, border: 'none', cursor: 'pointer', textAlign: 'left',
              background: active === i ? t.color : 'transparent',
              color: active === i ? '#fff' : '#64748b',
              transition: 'all 0.25s ease',
              fontWeight: active === i ? 600 : 400,
            }}>
            <t.icon size={16} />
            <span style={{ fontSize: 13.5 }}>{t.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, display: 'flex', gap: 32, alignItems: 'flex-start' }}>
        {/* Text */}
        <div style={{ flex: '0 0 auto', width: '42%', paddingTop: 8 }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 12px', borderRadius: 100, background: tab.bg, color: tab.color, fontSize: 12, fontWeight: 700, marginBottom: 16 }}>
            <tab.icon size={13} />
            {tab.label}
          </div>
          <h3 style={{ fontSize: 24, fontWeight: 800, color: '#0f172a', marginBottom: 10, letterSpacing: '-0.3px', lineHeight: 1.2 }}>{tab.title}</h3>
          <p style={{ fontSize: 14.5, color: '#64748b', marginBottom: 20, lineHeight: 1.65 }}>{tab.subtitle}</p>
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {tab.bullets.map(b => (
              <li key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 13.5, color: '#334155', lineHeight: 1.5 }}>
                <span style={{ width: 20, height: 20, borderRadius: '50%', background: tab.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1 }}>
                  <Check size={11} color={tab.color} />
                </span>
                {b}
              </li>
            ))}
          </ul>
        </div>

        {/* Visual */}
        <div style={{ flex: 1, background: tab.bg, borderRadius: 20, border: `1px solid ${tab.color}18`, overflow: 'hidden', minHeight: 380, position: 'relative' }}>
          <FeatureVisual type={tab.visual} color={tab.color} bg={tab.bg} />
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────────
   MAIN COMPONENT
───────────────────────────────────────────────────────────────────────────── */
export default function LandingPage() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' ? window.innerWidth < 768 : false);
  const [isTablet, setIsTablet] = useState(() => typeof window !== 'undefined' ? window.innerWidth < 1024 : false);

  const [heroRef, heroInView] = useInView({ threshold: 0.05 });
  const [metricsRef, metricsInView] = useInView();
  const [featuresRef, featuresInView] = useInView({ threshold: 0.05 });
  const [globeRef, globeInView] = useInView({ threshold: 0.15 });
  const [pricingRef, pricingInView] = useInView({ threshold: 0.05 });
  const [ctaRef, ctaInView] = useInView({ threshold: 0.2 });

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const onResize = () => {
      setIsMobile(window.innerWidth < 768);
      setIsTablet(window.innerWidth < 1024);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const pricingPlans = getLandingPricingPlans();

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
    { label: 'Email', desc: 'Send/Receive Emails', href: '#channels', icon: <NavBrandIconEmail />, color: '#f1f5f9' },
    { label: 'Web Chat', desc: 'Embeddable widget', href: '#channels', icon: <Globe size={16} color="#64748b" strokeWidth={1.75} />, color: '#f8fafc' },
  ];
  const resourcesDropdown = [
    { label: 'Documentation', desc: 'Guides & API reference', href: '#', icon: <BookOpen size={15} color="#2563eb" />, color: '#eff6ff' },
    { label: 'Blog', desc: 'Tips & best practices', href: '#', icon: <FileText size={15} color="#7c3aed" />, color: '#f5f3ff' },
    { label: 'Help Center', desc: 'Answers to common questions', href: '#faq', icon: <HelpCircle size={15} color="#0284c7" />, color: '#f0f9ff' },
    { label: 'Contact Us', desc: 'Talk to our team', href: '#', icon: <Mail size={15} color="#059669" />, color: '#ecfdf5' },
  ];

  const faqs = [
    { q: 'Which channels does Pulse Engine support?', a: 'Pulse Engine supports WhatsApp Business API, Facebook Messenger, Instagram Direct, Email (IMAP + SMTP/Brevo), and an embeddable Web Chat widget out of the box. Additional channels can be added via webhooks.' },
    { q: 'What is included in the free Starter plan?', a: 'The Starter plan is permanently free. It includes up to 500 conversations per month, WhatsApp & Web Chat integration, basic AI-assisted replies (50/month), and a shared inbox for 1 user. No credit card required.' },
    { q: 'How does the AI know when to escalate to a human?', a: 'The AI monitors sentiment signals, detects frustration patterns, and recognises intent that falls outside its confidence threshold. It hands off seamlessly with full conversation context so your agent can pick up instantly.' },
    { q: 'Can multiple agents work on the same inbox?', a: 'Yes. The shared inbox includes real-time collision detection so two agents never reply to the same conversation simultaneously. You can also set routing rules by team, skill, or language.' },
    { q: 'Is there a free trial on paid plans?', a: 'Yes — all new accounts receive a 30-day trial of Pro features. After the trial, you can continue on the free Starter plan or upgrade to Pro ($29/mo) or Enterprise ($99/mo) with no commitment.' },
    { q: 'How is my data secured?', a: 'All data is encrypted at rest and in transit. We provide JWT-based authentication, role-based access control, and are fully GDPR-compliant. Enterprise plans support on-premise deployment.' },
  ];

  const heroNavDark = !scrolled;

  return (
    <div className="lp-root" style={{ minHeight: '100vh', background: '#fff', fontFamily: '"Inter", "DM Sans", system-ui, sans-serif' }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

        .lp-root, .lp-root * {
          box-sizing: border-box;
        }

        @keyframes pulse-dot {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.8); }
        }

        @keyframes slideInRow {
          from { opacity: 0; transform: translateX(-12px); }
          to { opacity: 1; transform: translateX(0); }
        }

        @keyframes float-gentle {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-8px); }
        }

        @keyframes spin-slow {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        .lp-nav-link {
          position: relative;
          font-size: 14px;
          text-decoration: none;
          padding: 6px 2px;
          transition: color 0.2s;
          font-weight: 400;
        }
        .lp-nav-link:hover { font-weight: 600; }

        .ch-card-hover {
          transition: transform 0.35s cubic-bezier(.34,1.56,.64,1), box-shadow 0.35s ease;
        }
        .ch-card-hover:hover {
          transform: translateY(-8px) scale(1.02);
          box-shadow: 0 20px 48px rgba(0,0,0,0.10) !important;
        }

        .lp-pricing-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 20px;
          max-width: 1100px;
          margin: 0 auto;
        }
        @media (max-width: 860px) {
          .lp-pricing-grid { grid-template-columns: 1fr; max-width: 440px; }
        }

        .lp-footer-grid {
          display: grid;
          grid-template-columns: 2fr 1fr 1fr 1fr;
          gap: 40px;
        }
        @media (max-width: 768px) {
          .lp-footer-grid { grid-template-columns: 1fr 1fr; gap: 28px; }
        }
        @media (max-width: 480px) {
          .lp-footer-grid { grid-template-columns: 1fr; }
        }

        .lp-hero-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 60px;
          align-items: center;
        }
        @media (max-width: 900px) {
          .lp-hero-grid { grid-template-columns: 1fr; gap: 40px; }
        }

        .lp-channels-grid {
          display: grid;
          grid-template-columns: repeat(5, 1fr);
          gap: 16px;
        }
        @media (max-width: 900px) {
          .lp-channels-grid { grid-template-columns: repeat(3, 1fr); }
        }
        @media (max-width: 560px) {
          .lp-channels-grid { grid-template-columns: repeat(2, 1fr); }
        }

        .lp-metrics-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 20px;
        }
        @media (max-width: 900px) {
          .lp-metrics-grid { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 500px) {
          .lp-metrics-grid { grid-template-columns: 1fr 1fr; }
        }

        .lp-feat-tabs-wrap {
          display: flex;
          gap: 0;
          min-height: 480px;
        }
        @media (max-width: 900px) {
          .lp-feat-tabs-wrap { flex-direction: column; gap: 20px; }
          .lp-feat-tab-list { width: 100% !important; flex-direction: row !important; flex-wrap: wrap; padding-right: 0 !important; gap: 6px !important; }
          .lp-feat-tab-content { flex-direction: column !important; }
          .lp-feat-tab-text { width: 100% !important; }
          .lp-feat-visual { min-height: 280px !important; }
        }

        .lp-globe-wrap {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 60px;
          align-items: center;
        }
        @media (max-width: 800px) {
          .lp-globe-wrap { grid-template-columns: 1fr; }
        }

        .lp-mob-nav { display: none; }
        @media (max-width: 768px) {
          .lp-desk-nav { display: none !important; }
          .lp-mob-nav { display: flex; }
        }

        .lp-mob-menu {
          position: fixed;
          top: 64px;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(15,23,42,0.97);
          backdrop-filter: blur(16px);
          z-index: 49;
          padding: 28px 24px;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .lp-mob-menu-link {
          display: block;
          padding: 14px 0;
          font-size: 18px;
          font-weight: 600;
          color: #fff;
          text-decoration: none;
          border-bottom: 1px solid rgba(255,255,255,0.06);
          transition: color 0.2s;
        }
        .lp-mob-menu-link:hover { color: #3b82f6; }
      `}</style>

      {/* ═══════════════════════════════════════════════════════════════
          HEADER
      ══════════════════════════════════════════════════════════════════ */}
      <header style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
        transition: 'all 0.35s cubic-bezier(0.4,0,0.2,1)',
        background: scrolled ? 'rgba(255,255,255,0.94)' : 'transparent',
        backdropFilter: scrolled ? 'blur(20px) saturate(1.3)' : 'none',
        borderBottom: scrolled ? '1px solid #f1f5f9' : '1px solid transparent',
        boxShadow: scrolled ? '0 2px 24px rgba(0,0,0,0.07)' : 'none',
      }}>
        <div style={{ maxWidth: 1280, margin: '0 auto', padding: '0 28px', height: 64, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <PlatformLogo fontWeight={800} textColor={scrolled ? '#0f172a' : '#0f172a'} />

          {/* Desktop nav */}
          <nav className="lp-desk-nav" style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
            <NavDropdown label="Features" items={featuresDropdown} dark={false} />
            <NavDropdown label="Channels" items={channelsDropdown} dark={false} />
            <NavLink href="#pricing" dark={false}>Pricing</NavLink>
            <NavDropdown label="Resources" items={resourcesDropdown} dark={false} />
          </nav>

          <div className="lp-desk-nav" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Link to="/signin" style={{ fontSize: 13.5, fontWeight: 500, color: '#475569', textDecoration: 'none', padding: '7px 16px', borderRadius: 9, transition: 'all 0.2s' }}
              onMouseEnter={e => { e.currentTarget.style.background = '#f1f5f9'; e.currentTarget.style.color = '#0f172a'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#475569'; }}>
              Sign In
            </Link>
            <Link to="/signup" style={{ fontSize: 13.5, fontWeight: 700, color: '#fff', background: 'linear-gradient(135deg,#2563eb,#4f46e5)', padding: '8px 20px', borderRadius: 10, textDecoration: 'none', boxShadow: '0 4px 16px rgba(37,99,235,0.32)', transition: 'all 0.2s' }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(37,99,235,0.42)'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 4px 16px rgba(37,99,235,0.32)'; }}>
              Start Free Trial
            </Link>
          </div>

          {/* Mobile menu button */}
          <button className="lp-mob-nav" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 8, color: '#0f172a', display: 'flex', alignItems: 'center' }}>
            {mobileMenuOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>

        {/* Mobile menu */}
        {mobileMenuOpen && (
          <div className="lp-mob-menu">
            {['Features', 'Channels', 'Pricing', 'Resources'].map(item => (
              <a key={item} href={`#${item.toLowerCase()}`} className="lp-mob-menu-link" onClick={() => setMobileMenuOpen(false)}>{item}</a>
            ))}
            <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <Link to="/signin" style={{ display: 'block', padding: '14px 20px', background: 'rgba(255,255,255,0.06)', color: '#fff', textDecoration: 'none', borderRadius: 12, textAlign: 'center', fontSize: 15, fontWeight: 600 }}>Sign In</Link>
              <Link to="/signup" style={{ display: 'block', padding: '14px 20px', background: 'linear-gradient(135deg,#2563eb,#4f46e5)', color: '#fff', textDecoration: 'none', borderRadius: 12, textAlign: 'center', fontSize: 15, fontWeight: 700 }}>Start Free Trial →</Link>
            </div>
          </div>
        )}
      </header>

      {/* ═══════════════════════════════════════════════════════════════
          HERO
      ══════════════════════════════════════════════════════════════════ */}
      <section ref={heroRef} style={{ position: 'relative', paddingTop: 104, paddingBottom: 80, paddingLeft: 28, paddingRight: 28, overflow: 'hidden', background: '#fff' }}>
        {/* Subtle background gradient */}
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <div style={{ position: 'absolute', top: -200, right: -200, width: 700, height: 700, background: 'radial-gradient(circle, rgba(99,102,241,0.07) 0%, transparent 70%)', borderRadius: '50%' }} />
          <div style={{ position: 'absolute', top: 100, left: -100, width: 500, height: 500, background: 'radial-gradient(circle, rgba(37,99,235,0.06) 0%, transparent 70%)', borderRadius: '50%' }} />
        </div>

        <div style={{ maxWidth: 1200, margin: '0 auto', position: 'relative', zIndex: 1 }}>
          <div className="lp-hero-grid">
            {/* Left: Copy */}
            <div style={{ opacity: heroInView ? 1 : 0, transform: heroInView ? 'translateX(0)' : 'translateX(-24px)', transition: 'all 0.8s cubic-bezier(0.4,0,0.2,1) 0.1s' }}>
              {/* Eyebrow */}
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 14px', borderRadius: 100, background: '#eff6ff', border: '1px solid #bfdbfe', marginBottom: 24 }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#2563eb', animation: 'pulse-dot 2s infinite' }} />
                <span style={{ fontSize: 12.5, fontWeight: 700, color: '#1d4ed8', letterSpacing: 0.3 }}>AI-First CRM · Now Available</span>
              </div>

              <h1 style={{ fontSize: 'clamp(32px, 4.4vw, 56px)', fontWeight: 900, color: '#0a0f1e', lineHeight: 1.06, letterSpacing: '-2px', marginBottom: 22 }}>
                AI runs your{' '}
                <span style={{ background: 'linear-gradient(135deg,#2563eb 0%,#4f46e5 50%,#7c3aed 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
                  customer operations.
                </span>
              </h1>

              <p style={{ fontSize: 17, color: '#64748b', lineHeight: 1.7, marginBottom: 32, maxWidth: 480 }}>
                Unify WhatsApp, Instagram, Facebook, Email, and Web Chat into one intelligent inbox. Qualify leads, resolve queries, and close deals — automatically.
              </p>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 32 }}>
                <Link to="/signup" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '14px 28px', background: 'linear-gradient(135deg,#2563eb,#4f46e5)', color: '#fff', borderRadius: 12, fontWeight: 700, fontSize: 15, textDecoration: 'none', boxShadow: '0 8px 28px rgba(37,99,235,0.32)', transition: 'all 0.2s' }}
                  onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 14px 36px rgba(37,99,235,0.44)'; }}
                  onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 8px 28px rgba(37,99,235,0.32)'; }}>
                  Start Free — No Card Needed <ArrowRight size={17} />
                </Link>
                <Link to="/contact" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '14px 24px', background: '#fff', color: '#0f172a', border: '1.5px solid #e2e8f0', borderRadius: 12, fontWeight: 600, fontSize: 15, textDecoration: 'none', transition: 'all 0.2s' }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = '#cbd5e1'; e.currentTarget.style.background = '#f8fafc'; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#fff'; }}>
                  <Play size={14} style={{ fill: '#0f172a' }} /> Watch Demo
                </Link>
              </div>

              {/* Trust signals */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, fontSize: 13, color: '#94a3b8' }}>
                {['Free 30-day trial', 'No credit card required', 'Cancel anytime'].map(t => (
                  <span key={t} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Check size={13} color="#22c55e" strokeWidth={2.5} /> {t}
                  </span>
                ))}
              </div>
            </div>

            {/* Right: Interactive visualization */}
            <div style={{ opacity: heroInView ? 1 : 0, transform: heroInView ? 'translateY(0)' : 'translateY(32px)', transition: 'all 1s cubic-bezier(0.4,0,0.2,1) 0.3s' }}>
              <PipelineVisualization />
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SOCIAL PROOF STRIP
      ══════════════════════════════════════════════════════════════════ */}
      <section style={{ padding: '32px 28px', borderTop: '1px solid #f1f5f9', borderBottom: '1px solid #f1f5f9', background: '#fafbff' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <p style={{ textAlign: 'center', fontSize: 12, fontWeight: 700, color: '#94a3b8', letterSpacing: 1.5, textTransform: 'uppercase', marginBottom: 20 }}>Trusted by customer-obsessed teams</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center', gap: 40 }}>
            {['Meridian Group', 'Nexora Retail', 'PrimeShift', 'Atlus Commerce', 'Vanta Solutions', 'CoreBridge'].map(name => (
              <span key={name} style={{ fontSize: 14, fontWeight: 700, color: '#cbd5e1', letterSpacing: '-0.3px', fontFamily: 'Inter' }}>{name}</span>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          METRICS
      ══════════════════════════════════════════════════════════════════ */}
      <section ref={metricsRef} style={{ padding: '88px 28px', background: '#fff' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 56, opacity: metricsInView ? 1 : 0, transform: metricsInView ? 'translateY(0)' : 'translateY(20px)', transition: 'all 0.7s' }}>
            <h2 style={{ fontSize: 'clamp(24px, 3vw, 38px)', fontWeight: 800, color: '#0a0f1e', marginBottom: 10, letterSpacing: '-1px' }}>Results teams see in 90 days</h2>
            <p style={{ fontSize: 15.5, color: '#64748b', maxWidth: 440, margin: '0 auto' }}>Aggregated across businesses running Pulse Engine for 90+ days.</p>
          </div>
          <div className="lp-metrics-grid">
            {[
              { icon: Bot, value: '70', suffix: '%', label: 'AI Resolution Rate', sub: 'queries resolved without human involvement', color: '#2563eb', bg: '#eff6ff' },
              { icon: Clock, value: '3', suffix: 'x', label: 'Faster Response', sub: 'compared to manual handling workflows', color: '#7c3aed', bg: '#f5f3ff' },
              { icon: TrendingUp, value: '40', suffix: '%', label: 'More Leads Captured', sub: 'from inbound conversations automatically', color: '#059669', bg: '#ecfdf5' },
              { icon: Star, value: '4.9', suffix: '/5', label: 'Average CSAT', sub: 'up from industry average of 3.8', color: '#d97706', bg: '#fffbeb' },
            ].map((m, i) => (
              <div key={m.label} style={{
                background: '#fff', border: '1px solid #f1f5f9', borderRadius: 22, padding: '32px 24px', textAlign: 'center',
                boxShadow: '0 2px 16px rgba(0,0,0,0.04)',
                opacity: metricsInView ? 1 : 0, transform: metricsInView ? 'translateY(0)' : 'translateY(28px)',
                transition: `all 0.7s cubic-bezier(0.4,0,0.2,1) ${i * 100}ms`,
              }}
                onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-6px)'; e.currentTarget.style.boxShadow = '0 20px 48px rgba(0,0,0,0.08)'; }}
                onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 2px 16px rgba(0,0,0,0.04)'; }}>
                <div style={{ width: 48, height: 48, background: m.bg, borderRadius: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
                  <m.icon size={22} color={m.color} />
                </div>
                <p style={{ fontSize: 48, fontWeight: 900, color: m.color, marginBottom: 6, letterSpacing: '-2px', lineHeight: 1 }}>
                  <AnimatedCounter value={m.value} suffix={m.suffix} duration={1800 + i * 200} />
                </p>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#0f172a', marginBottom: 5 }}>{m.label}</p>
                <p style={{ fontSize: 12.5, color: '#94a3b8', lineHeight: 1.5 }}>{m.sub}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          FEATURE TABS SHOWCASE
      ══════════════════════════════════════════════════════════════════ */}
      <section id="features" ref={featuresRef} style={{ padding: '88px 28px', background: '#fafbff', borderTop: '1px solid #f1f5f9' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 56, opacity: featuresInView ? 1 : 0, transform: featuresInView ? 'translateY(0)' : 'translateY(20px)', transition: 'all 0.7s' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 14px', borderRadius: 100, background: '#eff6ff', color: '#2563eb', fontSize: 12.5, fontWeight: 700, marginBottom: 16 }}>
              <Sparkles size={13} />
              Enterprise Capabilities
            </div>
            <h2 style={{ fontSize: 'clamp(24px, 3vw, 40px)', fontWeight: 800, color: '#0a0f1e', marginBottom: 12, letterSpacing: '-1px' }}>Built for scale. Designed for humans.</h2>
            <p style={{ fontSize: 16, color: '#64748b', maxWidth: 520, margin: '0 auto', lineHeight: 1.65 }}>
              Six pillars that turn every message — from any channel — into intelligent, automated, revenue-generating action.
            </p>
          </div>
          <div style={{ opacity: featuresInView ? 1 : 0, transform: featuresInView ? 'translateY(0)' : 'translateY(28px)', transition: 'all 0.8s cubic-bezier(0.4,0,0.2,1) 0.2s' }}>
            {/* Desktop: tabbed layout */}
            {!isTablet ? <FeatureTabs /> : (
              /* Mobile/Tablet: stacked cards */
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {FEATURE_TABS.map(tab => (
                  <div key={tab.id} style={{ background: '#fff', borderRadius: 20, border: '1px solid #f1f5f9', padding: '28px 24px', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
                      <div style={{ width: 40, height: 40, borderRadius: 12, background: tab.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <tab.icon size={20} color={tab.color} />
                      </div>
                      <div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>{tab.title}</div>
                        <div style={{ fontSize: 12.5, color: '#94a3b8' }}>{tab.subtitle}</div>
                      </div>
                    </div>
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 9 }}>
                      {tab.bullets.map(b => (
                        <li key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, fontSize: 13.5, color: '#334155', lineHeight: 1.5 }}>
                          <span style={{ width: 18, height: 18, borderRadius: '50%', background: tab.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 2 }}>
                            <Check size={10} color={tab.color} />
                          </span>
                          {b}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          CHANNELS
      ══════════════════════════════════════════════════════════════════ */}
      <section id="channels" style={{ padding: '88px 28px', background: '#fff' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 52 }}>
            <h2 style={{ fontSize: 'clamp(24px, 3vw, 40px)', fontWeight: 800, color: '#0a0f1e', marginBottom: 12, letterSpacing: '-1px' }}>One platform. Every channel.</h2>
            <p style={{ fontSize: 16, color: '#64748b', maxWidth: 440, margin: '0 auto' }}>Your customers are everywhere. Meet them where they are — without switching tabs.</p>
          </div>
          <div className="lp-channels-grid">
            {[
              { name: 'WhatsApp', sub: 'Business API · 2B+ users', borderColor: '#bbf7d0', shadowColor: 'rgba(37,211,102,0.08)', icon: (
                <div style={{ width: 60, height: 60, background: '#25D366', borderRadius: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg viewBox="0 0 24 24" style={{ width: 32, height: 32, fill: '#fff' }}><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
                </div>
              )},
              { name: 'Instagram', sub: 'DM & story replies', borderColor: '#fbcfe8', shadowColor: 'rgba(214,36,159,0.07)', icon: (
                <div style={{ width: 60, height: 60, borderRadius: 18, background: 'radial-gradient(circle at 30% 107%, #fdf497 0%, #fd5949 45%, #d6249f 60%, #285AEB 90%)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg viewBox="0 0 24 24" style={{ width: 32, height: 32, fill: '#fff' }}><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/></svg>
                </div>
              )},
              { name: 'Facebook', sub: 'Messenger & Pages', borderColor: '#bfdbfe', shadowColor: 'rgba(24,119,242,0.07)', icon: (
                <div style={{ width: 60, height: 60, background: '#1877F2', borderRadius: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg viewBox="0 0 24 24" style={{ width: 32, height: 32, fill: '#fff' }}><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
                </div>
              )},
              { name: 'Email', sub: 'IMAP · SMTP · Brevo', borderColor: '#bae6fd', shadowColor: 'rgba(2,132,199,0.07)', icon: (
                <div style={{ width: 60, height: 60, background: 'linear-gradient(135deg,#0ea5e9,#0284c7)', borderRadius: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Mail size={30} color="#fff" />
                </div>
              )},
              { name: 'Web Chat', sub: 'Embeddable widget', borderColor: '#e2e8f0', shadowColor: 'rgba(100,116,139,0.07)', icon: (
                <div style={{ width: 60, height: 60, background: '#1e293b', borderRadius: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <MessageSquare size={28} color="#fff" />
                </div>
              )},
            ].map(ch => (
              <div key={ch.name} className="ch-card-hover" style={{ background: '#fff', border: `1px solid ${ch.borderColor}`, borderRadius: 22, padding: '28px 16px', textAlign: 'center', cursor: 'pointer', boxShadow: `0 2px 14px ${ch.shadowColor}` }}>
                <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>{ch.icon}</div>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#0f172a', marginBottom: 4 }}>{ch.name}</p>
                <p style={{ fontSize: 12, color: '#94a3b8' }}>{ch.sub}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          GLOBE SECTION
      ══════════════════════════════════════════════════════════════════ */}
      <section ref={globeRef} style={{ padding: '88px 28px', background: '#0a0f1e', overflow: 'hidden' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div className="lp-globe-wrap">
            {/* Left: Copy */}
            <div style={{ opacity: globeInView ? 1 : 0, transform: globeInView ? 'translateX(0)' : 'translateX(-24px)', transition: 'all 0.9s cubic-bezier(0.4,0,0.2,1) 0.1s' }}>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 14px', borderRadius: 100, background: 'rgba(37,99,235,0.15)', border: '1px solid rgba(37,99,235,0.25)', color: '#60a5fa', fontSize: 12.5, fontWeight: 700, marginBottom: 24 }}>
                <Globe size={13} /> Global Reach
              </div>
              <h2 style={{ fontSize: 'clamp(26px, 3.2vw, 42px)', fontWeight: 800, color: '#fff', marginBottom: 18, letterSpacing: '-1px', lineHeight: 1.12 }}>
                Serve customers across{' '}
                <span style={{ background: 'linear-gradient(135deg,#60a5fa,#a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
                  150+ countries.
                </span>
              </h2>
              <p style={{ fontSize: 16, color: 'rgba(255,255,255,0.6)', lineHeight: 1.7, marginBottom: 36, maxWidth: 400 }}>
                Pulse Engine routes conversations intelligently across time zones, languages, and channels. Your AI agents never sleep.
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {[
                  { icon: Activity, label: '99.9% uptime SLA', sub: 'Enterprise-grade reliability' },
                  { icon: Shield, label: 'GDPR & SOC2 compliant', sub: 'Your data stays yours' },
                  { icon: Zap, label: 'Sub-second AI responses', sub: 'WebSocket-powered infrastructure' },
                ].map(item => (
                  <div key={item.label} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                    <div style={{ width: 38, height: 38, borderRadius: 11, background: 'rgba(37,99,235,0.15)', border: '1px solid rgba(37,99,235,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      <item.icon size={17} color="#60a5fa" />
                    </div>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 700, color: '#f1f5f9' }}>{item.label}</div>
                      <div style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.4)' }}>{item.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Right: Globe */}
            <div style={{ opacity: globeInView ? 1 : 0, transform: globeInView ? 'scale(1)' : 'scale(0.9)', transition: 'all 1s cubic-bezier(0.4,0,0.2,1) 0.2s' }}>
              <GlobeVisualization />
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          ROTATING TEXT / BRAND STATEMENT
      ══════════════════════════════════════════════════════════════════ */}
      <section style={{ padding: '100px 28px', background: '#fff', textAlign: 'center' }}>
        <div style={{ maxWidth: 800, margin: '0 auto' }}>
          <p style={{ fontSize: 13, fontWeight: 700, color: '#94a3b8', letterSpacing: 2, textTransform: 'uppercase', marginBottom: 28 }}>What Pulse Engine powers</p>
          <div style={{ fontSize: 'clamp(28px, 4.5vw, 54px)', fontWeight: 900, color: '#0a0f1e', lineHeight: 1.12, letterSpacing: '-1.5px' }}>
            The future of{' '}
            <RotatingWord words={['Sales Automation', 'Customer Support', 'Lead Nurturing', 'Revenue Growth', 'Team Efficiency']} />
          </div>
          <p style={{ fontSize: 17, color: '#64748b', marginTop: 24, lineHeight: 1.7, maxWidth: 560, margin: '24px auto 0' }}>
            One platform that learns from every conversation, automates the repetitive, and puts your team in control of the moments that matter.
          </p>
          <Link to="/signup" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 36, padding: '13px 28px', background: '#0a0f1e', color: '#fff', borderRadius: 12, fontWeight: 700, fontSize: 14.5, textDecoration: 'none', transition: 'all 0.2s' }}
            onMouseEnter={e => { e.currentTarget.style.background = '#1e293b'; e.currentTarget.style.transform = 'translateY(-2px)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = '#0a0f1e'; e.currentTarget.style.transform = 'translateY(0)'; }}>
            See it in action <ArrowRight size={16} />
          </Link>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          PRICING
      ══════════════════════════════════════════════════════════════════ */}
      <section id="pricing" ref={pricingRef} style={{ padding: '88px 28px', background: '#fafbff', borderTop: '1px solid #f1f5f9' }}>
        <div style={{ maxWidth: 1180, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 56, opacity: pricingInView ? 1 : 0, transform: pricingInView ? 'translateY(0)' : 'translateY(20px)', transition: 'all 0.7s' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 14px', borderRadius: 100, background: '#ecfdf5', color: '#059669', fontSize: 12.5, fontWeight: 700, marginBottom: 16 }}>
              <Check size={13} strokeWidth={2.5} /> 30-Day Free Trial on All Plans
            </div>
            <h2 style={{ fontSize: 'clamp(24px, 3vw, 40px)', fontWeight: 800, color: '#0a0f1e', marginBottom: 12, letterSpacing: '-1px' }}>Pricing that scales with you</h2>
            <p style={{ fontSize: 16, color: '#64748b', maxWidth: 480, margin: '0 auto' }}>
              Start free forever, upgrade when you're ready. No contracts, cancel anytime.
            </p>
          </div>

          <div className="lp-pricing-grid">
            {pricingPlans.map((plan, i) => {
              const isEnterprise = plan.code === 'enterprise';
              const isPro = plan.code === 'pro';
              return (
                <div key={plan.code} style={{
                  position: 'relative',
                  borderRadius: 24,
                  padding: isPro ? '40px 32px' : '36px 28px',
                  background: isEnterprise ? 'linear-gradient(160deg, #0a0f1e 0%, #0f172a 40%, #1e1b4b 100%)' : isPro ? '#fff' : '#fff',
                  border: isEnterprise ? 'none' : isPro ? '2px solid #2563eb' : '1.5px solid #e8ecf0',
                  boxShadow: isEnterprise ? '0 32px 80px rgba(0,0,0,0.25)' : isPro ? '0 28px 64px rgba(37,99,235,0.18)' : '0 2px 16px rgba(0,0,0,0.04)',
                  transform: isPro ? 'scale(1.04)' : 'scale(1)',
                  opacity: pricingInView ? 1 : 0,
                  transition: `all 0.7s cubic-bezier(0.4,0,0.2,1) ${i * 130}ms`,
                }}>
                  {/* Enterprise shimmer border */}
                  {isEnterprise && (
                    <div style={{ position: 'absolute', inset: 0, borderRadius: 24, border: '1px solid rgba(255,255,255,0.08)', pointerEvents: 'none' }} />
                  )}

                  {isPro && (
                    <div style={{ position: 'absolute', top: -15, left: '50%', transform: 'translateX(-50%)', padding: '5px 18px', background: 'linear-gradient(135deg,#2563eb,#4f46e5)', color: '#fff', fontSize: 11, fontWeight: 800, borderRadius: 100, whiteSpace: 'nowrap', letterSpacing: 0.5, boxShadow: '0 4px 12px rgba(37,99,235,0.4)' }}>
                      ✦ MOST POPULAR
                    </div>
                  )}

                  {isEnterprise && (
                    <div style={{ position: 'absolute', top: -15, left: '50%', transform: 'translateX(-50%)', padding: '5px 18px', background: 'linear-gradient(135deg,#7c3aed,#4f46e5)', color: '#fff', fontSize: 11, fontWeight: 800, borderRadius: 100, whiteSpace: 'nowrap', letterSpacing: 0.5 }}>
                      ✦ ENTERPRISE
                    </div>
                  )}

                  <div style={{ marginBottom: 20 }}>
                    <h3 style={{ fontSize: 20, fontWeight: 800, color: isEnterprise ? '#fff' : '#0a0f1e', marginBottom: 6 }}>{plan.name}</h3>
                    <p style={{ fontSize: 13, color: isEnterprise ? 'rgba(255,255,255,0.5)' : '#94a3b8', lineHeight: 1.5 }}>{plan.description}</p>
                  </div>

                  <div style={{ marginBottom: 28, display: 'flex', alignItems: 'flex-end', gap: 4 }}>
                    <span style={{ fontSize: 50, fontWeight: 900, color: isEnterprise ? '#fff' : isPro ? '#2563eb' : '#0a0f1e', letterSpacing: '-2px', lineHeight: 1 }}>{plan.price}</span>
                    {plan.period && <span style={{ fontSize: 14, color: isEnterprise ? 'rgba(255,255,255,0.45)' : '#94a3b8', paddingBottom: 6 }}>{plan.period}</span>}
                  </div>

                  <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 28px', display: 'flex', flexDirection: 'column', gap: 9 }}>
                    {plan.features.map((f, fi) => (
                      <li key={fi} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 13, lineHeight: 1.5, color: isEnterprise ? 'rgba(255,255,255,0.75)' : '#475569' }}>
                        <Check size={14} color={isEnterprise ? '#a78bfa' : isPro ? '#2563eb' : '#22c55e'} style={{ flexShrink: 0, marginTop: 2 }} /> {f}
                      </li>
                    ))}
                  </ul>

                  <Link to={plan.href} style={{
                    display: 'block', textAlign: 'center', padding: '14px 0',
                    background: isEnterprise ? 'linear-gradient(135deg,#6d28d9,#4f46e5)' : isPro ? 'linear-gradient(135deg,#2563eb,#4f46e5)' : '#fff',
                    color: isEnterprise ? '#fff' : isPro ? '#fff' : '#0a0f1e',
                    border: isEnterprise ? 'none' : isPro ? 'none' : '1.5px solid #e2e8f0',
                    borderRadius: 12, fontWeight: 700, fontSize: 14, textDecoration: 'none',
                    boxShadow: isEnterprise ? '0 4px 20px rgba(109,40,217,0.4)' : isPro ? '0 4px 16px rgba(37,99,235,0.32)' : 'none',
                    transition: 'all 0.2s',
                  }}
                    onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.opacity = '0.9'; }}
                    onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.opacity = '1'; }}>
                    {plan.cta}
                  </Link>
                </div>
              );
            })}
          </div>

          {/* Comparison note */}
          <p style={{ textAlign: 'center', marginTop: 36, fontSize: 13, color: '#94a3b8' }}>
            All plans include a 30-day free trial · No credit card required · Cancel anytime
          </p>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          FAQ
      ══════════════════════════════════════════════════════════════════ */}
      <section id="faq" style={{ padding: '88px 28px', background: '#0a0f1e' }}>
        <div style={{ maxWidth: 720, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 48 }}>
            <h2 style={{ fontSize: 'clamp(24px, 3vw, 38px)', fontWeight: 800, color: '#fff', marginBottom: 10, letterSpacing: '-1px' }}>Frequently Asked Questions</h2>
            <p style={{ fontSize: 15.5, color: 'rgba(255,255,255,0.5)' }}>Everything you need to know before getting started.</p>
          </div>
          {faqs.map(f => <FaqItem key={f.q} q={f.q} a={f.a} dark />)}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          FINAL CTA
      ══════════════════════════════════════════════════════════════════ */}
      <section ref={ctaRef} style={{ position: 'relative', padding: '100px 28px', background: 'linear-gradient(135deg, #1e3a8a 0%, #1d4ed8 40%, #4f46e5 70%, #6d28d9 100%)', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', top: -100, right: -80, width: 400, height: 400, background: 'rgba(255,255,255,0.06)', borderRadius: '50%' }} />
          <div style={{ position: 'absolute', bottom: -120, left: -60, width: 360, height: 360, background: 'rgba(255,255,255,0.04)', borderRadius: '50%' }} />
          <div style={{ position: 'absolute', top: '40%', left: '50%', transform: 'translate(-50%,-50%)', width: 600, height: 600, background: 'radial-gradient(circle, rgba(255,255,255,0.04) 0%, transparent 70%)' }} />
        </div>
        <div style={{ maxWidth: 680, margin: '0 auto', textAlign: 'center', position: 'relative', zIndex: 1 }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 14px', borderRadius: 100, background: 'rgba(255,255,255,0.12)', color: 'rgba(255,255,255,0.9)', fontSize: 12.5, fontWeight: 700, marginBottom: 24 }}>
            <Sparkles size={13} /> Launch-ready in minutes
          </div>
          <h2 style={{ fontSize: 'clamp(30px, 5vw, 52px)', fontWeight: 900, color: '#fff', marginBottom: 16, letterSpacing: '-1.5px', lineHeight: 1.08, opacity: ctaInView ? 1 : 0, transform: ctaInView ? 'translateY(0)' : 'translateY(20px)', transition: 'all 0.8s ease 0.1s' }}>
            The inbox that runs your business.
          </h2>
          <p style={{ fontSize: 17, color: 'rgba(255,255,255,0.72)', marginBottom: 40, maxWidth: 480, margin: '0 auto 40px', lineHeight: 1.7, opacity: ctaInView ? 1 : 0, transition: 'all 0.8s ease 0.2s' }}>
            Join thousands of teams using Pulse Engine to turn conversations into customers — automatically.
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 14, opacity: ctaInView ? 1 : 0, transition: 'all 0.8s ease 0.3s' }}>
            <Link to="/signup" style={{ display: 'inline-flex', alignItems: 'center', gap: 9, padding: '15px 32px', background: '#fff', color: '#2563eb', borderRadius: 13, fontWeight: 800, fontSize: 15, textDecoration: 'none', boxShadow: '0 8px 28px rgba(0,0,0,0.2)', transition: 'all 0.2s' }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 14px 36px rgba(0,0,0,0.28)'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 8px 28px rgba(0,0,0,0.2)'; }}>
              Start Free Trial <ArrowRight size={17} />
            </Link>
            <Link to="/contact" style={{ display: 'inline-flex', alignItems: 'center', gap: 9, padding: '15px 28px', border: '1.5px solid rgba(255,255,255,0.3)', color: '#fff', borderRadius: 13, fontWeight: 700, fontSize: 15, textDecoration: 'none', transition: 'all 0.2s' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.1)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.5)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}>
              Schedule a Demo
            </Link>
          </div>
          <p style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.38)', marginTop: 24 }}>No credit card · 30-day free trial · Cancel anytime</p>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          FOOTER
      ══════════════════════════════════════════════════════════════════ */}
      <footer style={{ padding: '64px 28px 36px', background: '#060b14', color: '#64748b' }}>
        <div style={{ maxWidth: 1280, margin: '0 auto' }}>
          <div className="lp-footer-grid" style={{ marginBottom: 52 }}>
            <div>
              <div style={{ marginBottom: 18 }}>
                <PlatformLogo textColor="#f1f5f9" imageWidth={34} fontSize={17} fontWeight={800} />
              </div>
              <p style={{ fontSize: 13.5, lineHeight: 1.7, maxWidth: 240, color: '#475569' }}>AI-powered customer engagement for modern teams. Unify every channel in one intelligent inbox.</p>
              <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
                {['TW', 'LI', 'GH'].map(s => (
                  <a key={s} href="#" style={{ width: 34, height: 34, borderRadius: 9, background: '#0f172a', border: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: '#475569', textDecoration: 'none', transition: 'all 0.2s' }}
                    onMouseEnter={e => { e.currentTarget.style.background = '#1e293b'; e.currentTarget.style.color = '#94a3b8'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = '#0f172a'; e.currentTarget.style.color = '#475569'; }}>
                    {s}
                  </a>
                ))}
              </div>
            </div>
            {[
              { title: 'Product', links: [['Features', '#features'], ['Channels', '#channels'], ['Pricing', '#pricing'], ['FAQ', '#faq']] },
              { title: 'Resources', links: [['Documentation', '#'], ['Blog', '#'], ['Help Center', '#faq'], ['Status Page', '#']] },
              { title: 'Company', links: [['Privacy Policy', '/privacy'], ['Terms of Service', '/terms'], ['Contact Us', '/contact'], ['Sign In', '/signin']] },
            ].map(col => (
              <div key={col.title}>
                <p style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 1.2, marginBottom: 18 }}>{col.title}</p>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 11 }}>
                  {col.links.map(([label, href]) => (
                    <li key={label}><a href={href} style={{ fontSize: 13.5, color: '#475569', textDecoration: 'none', transition: 'color 0.2s' }} onMouseEnter={e => e.target.style.color = '#94a3b8'} onMouseLeave={e => e.target.style.color = '#475569'}>{label}</a></li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div style={{ borderTop: '1px solid #0f172a', paddingTop: 28, display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
            <p style={{ fontSize: 13, color: '#334155' }}>© 2026 Pulse Engine. All rights reserved.</p>
            <p style={{ fontSize: 12.5, color: '#1e293b' }}>Built to power 100,000+ customer conversations.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
