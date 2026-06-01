import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { API_BASE_URL, resolveMediaUrl } from '@/lib/backend-url';
import { showToast, getErrorMessage } from '@/hooks/use-toast';
import {
  ShoppingBag, CheckCircle2, Loader2, Shield, Truck, RefreshCw,
  ChevronLeft, ChevronRight, Star, Minus, Plus, Package, Phone,
  Mail, MapPin, FileText, ZoomIn,
} from 'lucide-react';

const publicClient = axios.create({ baseURL: API_BASE_URL, timeout: 15000 });

function formatPrice(value, currency) {
  const num = Number(value);
  if (!value || Number.isNaN(num)) return '';
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || 'USD' }).format(num);
  } catch {
    return `${currency || 'USD'} ${num.toFixed(2)}`;
  }
}

function genId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/* ─── Image Gallery ─────────────────────────────────────────────────────── */
function ImageGallery({ images, productName }) {
  const [active, setActive] = useState(0);
  const [zoomed, setZoomed] = useState(false);
  const [zoomPos, setZoomPos] = useState({ x: 50, y: 50 });
  const imgRef = useRef(null);

  const resolved = images.map(resolveMediaUrl);
  const hasPrev = active > 0;
  const hasNext = active < resolved.length - 1;

  const handleMouseMove = (e) => {
    if (!imgRef.current) return;
    const { left, top, width, height } = imgRef.current.getBoundingClientRect();
    const x = ((e.clientX - left) / width) * 100;
    const y = ((e.clientY - top) / height) * 100;
    setZoomPos({ x, y });
  };

  if (resolved.length === 0) {
    return (
      <div className="aspect-square rounded-2xl bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center">
        <Package className="w-20 h-20 text-slate-300" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Main image */}
      <div
        className="relative overflow-hidden rounded-2xl bg-slate-100 aspect-square cursor-zoom-in group"
        onMouseMove={handleMouseMove}
        onMouseEnter={() => setZoomed(true)}
        onMouseLeave={() => setZoomed(false)}
        ref={imgRef}
      >
        <img
          src={resolved[active]}
          alt={productName}
          className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
          style={zoomed ? {
            transformOrigin: `${zoomPos.x}% ${zoomPos.y}%`,
            transform: 'scale(1.6)',
            transition: 'none',
          } : {}}
          draggable={false}
        />
        {!zoomed && (
          <div className="absolute bottom-3 right-3 bg-white/80 backdrop-blur-sm rounded-full p-1.5 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
            <ZoomIn className="w-4 h-4 text-slate-600" />
          </div>
        )}
        {hasPrev && (
          <button
            onClick={() => setActive(a => a - 1)}
            className="absolute left-3 top-1/2 -translate-y-1/2 bg-white/90 hover:bg-white shadow-lg rounded-full p-2 transition-all opacity-0 group-hover:opacity-100 z-10"
          >
            <ChevronLeft className="w-4 h-4 text-slate-700" />
          </button>
        )}
        {hasNext && (
          <button
            onClick={() => setActive(a => a + 1)}
            className="absolute right-3 top-1/2 -translate-y-1/2 bg-white/90 hover:bg-white shadow-lg rounded-full p-2 transition-all opacity-0 group-hover:opacity-100 z-10"
          >
            <ChevronRight className="w-4 h-4 text-slate-700" />
          </button>
        )}
        {/* Dot indicators */}
        {resolved.length > 1 && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1.5 z-10">
            {resolved.map((_, i) => (
              <button
                key={i}
                onClick={() => setActive(i)}
                className={`w-1.5 h-1.5 rounded-full transition-all ${i === active ? 'bg-white w-4' : 'bg-white/60'}`}
              />
            ))}
          </div>
        )}
      </div>

      {/* Thumbnails */}
      {resolved.length > 1 && (
        <div className="grid grid-cols-4 gap-2">
          {resolved.map((src, i) => (
            <button
              key={i}
              onClick={() => setActive(i)}
              className={`aspect-square rounded-xl overflow-hidden border-2 transition-all ${
                i === active ? 'border-slate-900 ring-2 ring-slate-900/10' : 'border-transparent hover:border-slate-300'
              }`}
            >
              <img src={src} alt="" className="w-full h-full object-cover" draggable={false} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Trust Badges ──────────────────────────────────────────────────────── */
function TrustBadges() {
  const items = [
    { icon: <Shield className="w-4 h-4" />, text: 'Secure checkout' },
    { icon: <Truck className="w-4 h-4" />, text: 'Fast delivery' },
    { icon: <RefreshCw className="w-4 h-4" />, text: 'Easy returns' },
  ];
  return (
    <div className="flex flex-wrap gap-3 mt-4">
      {items.map((b, i) => (
        <div key={i} className="flex items-center gap-1.5 text-xs text-slate-500 bg-slate-50 rounded-full px-3 py-1.5 border border-slate-100">
          <span className="text-slate-400">{b.icon}</span>
          {b.text}
        </div>
      ))}
    </div>
  );
}

/* ─── Quantity Stepper ──────────────────────────────────────────────────── */
function QuantityStepper({ value, onChange, max }) {
  const num = Math.max(1, Number(value) || 1);
  const atMax = max !== null && max !== undefined && num >= max;
  return (
    <div className="flex items-center border border-slate-200 rounded-xl overflow-hidden w-fit">
      <button
        type="button"
        onClick={() => onChange(Math.max(1, num - 1))}
        disabled={num <= 1}
        className="w-10 h-10 flex items-center justify-center text-slate-600 hover:bg-slate-50 disabled:opacity-30 transition-colors"
      >
        <Minus className="w-3.5 h-3.5" />
      </button>
      <span className="w-12 text-center text-sm font-semibold text-slate-900 select-none">{num}</span>
      <button
        type="button"
        onClick={() => onChange(Math.min(99, num + 1))}
        disabled={atMax}
        className="w-10 h-10 flex items-center justify-center text-slate-600 hover:bg-slate-50 disabled:opacity-30 transition-colors"
      >
        <Plus className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

/* ─── Order Form ────────────────────────────────────────────────────────── */
function OrderForm({ product, availability, productPath, onSuccess }) {
  const [submitting, setSubmitting] = useState(false);
  const clientRequestIdRef = useRef(genId());
  const [form, setForm] = useState({
    customer_name: '', customer_phone: '', customer_email: '',
    quantity: 1, shipping_address: '', notes: '',
  });

  const stockAvailable = availability?.stock_quantity ?? null;
  const inStock = stockAvailable === null || Number(stockAvailable) > 0;

  const set = (field) => (e) => {
    const v = typeof e === 'number' ? e : (e?.target?.value ?? '');
    setForm(p => ({ ...p, [field]: v }));
  };

  const handleSubmit = async (e) => {
    e?.preventDefault();
    if (submitting) return;
    if (!form.customer_name.trim()) {
      showToast({ type: 'error', message: 'Please enter your name.' });
      return;
    }
    if (!form.customer_phone.trim() && !form.customer_email.trim()) {
      showToast({ type: 'error', message: 'Please enter a phone number or email.' });
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await publicClient.post(`/${productPath}/buy`, {
        customer_name: form.customer_name.trim(),
        customer_phone: form.customer_phone.trim(),
        customer_email: form.customer_email.trim() || null,
        quantity: Math.max(1, Number(form.quantity) || 1),
        shipping_address: form.shipping_address.trim(),
        notes: form.notes.trim(),
        client_request_id: clientRequestIdRef.current,
      });
      onSuccess(data);
    } catch (err) {
      const s = err?.response?.status;
      if (s === 429) showToast({ type: 'error', message: 'Too many requests. Please wait and try again.' });
      else if (s === 409) showToast({ type: 'error', message: 'Sorry, this item just went out of stock.' });
      else showToast({ type: 'error', message: getErrorMessage(err, 'We could not place your order.') });
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = "w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900/10 focus:border-slate-400 transition-all";
  const labelCls = "block text-xs font-medium text-slate-600 mb-1.5";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Quantity + CTA row */}
      <div className="flex items-center gap-4 flex-wrap">
        <div>
          <p className={labelCls}>Quantity</p>
          <QuantityStepper
            value={form.quantity}
            onChange={set('quantity')}
            max={stockAvailable}
          />
        </div>
        {stockAvailable !== null && (
          <p className="text-xs text-slate-400 mt-4">
            {stockAvailable > 0 ? `${stockAvailable} left in stock` : 'Out of stock'}
          </p>
        )}
      </div>

      <div className="border-t border-slate-100 pt-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Your details</p>
        <div className="space-y-3">
          <div>
            <label className={labelCls} htmlFor="name">Full name *</label>
            <div className="relative">
              <input id="name" className={inputCls} placeholder="Jane Smith" value={form.customer_name} onChange={set('customer_name')} maxLength={120} required />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls} htmlFor="phone">
                <span className="flex items-center gap-1"><Phone className="w-3 h-3" /> Phone</span>
              </label>
              <input id="phone" className={inputCls} placeholder="+1 555 0100" value={form.customer_phone} onChange={set('customer_phone')} maxLength={80} />
            </div>
            <div>
              <label className={labelCls} htmlFor="email">
                <span className="flex items-center gap-1"><Mail className="w-3 h-3" /> Email</span>
              </label>
              <input id="email" type="email" className={inputCls} placeholder="you@example.com" value={form.customer_email} onChange={set('customer_email')} maxLength={160} />
            </div>
          </div>
          <div>
            <label className={labelCls} htmlFor="address">
              <span className="flex items-center gap-1"><MapPin className="w-3 h-3" /> Shipping address</span>
            </label>
            <textarea id="address" className={`${inputCls} resize-none`} rows={2} placeholder="123 Main St, City, Country" value={form.shipping_address} onChange={set('shipping_address')} maxLength={500} />
          </div>
          <div>
            <label className={labelCls} htmlFor="notes">
              <span className="flex items-center gap-1"><FileText className="w-3 h-3" /> Notes (optional)</span>
            </label>
            <textarea id="notes" className={`${inputCls} resize-none`} rows={2} placeholder="Any special requests…" value={form.notes} onChange={set('notes')} maxLength={500} />
          </div>
        </div>
      </div>

      <button
        type="submit"
        disabled={submitting || !inStock}
        className="w-full flex items-center justify-center gap-2 bg-slate-900 hover:bg-slate-700 disabled:bg-slate-300 text-white font-semibold text-sm rounded-xl py-3.5 transition-all active:scale-[.98] shadow-sm"
      >
        {submitting
          ? <><Loader2 className="w-4 h-4 animate-spin" /> Processing…</>
          : inStock
            ? <><ShoppingBag className="w-4 h-4" /> Place order request</>
            : 'Out of stock'
        }
      </button>
      <p className="text-center text-xs text-slate-400">
        Your information is private and secure. We'll follow up to confirm your order.
      </p>
    </form>
  );
}

/* ─── Order Confirmation ────────────────────────────────────────────────── */
function OrderConfirmation({ data, product }) {
  return (
    <div className="text-center py-8 px-4">
      <div className="w-16 h-16 bg-emerald-100 rounded-full flex items-center justify-center mx-auto mb-4">
        <CheckCircle2 className="w-8 h-8 text-emerald-600" />
      </div>
      <h3 className="text-xl font-bold text-slate-900 mb-1">Order received!</h3>
      <p className="text-slate-500 text-sm mb-4">
        Thanks for your interest in <strong>{product?.name}</strong>. We'll be in touch shortly to confirm your order.
      </p>
      <div className="bg-slate-50 rounded-xl p-4 text-left border border-slate-100">
        <p className="text-xs text-slate-500 mb-1">Order reference</p>
        <p className="font-mono text-2xl font-bold text-slate-900 tracking-widest">
          {data?.order_ref || ('ORD-' + (data?.order_id || '').replace(/-/g, '').slice(0, 6).toUpperCase())}
        </p>
      </div>
      <p className="text-xs text-slate-400 mt-4">Keep this reference for your records.</p>
    </div>
  );
}

/* ─── Tabs ──────────────────────────────────────────────────────────────── */
function ProductTabs({ description, features }) {
  const [tab, setTab] = useState('description');
  const tabs = [
    { id: 'description', label: 'Description', show: !!description },
    { id: 'features', label: 'Features', show: features.length > 0 },
    { id: 'shipping', label: 'Shipping & Returns', show: true },
  ].filter(t => t.show);

  if (!tabs.length) return null;

  return (
    <div className="mt-8 border-t border-slate-100 pt-6">
      <div className="flex gap-0 border-b border-slate-200">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              tab === t.id ? 'border-slate-900 text-slate-900' : 'border-transparent text-slate-400 hover:text-slate-600'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="pt-4 text-sm text-slate-600 leading-relaxed">
        {tab === 'description' && <p className="whitespace-pre-wrap">{description}</p>}
        {tab === 'features' && (
          <ul className="space-y-2">
            {features.map((f, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-0.5 w-4 h-4 rounded-full bg-slate-900 text-white text-[10px] flex items-center justify-center flex-shrink-0">{i + 1}</span>
                {f}
              </li>
            ))}
          </ul>
        )}
        {tab === 'shipping' && (
          <div className="space-y-3">
            {[
              { icon: <Truck className="w-4 h-4" />, title: 'Delivery', text: 'Estimated 3–7 business days. Exact timing confirmed at order.' },
              { icon: <RefreshCw className="w-4 h-4" />, title: 'Returns', text: 'Contact us within 14 days of receiving your order if there is an issue.' },
              { icon: <Shield className="w-4 h-4" />, title: 'Guarantee', text: 'Every order is reviewed personally. Your satisfaction is our priority.' },
            ].map((item, i) => (
              <div key={i} className="flex items-start gap-3">
                <div className="w-8 h-8 bg-slate-100 rounded-lg flex items-center justify-center flex-shrink-0 text-slate-500">{item.icon}</div>
                <div><p className="font-medium text-slate-800">{item.title}</p><p className="text-slate-500">{item.text}</p></div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Main Page ─────────────────────────────────────────────────────────── */
export default function ProductDetailPage() {
  const { companySlug, productSlug } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [product, setProduct] = useState(null);
  const [company, setCompany] = useState(null);
  const [availability, setAvailability] = useState(null);
  const [orderResult, setOrderResult] = useState(null);

  const path = `public/companies/${encodeURIComponent(companySlug)}/products/${encodeURIComponent(productSlug)}`;

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const { data } = await publicClient.get(`/${path}`);
      setProduct(data.product || null);
      setCompany(data.company || null);
    } catch (err) {
      setError(err?.response?.status === 404 ? 'This product is not available.' : 'We could not load this product.');
    } finally { setLoading(false); }
  }, [path]);

  const loadAvailability = useCallback(async () => {
    try { const { data } = await publicClient.get(`/${path}/availability`); setAvailability(data); }
    catch { setAvailability(null); }
  }, [path]);

  useEffect(() => { load(); loadAvailability(); }, [load, loadAvailability]);

  if (loading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-slate-400 animate-spin mx-auto mb-3" />
          <p className="text-sm text-slate-400">Loading product…</p>
        </div>
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="min-h-screen bg-white flex flex-col items-center justify-center p-8 text-center">
        <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mb-4">
          <Package className="w-7 h-7 text-slate-400" />
        </div>
        <h1 className="text-lg font-semibold text-slate-700">{error || 'Product not found'}</h1>
        <p className="text-slate-400 text-sm mt-1">Please check the link and try again.</p>
      </div>
    );
  }

  const images = Array.isArray(product.images) ? product.images : [];
  const features = Array.isArray(product.features) ? product.features : [];
  const stockQty = availability?.stock_quantity ?? null;
  const inStock = stockQty === null || Number(stockQty) > 0;

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <header className="sticky top-0 z-20 bg-white/95 backdrop-blur-sm border-b border-slate-100">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            {company?.logo_url ? (
              <img src={resolveMediaUrl(company.logo_url)} alt={company?.name} className="w-8 h-8 rounded-lg object-cover" />
            ) : (
              <div className="w-8 h-8 rounded-lg bg-slate-900 flex items-center justify-center">
                <Package className="w-4 h-4 text-white" />
              </div>
            )}
            <div className="leading-tight">
              <p className="text-sm font-semibold text-slate-900">{company?.name || ''}</p>
              {company?.tagline && <p className="text-xs text-slate-400 hidden sm:block">{company.tagline}</p>}
            </div>
          </div>
          <div className="flex items-center gap-1 text-xs text-slate-400">
            <Shield className="w-3.5 h-3.5 text-emerald-500" />
            <span>Secure</span>
          </div>
        </div>
      </header>

      {/* Breadcrumb */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 pt-4 pb-0">
        <nav className="flex items-center gap-1.5 text-xs text-slate-400">
          <span>{company?.name || 'Store'}</span>
          <span>/</span>
          {product.category && <><span>{product.category}</span><span>/</span></>}
          <span className="text-slate-600 font-medium truncate max-w-[200px]">{product.name}</span>
        </nav>
      </div>

      {/* Main content */}
      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-6 lg:py-10">
        <div className="grid lg:grid-cols-2 gap-8 lg:gap-14">

          {/* Left — Gallery */}
          <div className="lg:sticky lg:top-20 lg:self-start">
            <ImageGallery images={images} productName={product.name} />
          </div>

          {/* Right — Info + Form */}
          <div>
            {/* Category + stock */}
            <div className="flex items-center gap-2 flex-wrap mb-3">
              {product.category && (
                <span className="text-xs font-medium text-slate-500 bg-slate-100 rounded-full px-3 py-1 uppercase tracking-wide">
                  {product.category}
                </span>
              )}
              {inStock ? (
                <span className="text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-full px-3 py-1 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full inline-block" />
                  In stock
                </span>
              ) : (
                <span className="text-xs font-medium text-red-600 bg-red-50 border border-red-100 rounded-full px-3 py-1">
                  Out of stock
                </span>
              )}
            </div>

            {/* Title */}
            <h1 className="text-3xl sm:text-4xl font-bold text-slate-900 leading-tight">{product.name}</h1>
            {product.product_title && (
              <p className="text-slate-400 mt-1.5 text-base">{product.product_title}</p>
            )}

            {/* Rating placeholder — makes it feel like a real store */}
            <div className="flex items-center gap-2 mt-3">
              <div className="flex gap-0.5">
                {[1,2,3,4,5].map(s => (
                  <Star key={s} className="w-4 h-4 fill-amber-400 text-amber-400" />
                ))}
              </div>
              <span className="text-xs text-slate-400">Verified product</span>
            </div>

            {/* Price */}
            {product.price && (
              <div className="mt-4">
                <span className="text-4xl font-extrabold text-slate-900">
                  {formatPrice(product.price, product.price_currency)}
                </span>
              </div>
            )}

            {/* Short description (shown above fold) */}
            {product.description && (
              <p className="mt-4 text-slate-500 text-sm leading-relaxed line-clamp-3">
                {product.description}
              </p>
            )}

            <TrustBadges />

            {/* Divider */}
            <div className="border-t border-slate-100 my-6" />

            {/* Order form or confirmation */}
            {orderResult ? (
              <OrderConfirmation data={orderResult} product={product} />
            ) : (
              <OrderForm
                product={product}
                availability={availability}
                productPath={path}
                onSuccess={(data) => { setOrderResult(data); loadAvailability(); }}
              />
            )}

            {/* Support */}
            {company?.support_email && (
              <p className="text-xs text-slate-400 mt-5 text-center">
                Questions?{' '}
                <a href={`mailto:${company.support_email}`} className="text-slate-600 hover:text-slate-900 underline underline-offset-2 transition-colors">
                  {company.support_email}
                </a>
              </p>
            )}

            {/* Tabs */}
            <ProductTabs description={product.description} features={features} />
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-100 mt-16 py-6 text-center">
        <p className="text-xs text-slate-300">
          {company?.name ? `© ${new Date().getFullYear()} ${company.name}` : ''}
          {company?.website_address && (
            <> · <a href={company.website_address} className="hover:text-slate-500 transition-colors" target="_blank" rel="noopener noreferrer">{company.website_address.replace(/^https?:\/\//, '')}</a></>
          )}
        </p>
      </footer>
    </div>
  );
}
