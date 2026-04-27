import { Link } from 'react-router-dom';
import { useState, useEffect, useRef } from 'react';
import PlatformLogo from '@/components/PlatformLogo';
import { PUBLIC_PRICING_PLANS } from '@/data/publicPricingPlans';
import {
  Check,
  ArrowLeft,
  Shield,
  Star,
  ChevronRight,
  Zap,
} from 'lucide-react';

/* ── Animated number ── */
function Counter({ to, suffix = '', duration = 1400 }) {
  const [val, setVal] = useState(0);
  const ref = useRef();
  const started = useRef(false);
  useEffect(() => {
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting && !started.current) {
        started.current = true;
        const num = parseFloat(to);
        const inc = num / (duration / 16);
        let cur = 0;
        const t = setInterval(() => {
          cur += inc;
          if (cur >= num) { setVal(num); clearInterval(t); }
          else setVal(Number.isInteger(num) ? Math.floor(cur) : Math.round(cur * 10) / 10);
        }, 16);
      }
    }, { threshold: 0.3 });
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, [to, duration]);
  return <span ref={ref}>{val}{suffix}</span>;
}

const PLANS = PUBLIC_PRICING_PLANS.map((p) => {
  const accent =
    p.code === 'pro' ? '#2563eb' : p.code === 'enterprise' ? '#7c3aed' : '#64748b';
  const glow =
    p.code === 'pro'
      ? 'rgba(37,99,235,0.25)'
      : p.code === 'enterprise'
        ? 'rgba(124,58,237,0.15)'
        : 'rgba(71,85,105,0.12)';
  const border =
    p.code === 'pro' ? '#2563eb' : p.code === 'enterprise' ? '#ddd6fe' : '#e2e8f0';
  return {
    id: p.code,
    contactSales: Boolean(p.contactSales),
    name: p.name,
    tagline: p.description,
    price: p.cardPrice,
    period: p.cardPeriod,
    accentColor: accent,
    glowColor: glow,
    borderColor: border,
    badge: p.code === 'pro' ? 'Most Popular' : null,
    features: p.features.map((text) => ({ text })),
    cta: p.contactSales ? 'Contact Sales' : 'Continue to Secure Checkout',
    ctaStyle: p.contactSales ? 'outline-muted' : p.code === 'pro' ? 'solid' : 'outline-accent',
  };
});

const TRUST = [
  { value: '10', suffix: 'k+', label: 'Teams worldwide' },
  { value: '4.9', suffix: '/5', label: 'Average rating' },
  { value: '99.9', suffix: '%', label: 'Uptime SLA' },
  { value: '70', suffix: '%', label: 'AI resolution rate' },
];

