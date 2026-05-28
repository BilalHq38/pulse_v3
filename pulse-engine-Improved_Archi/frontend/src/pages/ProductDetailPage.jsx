import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { API_BASE_URL, resolveMediaUrl } from '@/lib/backend-url';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { showToast, getErrorMessage } from '@/hooks/use-toast';
import { Package, ShoppingCart, CheckCircle2, Loader2 } from 'lucide-react';

const publicClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
});

function formatPrice(value, currency) {
  const num = Number(value);
  if (!value || Number.isNaN(num)) return '';
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || 'USD' }).format(num);
  } catch {
    return `${currency || 'USD'} ${num.toFixed(2)}`;
  }
}

function generateClientRequestId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, '');
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export default function ProductDetailPage() {
  const { companySlug, productSlug } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [product, setProduct] = useState(null);
  const [company, setCompany] = useState(null);
  const [availability, setAvailability] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [orderResult, setOrderResult] = useState(null);
  const [form, setForm] = useState({
    customer_name: '',
    customer_phone: '',
    customer_email: '',
    quantity: 1,
    shipping_address: '',
    notes: '',
  });

  const productPath = `public/companies/${encodeURIComponent(companySlug)}/products/${encodeURIComponent(productSlug)}`;

  const fetchProduct = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await publicClient.get(`/${productPath}`);
      setProduct(data.product || null);
      setCompany(data.company || null);
    } catch (err) {
      setError(err?.response?.status === 404 ? 'This product is not available.' : 'We could not load this product.');
    } finally {
      setLoading(false);
    }
  }, [productPath]);

  const fetchAvailability = useCallback(async () => {
    try {
      const { data } = await publicClient.get(`/${productPath}/availability`);
      setAvailability(data || null);
    } catch {
      setAvailability(null);
    }
  }, [productPath]);

  useEffect(() => {
    fetchProduct();
    fetchAvailability();
  }, [fetchProduct, fetchAvailability]);

  const handleField = (field) => (event) => {
    const value = event?.target?.value ?? '';
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleBuy = async (event) => {
    event?.preventDefault?.();
    if (submitting) return;
    if (!form.customer_name.trim()) {
      showToast({ type: 'error', message: 'Please add your name.' });
      return;
    }
    if (!form.customer_phone.trim() && !form.customer_email.trim()) {
      showToast({ type: 'error', message: 'Please add a phone number or email so we can confirm your order.' });
      return;
    }
    const quantity = Math.max(1, Number(form.quantity) || 1);
    const body = {
      customer_name: form.customer_name.trim(),
      customer_phone: form.customer_phone.trim(),
      customer_email: form.customer_email.trim() || null,
      quantity,
      shipping_address: form.shipping_address.trim(),
      notes: form.notes.trim(),
      client_request_id: generateClientRequestId(),
    };
    setSubmitting(true);
    try {
      const { data } = await publicClient.post(`/${productPath}/buy`, body);
      setOrderResult(data);
      showToast({ type: 'success', message: 'Thanks! Your order request is in.' });
      fetchAvailability();
    } catch (err) {
      const status = err?.response?.status;
      if (status === 429) {
        showToast({ type: 'error', message: 'Too many requests. Please wait a moment and try again.' });
      } else if (status === 409) {
        showToast({ type: 'error', message: 'Sorry, this item just went out of stock.' });
        fetchAvailability();
      } else {
        showToast({ type: 'error', message: getErrorMessage(err, 'We could not place your order.') });
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-8 text-center">
        <Package className="w-12 h-12 text-slate-300 mb-3" />
        <h1 className="text-xl font-semibold text-slate-700">{error || 'Product not found'}</h1>
        <p className="text-slate-500 mt-1">Please check the link and try again.</p>
      </div>
    );
  }

  const images = Array.isArray(product.images) ? product.images : [];
  const features = Array.isArray(product.features) ? product.features : [];
  const stockAvailable = availability && availability.stock_quantity !== null && availability.stock_quantity !== undefined
    ? Number(availability.stock_quantity)
    : null;
  const inStock = stockAvailable === null || stockAvailable > 0;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center gap-3">
          {company?.logo_url ? (
            <img src={resolveMediaUrl(company.logo_url)} alt={company.name} className="w-9 h-9 rounded object-cover" />
          ) : (
            <div className="w-9 h-9 rounded bg-blue-100 flex items-center justify-center">
              <Package className="w-4 h-4 text-blue-600" />
            </div>
          )}
          <div>
            <div className="font-semibold text-slate-800">{company?.name || ''}</div>
            {company?.tagline ? <div className="text-xs text-slate-500">{company.tagline}</div> : null}
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-8 grid lg:grid-cols-2 gap-8">
        <section>
          {images.length === 0 ? (
            <div className="aspect-square bg-gradient-to-br from-blue-50 to-slate-100 rounded-xl flex items-center justify-center">
              <Package className="w-16 h-16 text-blue-200" />
            </div>
          ) : (
            <div className="grid gap-3">
              <img src={resolveMediaUrl(images[0])} alt={product.name} className="w-full rounded-xl object-cover aspect-square" />
              {images.length > 1 ? (
                <div className="grid grid-cols-3 gap-3">
                  {images.slice(1, 4).map((img, i) => (
                    <img key={i} src={resolveMediaUrl(img)} alt="" className="w-full aspect-square object-cover rounded-lg" />
                  ))}
                </div>
              ) : null}
            </div>
          )}
        </section>

        <section>
          <div className="flex items-center gap-2 mb-2 text-xs text-slate-500">
            {product.category ? <Badge variant="secondary">{product.category}</Badge> : null}
            {inStock ? (
              <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">In stock{stockAvailable !== null ? ` (${stockAvailable})` : ''}</Badge>
            ) : (
              <Badge variant="destructive">Out of stock</Badge>
            )}
          </div>
          <h1 className="text-2xl font-bold text-slate-900">{product.name}</h1>
          {product.product_title ? <div className="text-sm text-slate-500 mt-1">{product.product_title}</div> : null}
          {product.price ? (
            <div className="text-3xl font-bold text-slate-900 mt-4">{formatPrice(product.price, product.price_currency)}</div>
          ) : null}
          {product.description ? <p className="text-slate-700 mt-4 whitespace-pre-wrap leading-relaxed">{product.description}</p> : null}
          {features.length ? (
            <ul className="mt-4 space-y-1 list-disc pl-5 text-slate-700">
              {features.map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          ) : null}

          {orderResult ? (
            <Card className="mt-6 border-emerald-200 bg-emerald-50">
              <CardContent className="p-4 flex items-start gap-3">
                <CheckCircle2 className="w-5 h-5 text-emerald-600 mt-0.5" />
                <div>
                  <div className="font-semibold text-emerald-900">Order received</div>
                  <div className="text-sm text-emerald-800">
                    Your reference is <span className="font-mono">{orderResult.order_id}</span>. We will follow up shortly.
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card className="mt-6">
              <CardHeader>
                <CardTitle>Request to buy</CardTitle>
              </CardHeader>
              <CardContent>
                <form onSubmit={handleBuy} className="space-y-3">
                  <div>
                    <Label htmlFor="customer_name">Name</Label>
                    <Input id="customer_name" value={form.customer_name} onChange={handleField('customer_name')} required maxLength={120} />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label htmlFor="customer_phone">Phone</Label>
                      <Input id="customer_phone" value={form.customer_phone} onChange={handleField('customer_phone')} maxLength={80} placeholder="+1 555 010" />
                    </div>
                    <div>
                      <Label htmlFor="customer_email">Email</Label>
                      <Input id="customer_email" type="email" value={form.customer_email} onChange={handleField('customer_email')} maxLength={160} />
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <Label htmlFor="quantity">Quantity</Label>
                      <Input id="quantity" type="number" min={1} max={99} value={form.quantity} onChange={handleField('quantity')} />
                    </div>
                  </div>
                  <div>
                    <Label htmlFor="shipping_address">Shipping address</Label>
                    <Textarea id="shipping_address" rows={2} value={form.shipping_address} onChange={handleField('shipping_address')} maxLength={500} />
                  </div>
                  <div>
                    <Label htmlFor="notes">Notes</Label>
                    <Textarea id="notes" rows={2} value={form.notes} onChange={handleField('notes')} maxLength={500} />
                  </div>
                  <Button type="submit" className="w-full" disabled={submitting || !inStock}>
                    {submitting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <ShoppingCart className="w-4 h-4 mr-2" />}
                    {inStock ? 'Place order request' : 'Out of stock'}
                  </Button>
                  <p className="text-xs text-slate-500">By submitting, you agree to be contacted about this order. We will not share your details.</p>
                </form>
              </CardContent>
            </Card>
          )}

          {company?.support_email ? (
            <p className="text-xs text-slate-500 mt-4">Questions? Email <a className="text-blue-600 hover:underline" href={`mailto:${company.support_email}`}>{company.support_email}</a>.</p>
          ) : null}
        </section>
      </main>
    </div>
  );
}