export default function PricingPage() {
  const [hovered, setHovered] = useState(null);
  const [billing, setBilling] = useState('monthly'); // monthly | annual
  const annualDiscount = 0.2;

  const displayPrice = (plan) => {
    if (plan.contactSales || plan.price === 'Custom') return plan.price;
    const num = parseInt(plan.price.replace('$', ''), 10);
    if (Number.isNaN(num)) return plan.price;
    if (billing === 'annual') return `$${Math.round(num * (1 - annualDiscount))}`;
    return plan.price;
  };

  return (
    <div className="pricing-page-root" style={{ minHeight: '100vh', background: '#fafafa', fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif', position: 'relative', overflow: 'hidden' }}>
      <style>{`
        .pricing-page-root,
        .pricing-page-root * {
          font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
        }
        .pricing-cards-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 22px;
          align-items: start;
          max-width: 1180px;
          margin: 48px auto 0;
          padding: 0 24px;
        }
        @media (max-width: 960px) {
          .pricing-cards-grid { grid-template-columns: 1fr; }
        }
      `}</style>

      {/* ── Background mesh ── */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
        <div style={{ position: 'absolute', top: -200, right: -200, width: 700, height: 700, background: 'radial-gradient(circle, rgba(37,99,235,0.06) 0%, transparent 65%)', borderRadius: '50%' }} />
        <div style={{ position: 'absolute', bottom: -100, left: -100, width: 500, height: 500, background: 'radial-gradient(circle, rgba(124,58,237,0.06) 0%, transparent 65%)', borderRadius: '50%' }} />
        {/* subtle grid */}
        <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.3 }}>
          <defs>
            <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#e2e8f0" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#grid)" />
        </svg>
      </div>

      <div style={{ position: 'relative', zIndex: 1 }}>

        {/* ── Top bar ── */}
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '20px 32px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          {/* Logo */}
          <PlatformLogo fontWeight={800} fontSize={18} />
          {/* Back link */}
          <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5, color: '#64748b', textDecoration: 'none', padding: '7px 14px', borderRadius: 9, border: '1px solid #e2e8f0', background: '#fff', transition: 'all 0.2s', fontWeight: 500 }}
            onMouseEnter={e => { e.currentTarget.style.color = '#0f172a'; e.currentTarget.style.borderColor = '#cbd5e1'; }}
            onMouseLeave={e => { e.currentTarget.style.color = '#64748b'; e.currentTarget.style.borderColor = '#e2e8f0'; }}>
            <ArrowLeft size={14} /> Back to home
          </Link>
        </div>

        {/* ── Hero header ── */}
        <div style={{ textAlign: 'center', padding: '48px 24px 20px', maxWidth: 680, margin: '0 auto' }}>
          <h1 style={{ fontFamily: 'Syne', fontSize: 'clamp(36px,5vw,56px)', fontWeight: 800, color: '#0f172a', lineHeight: 1.1, letterSpacing: '-1.5px', marginBottom: 14 }}>
            Find the plan that<br />
            <span style={{ background: 'linear-gradient(135deg, #2563eb, #6366f1)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>fits your team.</span>
          </h1>
          <p style={{ fontSize: 16, color: '#64748b', lineHeight: 1.65, maxWidth: 560, margin: '0 auto 28px' }}>
            Pro ($29/mo), Enterprise ($99/mo), and Custom (sales-led) use the same product pillars—limits and support scale by tier.
          </p>

          {/* Billing toggle */}
          <div style={{ display: 'inline-flex', alignItems: 'center', background: '#fff', border: '1px solid #e2e8f0', borderRadius: 12, padding: 4, gap: 2, boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
            {['monthly', 'annual'].map(b => (
              <button key={b} onClick={() => setBilling(b)}
                style={{ padding: '8px 18px', borderRadius: 9, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600, fontFamily: 'DM Sans', transition: 'all 0.2s',
                  background: billing === b ? 'linear-gradient(135deg,#2563eb,#6366f1)' : 'transparent',
                  color: billing === b ? '#fff' : '#64748b',
                  boxShadow: billing === b ? '0 2px 10px rgba(37,99,235,0.3)' : 'none',
                }}>
                {b === 'monthly' ? 'Monthly' : 'Annual'}
                {b === 'annual' && <span style={{ marginLeft: 6, fontSize: 10, background: '#dcfce7', color: '#16a34a', padding: '1px 6px', borderRadius: 6, fontWeight: 700 }}>–20%</span>}
              </button>
            ))}
          </div>
        </div>

        {/* ── Pricing cards ── */}
        <div className="pricing-cards-grid">
          {PLANS.map((plan, i) => {
            const isPopular = plan.badge === 'Most Popular';
            const isHovered = hovered === plan.id;

            return (
              <div key={plan.id}
                onMouseEnter={() => setHovered(plan.id)}
                onMouseLeave={() => setHovered(null)}
                style={{
                  position: 'relative',
                  borderRadius: 22,
                  padding: isPopular ? '36px 30px' : '30px 28px',
                  background: isPopular
                    ? 'linear-gradient(155deg, #1e3a8a 0%, #2563eb 45%, #4338ca 100%)'
                    : '#fff',
                  border: `1.5px solid ${isHovered && !isPopular ? plan.accentColor : (isPopular ? 'transparent' : plan.borderColor)}`,
                  boxShadow: isHovered
                    ? `0 24px 60px ${plan.glowColor}, 0 2px 20px rgba(0,0,0,0.06)`
                    : isPopular
                      ? '0 20px 60px rgba(37,99,235,0.3)'
                      : '0 2px 16px rgba(0,0,0,0.05)',
                  transform: isPopular
                    ? isHovered ? 'scale(1.065) translateY(-4px)' : 'scale(1.055)'
                    : isHovered ? 'translateY(-6px)' : 'translateY(0)',
                  transition: 'all 0.35s cubic-bezier(0.34,1.56,0.64,1)',
                  cursor: 'default',
                  overflow: 'hidden',
                }}>

                {/* Decorative shape for popular card */}
                {isPopular && (
                  <div style={{ position: 'absolute', top: -60, right: -60, width: 180, height: 180, background: 'rgba(255,255,255,0.06)', borderRadius: '50%', pointerEvents: 'none' }} />
                )}
                {isPopular && (
                  <div style={{ position: 'absolute', bottom: -40, left: -40, width: 140, height: 140, background: 'rgba(255,255,255,0.04)', borderRadius: '50%', pointerEvents: 'none' }} />
                )}

                {/* Badge */}
                {plan.badge && (
                  <div style={{ position: 'absolute', top: -1, left: '50%', transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 5, padding: '5px 16px', background: '#fbbf24', color: '#78350f', fontSize: 11, fontWeight: 800, borderRadius: '0 0 12px 12px', whiteSpace: 'nowrap', letterSpacing: 0.3 }}>
                    <Star size={10} style={{ fill: '#78350f' }} /> {plan.badge}
                  </div>
                )}

                {/* Plan name & tagline */}
                <div style={{ marginBottom: 20, marginTop: plan.badge ? 16 : 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <h3 style={{ fontFamily: 'Syne', fontSize: 22, fontWeight: 800, color: isPopular ? '#fff' : '#0f172a', letterSpacing: '-0.3px', margin: 0 }}>{plan.name}</h3>
                  </div>
                  <p style={{ fontSize: 13, color: isPopular ? 'rgba(255,255,255,0.65)' : '#94a3b8', margin: 0 }}>{plan.tagline}</p>
                </div>

                {/* Price */}
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, marginBottom: 6 }}>
                  <span style={{ fontFamily: 'Syne', fontSize: 48, fontWeight: 800, color: isPopular ? '#fff' : '#0f172a', lineHeight: 1, letterSpacing: '-2px' }}>{displayPrice(plan)}</span>
                  {plan.period && (
                    <span style={{ fontSize: 14, color: isPopular ? 'rgba(255,255,255,0.55)' : '#94a3b8', marginBottom: 8 }}>{plan.period}</span>
                  )}
                </div>
                {billing === 'annual' && !plan.contactSales && plan.period && (
                  <p style={{ fontSize: 11.5, color: isPopular ? 'rgba(255,255,255,0.55)' : '#94a3b8', marginBottom: 0 }}>
                    Billed annually · <span style={{ color: isPopular ? '#86efac' : '#16a34a', fontWeight: 600 }}>Save 20%</span>
                  </p>
                )}

                {/* Divider */}
                <div style={{ height: 1, background: isPopular ? 'rgba(255,255,255,0.15)' : '#f1f5f9', margin: '20px 0' }} />

                {/* Features */}
                <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 24px', display: 'flex', flexDirection: 'column', gap: 9 }}>
                  {plan.features.map((feat, fi) => (
                    <li key={fi} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                      <div style={{ width: 20, height: 20, borderRadius: 6, background: isPopular ? 'rgba(255,255,255,0.2)' : `${plan.accentColor}18`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1 }}>
                        <Check size={11} color={isPopular ? '#fff' : plan.accentColor} strokeWidth={2.5} />
                      </div>
                      <span style={{ fontSize: 12.75, color: isPopular ? 'rgba(255,255,255,0.88)' : '#334155', lineHeight: 1.42, fontWeight: 400 }}>
                        {feat.text}
                      </span>
                    </li>
                  ))}
                </ul>

                {/* CTA Button */}
                <Link
                  to={plan.contactSales ? '/contact' : `/signup?plan=${plan.id}`}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
                    width: '100%', padding: '13px 0', borderRadius: 12,
                    fontSize: 14, fontWeight: 700, textDecoration: 'none', fontFamily: 'DM Sans',
                    transition: 'all 0.25s',
                    ...(plan.ctaStyle === 'solid'
                      ? { background: '#fff', color: '#2563eb', boxShadow: '0 4px 16px rgba(0,0,0,0.12)' }
                      : plan.ctaStyle === 'outline-accent'
                        ? { background: 'transparent', color: plan.accentColor, border: `1.5px solid ${plan.accentColor}40`, backgroundColor: `${plan.accentColor}08` }
                        : { background: 'transparent', color: '#334155', border: '1.5px solid #e2e8f0', backgroundColor: '#f8fafc' }
                    ),
                  }}
                  onMouseEnter={e => {
                    if (plan.ctaStyle === 'solid') { e.currentTarget.style.background = '#f0f4ff'; e.currentTarget.style.transform = 'translateY(-1px)'; }
                    else if (plan.ctaStyle === 'outline-accent') { e.currentTarget.style.backgroundColor = `${plan.accentColor}14`; e.currentTarget.style.transform = 'translateY(-1px)'; }
                    else { e.currentTarget.style.borderColor = '#cbd5e1'; e.currentTarget.style.background = '#fff'; e.currentTarget.style.transform = 'translateY(-1px)'; }
                  }}
                  onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; }}>
                  {plan.cta} <ChevronRight size={15} />
                </Link>

              </div>
            );
          })}
        </div>

        {/* ── Trust strip ── */}
        <div style={{ maxWidth: 860, margin: '56px auto 0', padding: '0 24px' }}>
          <div style={{ background: '#fff', border: '1px solid #f1f5f9', borderRadius: 20, padding: '28px 36px', display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, boxShadow: '0 2px 16px rgba(0,0,0,0.04)' }}>
            {TRUST.map((t, i) => (
              <div key={i} style={{ textAlign: 'center' }}>
                <p style={{ fontFamily: 'Syne', fontSize: 32, fontWeight: 800, color: '#2563eb', margin: '0 0 4px', letterSpacing: '-1px' }}>
                  <Counter to={t.value} suffix={t.suffix} duration={1400 + i * 100} />
                </p>
                <p style={{ fontSize: 12.5, color: '#94a3b8', margin: 0, fontWeight: 500 }}>{t.label}</p>
              </div>
            ))}
          </div>
        </div>

        {/* ── Guarantee row ── */}
        <div style={{ maxWidth: 860, margin: '20px auto 0', padding: '0 24px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
            {[
              { icon: Shield, title: 'No credit card needed', desc: 'Start for free, upgrade anytime', color: '#2563eb', bg: '#eff6ff' },
              { icon: Zap,    title: 'Setup in 5 minutes',   desc: 'Connect your first channel instantly', color: '#7c3aed', bg: '#f5f3ff' },
              { icon: Star,   title: '14-day free trial',    desc: 'Full access, cancel anytime', color: '#059669', bg: '#ecfdf5' },
            ].map((g, i) => (
              <div key={i} style={{ background: '#fff', border: '1px solid #f1f5f9', borderRadius: 16, padding: '18px 20px', display: 'flex', alignItems: 'flex-start', gap: 12, boxShadow: '0 1px 8px rgba(0,0,0,0.04)' }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: g.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <g.icon size={17} color={g.color} />
                </div>
                <div>
                  <p style={{ fontSize: 13.5, fontWeight: 700, color: '#0f172a', margin: '0 0 3px', fontFamily: 'Syne' }}>{g.title}</p>
                  <p style={{ fontSize: 12, color: '#94a3b8', margin: 0 }}>{g.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Footer note ── */}
        <div style={{ textAlign: 'center', padding: '40px 24px 60px' }}>
          <p style={{ fontSize: 13.5, color: '#94a3b8' }}>
            Need a custom plan?{' '}
            <Link to="/contact" style={{ color: '#2563eb', fontWeight: 600, textDecoration: 'none' }}>Talk to our sales team →</Link>
          </p>
        </div>

      </div>
    </div>
  );
}
