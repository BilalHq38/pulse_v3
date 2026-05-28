import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { resolveMediaUrl } from '@/lib/backend-url';
import BulkUploadModal from '@/components/BulkUploadModal';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import {
  Package,
  Plus,
  Search,
  Upload,
  X,
  Trash2,
  Edit,
  ChevronLeft,
  ChevronRight,
  Save,
  Wand2,
  Lock,
  Check,
  LayoutGrid,
  List,
  Eye,
} from 'lucide-react';

const PRODUCT_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const MAX_PRODUCT_IMAGES = 3;
const MAX_PRODUCT_IMAGE_SIZE_MB = 5;
const DEFAULT_CATEGORIES = ['general','software','service','hardware','subscription','consulting','support','training','integration'];
const PRODUCT_BULK_TEMPLATE_HEADERS = ['name', 'product_title', 'description', 'price', 'price_currency', 'category', 'product_type', 'links', 'stock_quantity', 'image_url'];
const PRODUCT_BULK_TEMPLATE_SAMPLE = ['Starter Plan', 'starter-plan-2026', 'Entry-level package for new teams', '29', 'USD', 'subscription', 'standard', '', '50', 'https://example.com/product.jpg'];
const PRODUCT_BULK_GUIDE_ROWS = [
  { column: 'name', help: 'Required product name.' },
  { column: 'product_title', help: 'Optional SKU, short code, or public title.' },
  { column: 'description', help: 'Optional product description text.' },
  { column: 'price', help: 'Optional numeric amount. Currency symbols are allowed.' },
  { column: 'price_currency', help: 'Optional currency code such as USD, EUR, or GBP. Defaults to USD.' },
  { column: 'category', help: 'Optional category such as software, service, subscription, or support.' },
  { column: 'product_type', help: 'Optional type or variant. Defaults to standard.' },
  { column: 'links', help: 'Optional canonical product URL (http or https). Leave blank to use the auto-generated public product page.' },
  { column: 'stock_quantity', help: 'Optional integer stock count. Leave blank if the product does not track inventory.' },
  { column: 'image_url', help: 'Optional public image URL (JPG, PNG, WEBP, GIF). Up to 3 URLs separated by |, use columns image_url_1, image_url_2, image_url_3, or embed images in XLSX rows.' },
];

const EMPTY_FORM = {
  name: '', product_title: '', description: '', price: '', price_currency: 'USD',
  category: 'general', product_type: '', images: [],
};

function ImageGrid({ images, onClickImage }) {
  if (!Array.isArray(images) || images.length === 0) {
    return (
      <div className="h-36 flex items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 rounded-t-xl">
        <Package size={32} className="text-blue-200" />
      </div>
    );
  }
  const clickable = typeof onClickImage === 'function';
  const cols = images.length === 1 ? 'grid-cols-1' : images.length === 2 ? 'grid-cols-2' : 'grid-cols-3';
  return (
    <div className={`h-36 grid ${cols} gap-0.5 overflow-hidden rounded-t-xl`}>
      {images.map((img, i) => (
        <div key={i} className="relative overflow-hidden h-36">
          <img
            src={resolveMediaUrl(img)}
            alt="product"
            className={`w-full h-full object-cover${clickable ? ' cursor-zoom-in' : ''}`}
            onClick={() => clickable && onClickImage(i)}
          />
        </div>
      ))}
    </div>
  );
}

export default function ProductsPage() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  // Detail modal
  const [selectedProduct, setSelectedProduct] = useState(null);

  // Add / Edit form
  const [showForm, setShowForm] = useState(false);
  const [editingProduct, setEditingProduct] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [imageError, setImageError] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generatingDesc, setGeneratingDesc] = useState(false);
  const [genDescNudge, setGenDescNudge] = useState(false);
  const [showBulkUpload, setShowBulkUpload] = useState(false);
  const [bulkUploading, setBulkUploading] = useState(false);

  const [viewMode, setViewMode] = useState('list'); // 'grid' | 'list'

  // Delete confirm dialog
  const [deleteTarget, setDeleteTarget] = useState(null); // { products: [...] }
  const [deletingBulk, setDeletingBulk] = useState(false);

  // Multi-select
  const [selectedIds, setSelectedIds] = useState(new Set());

  // Custom categories (persisted in localStorage)
  const [customCategories, setCustomCategories] = useState(() => {
    try { return JSON.parse(localStorage.getItem('product_custom_categories') || '[]'); }
    catch { return []; }
  });
  const [customCategoryInput, setCustomCategoryInput] = useState('');
  const [showCustomCategoryInput, setShowCustomCategoryInput] = useState(false);

  const allCategories = [...DEFAULT_CATEGORIES, ...customCategories];

  const addCustomCategory = (raw) => {
    const val = raw.trim().toLowerCase().replace(/\s+/g, '-');
    if (!val) return;
    const updated = [...new Set([...customCategories, val])];
    setCustomCategories(updated);
    localStorage.setItem('product_custom_categories', JSON.stringify(updated));
    setForm(prev => ({ ...prev, category: val }));
    setCustomCategoryInput('');
    setShowCustomCategoryInput(false);
  };

  // Lightbox
  const [lightboxImages, setLightboxImages] = useState([]);
  const [lightboxIndex, setLightboxIndex] = useState(0);

  // ── Load ────────────────────────────────────────────────────
  const loadProducts = async () => {
    setLoading(true);
    try {
      const res = await api.get('/products', { params: { page: 1, page_size: 200 } });
      const payload = res?.data;
      const items = Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : [];
      setProducts(items);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadProducts(); }, []);

  // ── Lightbox ────────────────────────────────────────────────
  const openLightbox = useCallback((images, index) => {
    setLightboxImages(images);
    setLightboxIndex(index);
  }, []);
  const closeLightbox = useCallback(() => setLightboxImages([]), []);

  useEffect(() => {
    if (!lightboxImages.length) return;
    const handler = (e) => {
      if (e.key === 'ArrowRight') setLightboxIndex(i => (i + 1) % lightboxImages.length);
      if (e.key === 'ArrowLeft') setLightboxIndex(i => (i - 1 + lightboxImages.length) % lightboxImages.length);
      if (e.key === 'Escape') closeLightbox();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [lightboxImages, closeLightbox]);

  // ── Form helpers ────────────────────────────────────────────
  const resetForm = () => {
    setForm(EMPTY_FORM);
    setImageError('');
    setDragActive(false);
    setEditingProduct(null);
    setShowForm(false);
    setCustomCategoryInput('');
    setShowCustomCategoryInput(false);
  };

  const openAdd = () => { resetForm(); setShowForm(true); };

  const openEdit = (product, e) => {
    if (e) e.stopPropagation();
    setEditingProduct(product);
    setImageError('');
    setDragActive(false);
    setForm({
      name: product.name || '',
      product_title: product.product_title || '',
      description: product.description || '',
      price: product.price || '',
      price_currency: product.price_currency || 'USD',
      category: product.category || 'general',
      product_type: product.product_type || '',
      images: Array.isArray(product.images)
        ? product.images.slice(0, MAX_PRODUCT_IMAGES).map((url, idx) => ({
            id: `${product.id}-e${idx}`, name: `img-${idx + 1}`,
            type: 'image', size: 0, dataUrl: url,
          }))
        : [],
    });
    setSelectedProduct(null);
    setShowForm(true);
  };

  const readAsDataUrl = (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const slots = MAX_PRODUCT_IMAGES - (form.images?.length || 0);
    if (slots <= 0) { setImageError(`Max ${MAX_PRODUCT_IMAGES} images`); return; }
    const accepted = files.slice(0, slots);
    const next = [];
    for (const file of accepted) {
      if (!PRODUCT_IMAGE_TYPES.includes(file.type)) { setImageError('Only JPG, PNG, WEBP, GIF'); continue; }
      if (file.size > MAX_PRODUCT_IMAGE_SIZE_MB * 1024 * 1024) { setImageError(`Max ${MAX_PRODUCT_IMAGE_SIZE_MB}MB each`); continue; }
      try {
        const dataUrl = await readAsDataUrl(file);
        next.push({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, name: file.name, type: file.type, size: file.size, dataUrl });
      } catch { setImageError('Failed to read file'); }
    }
    if (next.length) {
      setImageError('');
      setForm(prev => ({ ...prev, images: [...(prev.images || []), ...next].slice(0, MAX_PRODUCT_IMAGES) }));
    }
  };

  const removeImage = (id) => setForm(prev => ({ ...prev, images: prev.images.filter(img => img.id !== id) }));

  // ── AI description ──────────────────────────────────────────
  const generateProductDescription = async () => {
    if (!form.name.trim()) {
      setGenDescNudge(true);
      setTimeout(() => setGenDescNudge(false), 2800);
      return;
    }
    if (generatingDesc) return;
    setGeneratingDesc(true);
    try {
      const res = await api.post('/products/generate-description', {
        name: form.name.trim(),
        product_title: form.product_title || '',
        product_type: form.product_type || '',
        category: form.category || 'general',
        price: form.price || '',
        price_currency: form.price_currency || 'USD',
        images: (form.images || []).map(img => img.dataUrl || img).filter(Boolean),
      });
      if (res.data?.description) setForm(prev => ({ ...prev, description: res.data.description }));
    } catch (err) { console.error('AI description failed:', err); }
    finally { setGeneratingDesc(false); }
  };
  const saveProduct = async () => {
    if (!form.name.trim()) { setImageError('Product name is required'); return; }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        product_title: (form.product_title || '').trim(),
        product_type: (form.product_type || '').trim() || 'standard',
        description: (form.description || ''),
        price: (form.price || ''),
        price_currency: form.price_currency || 'USD',
        category: form.category || 'general',
        images: (form.images || []).map(img => img.dataUrl).slice(0, MAX_PRODUCT_IMAGES),
        features: [],
      };
      if (editingProduct?.id) {
        await api.put(`/products/${editingProduct.id}`, payload);
      } else {
        await api.post('/products', payload);
      }
      resetForm();
      await loadProducts();
    } catch (err) { console.error(err); setImageError('Failed to save product'); }
    finally { setSaving(false); }
  };

  const handleBulkUpload = async (file) => {
    setBulkUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('upsert', 'true');
      const response = await api.post('/products/bulk-upload', formData);

      const created = Number(response?.data?.created || 0);
      const updated = Number(response?.data?.updated || 0);
      const apiErrors = Array.isArray(response?.data?.errors) ? response.data.errors : [];
      const uploadErrors = apiErrors;

      if (created + updated > 0) {
        await loadProducts();
        setShowBulkUpload(false);
      }

      if (uploadErrors.length > 0) {
        const firstError = uploadErrors[0];
        showToast({
          type: created + updated > 0 ? 'warning' : 'error',
          title: created + updated > 0 ? 'Products Imported with Warnings' : 'Product Import Failed',
          message: `Created ${created}, updated ${updated}, skipped ${uploadErrors.length}. First issue: row ${firstError.row} - ${firstError.error}`,
        });
      } else {
        showToast({
          type: 'success',
          title: 'Product Import Complete',
          message: `Created ${created} product${created === 1 ? '' : 's'}${updated > 0 ? ` and updated ${updated}` : ''}.`,
        });
      }
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Import Failed',
        message: getErrorMessage(err, 'We could not import that product spreadsheet.'),
      });
    } finally {
      setBulkUploading(false);
    }
  };

  // ── Delete ──────────────────────────────────────────────────
  const deleteProduct = (product, e) => {
    if (e) e.stopPropagation();
    setDeleteTarget({ products: [product] });
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeletingBulk(true);
    try {
      await Promise.all(deleteTarget.products.map(p => api.delete(`/products/${p.id}`)));
      const ids = new Set(deleteTarget.products.map(p => p.id));
      setProducts(prev => prev.filter(p => !ids.has(p.id)));
      setSelectedIds(prev => { const next = new Set(prev); ids.forEach(id => next.delete(id)); return next; });
      if (selectedProduct && ids.has(selectedProduct.id)) setSelectedProduct(null);
    } catch (err) { console.error(err); }
    finally { setDeletingBulk(false); setDeleteTarget(null); }
  };

  const bulkDeleteClick = () => {
    const toDelete = products.filter(p => selectedIds.has(p.id));
    if (toDelete.length) setDeleteTarget({ products: toDelete });
  };

  const toggleSelect = (id, e) => {
    if (e) e.stopPropagation();
    setSelectedIds(prev => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; });
  };

  const toggleSelectAll = () => {
    setSelectedIds(selectedIds.size === filtered.length && filtered.length > 0
      ? new Set()
      : new Set(filtered.map(p => p.id)));
  };

  // ── Filter ──────────────────────────────────────────────────
  const filtered = products.filter(p => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (p.name || '').toLowerCase().includes(q) ||
      (p.product_title || '').toLowerCase().includes(q) ||
      (p.category || '').toLowerCase().includes(q) ||
      (p.product_type || '').toLowerCase().includes(q);
  });

  // ── Render ──────────────────────────────────────────────────
  return (
    <div className="p-6 lg:p-8 space-y-6" data-testid="products-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Products</h1>
          <p className="text-slate-400 text-sm mt-1">{products.length} product{products.length !== 1 ? 's' : ''} in catalog</p>
        </div>
        <div className="flex items-center gap-2">
          {selectedIds.size > 0 && (
            <button
              onClick={bulkDeleteClick}
              className="flex items-center gap-2 px-4 py-2.5 bg-red-50 border border-red-200 text-red-600 rounded-xl text-sm font-medium hover:bg-red-100 transition-all"
            >
              <Trash2 size={16} /> Delete ({selectedIds.size})
            </button>
          )}
          <button
            onClick={() => setShowBulkUpload(true)}
            className="flex items-center gap-2 rounded-xl border border-slate-100 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-all hover:border-blue-200 hover:text-blue-700"
            data-testid="bulk-upload-products-btn"
          >
            <Upload size={16} /> Bulk Upload
          </button>
          <button
            onClick={openAdd}
            className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15"
          >
            <Plus size={16} /> Add Product
          </button>
        </div>
      </div>

      {/* Search + View Toggle */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search products..."
            className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20"
          />
        </div>
        <div className="flex items-center border border-slate-200 rounded-lg overflow-hidden">
          <button
            onClick={() => setViewMode('list')}
            className={`p-2 transition-colors ${viewMode === 'list' ? 'bg-blue-50 text-blue-600' : 'bg-white text-slate-400 hover:text-slate-600 hover:bg-slate-50'}`}
            title="List view"
          >
            <List size={16} />
          </button>
          <button
            onClick={() => setViewMode('grid')}
            className={`p-2 border-l border-slate-200 transition-colors ${viewMode === 'grid' ? 'bg-blue-50 text-blue-600' : 'bg-white text-slate-400 hover:text-slate-600 hover:bg-slate-50'}`}
            title="Grid view"
          >
            <LayoutGrid size={16} />
          </button>
        </div>
      </div>

      {/* Products */}
      {loading && <p className="text-sm text-slate-400">Loading products...</p>}
      {!loading && filtered.length === 0 && (
        <div className="bg-white border border-slate-100 rounded-xl p-8 text-center">
          <Package size={28} className="text-slate-200 mx-auto mb-3" />
          <p className="text-sm text-slate-400">{search ? 'No products match your search.' : 'No products yet. Click Add Product to get started.'}</p>
        </div>
      )}

      {/* ── List View ── */}
      {!loading && filtered.length > 0 && viewMode === 'list' && (
        <div className="bg-white border border-slate-100 rounded-xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50/80 border-b border-slate-100">
                  <th className="px-4 py-3 w-8">
                    <input
                      type="checkbox"
                      checked={filtered.length > 0 && selectedIds.size === filtered.length}
                      onChange={toggleSelectAll}
                      className="rounded border-slate-300 cursor-pointer"
                      title="Select all"
                    />
                  </th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider w-12"></th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Product</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Product ID / SKU</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Category</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Type</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Price</th>
                  <th className="text-right px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(p => (
                  <tr
                    key={p.id}
                    onClick={() => setSelectedProduct(p)}
                    className={`border-b border-slate-50 hover:bg-slate-50/50 transition-colors group cursor-pointer ${selectedIds.has(p.id) ? 'bg-blue-50/40' : ''}`}
                  >
                    {/* Checkbox */}
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(p.id)}
                        onChange={(e) => toggleSelect(p.id, e)}
                        className="rounded border-slate-300 cursor-pointer"
                      />
                    </td>
                    {/* Thumbnail */}
                    <td className="px-4 py-3">
                      {Array.isArray(p.images) && p.images.length > 0 ? (
                        <div className="w-10 h-10 rounded-lg overflow-hidden border border-slate-100 flex-shrink-0">
                          <img src={resolveMediaUrl(p.images[0])} alt={p.name} className="w-full h-full object-cover" />
                        </div>
                      ) : (
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-50 to-slate-100 flex items-center justify-center flex-shrink-0">
                          <Package size={16} className="text-blue-200" />
                        </div>
                      )}
                    </td>
                    {/* Name */}
                    <td className="px-4 py-3">
                      <p className="font-semibold text-slate-800 truncate max-w-[200px] group-hover:text-blue-700 transition-colors">{p.name}</p>
                      {p.description && (
                        <p className="text-[11px] text-slate-400 truncate max-w-[200px] mt-0.5">{p.description}</p>
                      )}
                    </td>
                    {/* Code */}
                    <td className="px-4 py-3">
                      <span className="text-xs text-slate-700">{p.product_title || '—'}</span>
                    </td>
                    {/* Category */}
                    <td className="px-4 py-3">
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">{p.category || 'general'}</span>
                    </td>
                    {/* Type */}
                    <td className="px-4 py-3 text-slate-500 text-xs">{p.product_type || '—'}</td>
                    {/* Price */}
                    <td className="px-4 py-3">
                      {p.price ? (
                        <span className="text-xs font-medium text-green-600">{p.price} {p.price_currency || ''}</span>
                      ) : (
                        <span className="text-xs text-slate-300">—</span>
                      )}
                    </td>
                    {/* Actions */}
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={(e) => { e.stopPropagation(); setSelectedProduct(p); }}
                          className="p-1.5 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors" title="View">
                          <Eye size={14} />
                        </button>
                        <button onClick={(e) => openEdit(p, e)}
                          className="p-1.5 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors" title="Edit">
                          <Edit size={14} />
                        </button>
                        <button onClick={(e) => deleteProduct(p, e)}
                          className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors" title="Delete">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Grid View ── */}
      {!loading && filtered.length > 0 && viewMode === 'grid' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map(p => (
            <div
              key={p.id}
              onClick={() => setSelectedProduct(p)}
              className={`relative bg-white border rounded-xl overflow-hidden hover:border-blue-200 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 cursor-pointer group ${selectedIds.has(p.id) ? 'border-blue-400 ring-2 ring-blue-200' : 'border-slate-100'}`}
            >
              <div className="absolute top-2 left-2 z-10" onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(p.id)}
                  onChange={(e) => toggleSelect(p.id, e)}
                  className="rounded border-white/80 bg-white/90 shadow-sm cursor-pointer"
                />
              </div>
              <ImageGrid images={p.images} />
              <div className="p-4">
                <div className="flex items-start justify-between gap-2 mb-1">
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-slate-800 truncate group-hover:text-blue-700 transition-colors">{p.name}</h3>
                    {p.product_title && (
                      <p className="text-xs text-slate-500 truncate mt-0.5">
                        <span className="text-slate-400 font-medium">Code: </span>{p.product_title}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-1 flex-shrink-0">
                    <button
                      type="button"
                      onClick={(e) => openEdit(p, e)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                      title="Edit"
                    >
                      <Edit size={13} />
                    </button>
                    <button
                      type="button"
                      onClick={(e) => deleteProduct(p, e)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                      title="Delete"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                <p
                  className="text-xs text-slate-600 leading-relaxed mt-2 min-h-[2.4rem]"
                  style={{
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                  }}
                >
                  {p.description || 'No description provided yet.'}
                </p>
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  {p.price && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-50 text-green-600 border border-green-200 font-medium">
                      {p.price}{p.price_currency ? ` ${p.price_currency}` : ''}
                    </span>
                  )}
                  {p.product_type && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 border border-blue-200">{p.product_type}</span>}
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">{p.category}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Detail Modal ───────────────────────────────────────── */}
      {selectedProduct && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setSelectedProduct(null)}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
            {/* Images */}
            {Array.isArray(selectedProduct.images) && selectedProduct.images.length > 0 && (
              <div className="overflow-hidden rounded-t-2xl bg-slate-100">
                <div className={`grid gap-0.5 h-56 ${
                  selectedProduct.images.length === 1 ? 'grid-cols-1' :
                  selectedProduct.images.length === 2 ? 'grid-cols-2' :
                  'grid-cols-3'
                }`}>
                  {selectedProduct.images.map((img, i) => (
                    <div key={i} className="overflow-hidden h-56">
                      <img
                        src={resolveMediaUrl(img)}
                        alt={selectedProduct.name}
                        className="w-full h-full object-cover cursor-pointer hover:scale-105 transition-transform duration-200"
                        onClick={(e) => { e.stopPropagation(); openLightbox(selectedProduct.images, i); }}
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="p-6">
              <div className="flex items-start justify-between mb-5">
                <div className="min-w-0 pr-3">
                  <h3 className="text-xl font-bold text-slate-900 truncate">{selectedProduct.name}</h3>
                  {selectedProduct.product_title && (
                    <p className="text-sm text-slate-500 mt-0.5">
                      <span className="text-slate-400 text-xs font-medium">Code: </span>{selectedProduct.product_title}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={(e) => openEdit(selectedProduct, e)}
                    className="px-2.5 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 rounded-lg text-xs font-medium hover:bg-slate-100 transition-colors inline-flex items-center gap-1"
                  >
                    <Edit size={12} /> Edit
                  </button>
                  <button onClick={() => setSelectedProduct(null)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
                </div>
              </div>
              <div className="space-y-3">
                {selectedProduct.description && (
                  <div className="bg-slate-50 rounded-xl p-3">
                    <p className="text-xs text-slate-400 font-medium mb-1">Description</p>
                    <p className="text-sm text-slate-700 leading-relaxed">{selectedProduct.description}</p>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3">
                  {selectedProduct.price && (
                    <div className="bg-green-50 border border-green-100 rounded-xl p-3">
                      <p className="text-[10px] text-green-500 font-medium mb-0.5">Price</p>
                      <p className="text-sm font-semibold text-green-700">
                        {selectedProduct.price}{selectedProduct.price_currency ? ` ${selectedProduct.price_currency}` : ''}
                      </p>
                    </div>
                  )}
                  {selectedProduct.product_type && (
                    <div className="bg-blue-50 border border-blue-100 rounded-xl p-3">
                      <p className="text-[10px] text-blue-500 font-medium mb-0.5">Type</p>
                      <p className="text-sm font-semibold text-blue-700">{selectedProduct.product_type}</p>
                    </div>
                  )}
                  <div className="bg-slate-50 border border-slate-100 rounded-xl p-3">
                    <p className="text-[10px] text-slate-400 font-medium mb-0.5">Category</p>
                    <p className="text-sm font-semibold text-slate-700 capitalize">{selectedProduct.category}</p>
                  </div>
                  {Array.isArray(selectedProduct.features) && selectedProduct.features.length > 0 && (
                    <div className="bg-slate-50 border border-slate-100 rounded-xl p-3">
                      <p className="text-[10px] text-slate-400 font-medium mb-1">Features</p>
                      <ul className="space-y-0.5">
                        {selectedProduct.features.slice(0, 4).map((f, i) => (
                          <li key={i} className="text-xs text-slate-600 flex items-start gap-1"><span className="text-blue-400 mt-0.5">•</span>{f}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
                  <button
                    onClick={(e) => deleteProduct(selectedProduct, e)}
                    className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1"
                  >
                    <Trash2 size={13} /> Delete product
                  </button>
                  <button onClick={() => setSelectedProduct(null)} className="text-xs text-slate-400 hover:text-slate-600">Close</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Lightbox ──────────────────────────────────────────── */}
      {lightboxImages.length > 0 && (
        <div className="fixed inset-0 z-[60] bg-white/85 backdrop-blur-md flex items-center justify-center" onClick={closeLightbox}>
          <button
            onClick={(e) => { e.stopPropagation(); setLightboxIndex(i => (i - 1 + lightboxImages.length) % lightboxImages.length); }}
            className="absolute left-3 sm:left-6 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-black/8 hover:bg-black/15 text-slate-700 flex items-center justify-center transition-colors z-10 border border-slate-200"
          >
            <ChevronLeft size={22} />
          </button>
          <img
            src={resolveMediaUrl(lightboxImages[lightboxIndex])}
            alt={`Attachment ${lightboxIndex + 1}`}
            className="max-h-[82vh] max-w-[88vw] object-contain rounded-2xl shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            onClick={(e) => { e.stopPropagation(); setLightboxIndex(i => (i + 1) % lightboxImages.length); }}
            className="absolute right-3 sm:right-6 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-black/8 hover:bg-black/15 text-slate-700 flex items-center justify-center transition-colors z-10 border border-slate-200"
          >
            <ChevronRight size={22} />
          </button>
          <button onClick={closeLightbox} className="absolute top-4 right-4 w-9 h-9 rounded-full bg-black/8 hover:bg-black/15 text-slate-700 flex items-center justify-center border border-slate-200">
            <X size={18} />
          </button>
          <div className="absolute bottom-5 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2">
            <span className="text-slate-500 text-xs font-medium">{lightboxIndex + 1} / {lightboxImages.length}</span>
            <div className="flex gap-1.5">
              {lightboxImages.map((_, idx) => (
                <button key={idx} onClick={(e) => { e.stopPropagation(); setLightboxIndex(idx); }}
                  className={`w-1.5 h-1.5 rounded-full transition-colors ${idx === lightboxIndex ? 'bg-slate-700' : 'bg-slate-300'}`} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Add / Edit Modal ───────────────────────────────────── */}
      {showForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-xl">
            <div className="flex items-center justify-between p-6 pb-4 border-b border-slate-100">
              <h3 className="text-lg font-bold text-slate-900">{editingProduct ? 'Edit Product' : 'Add Product'}</h3>
              <button onClick={resetForm} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
            </div>
            <div className="p-6 space-y-3">
              <input value={form.name} onChange={(e) => setForm(prev => ({ ...prev, name: e.target.value }))} placeholder="Product Name *" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500/30" />
              <input value={form.product_title} onChange={(e) => setForm(prev => ({ ...prev, product_title: e.target.value }))} placeholder="Product Code" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500/30" />
              <input value={form.product_type} onChange={(e) => setForm(prev => ({ ...prev, product_type: e.target.value }))} placeholder="Type / Variety" className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500/30" />
              <textarea value={form.description} onChange={(e) => setForm(prev => ({ ...prev, description: e.target.value }))} placeholder="Describe this product — or let AI write it for you." rows={3} className="w-full px-3 py-2.5 pb-10 bg-white border border-slate-200 rounded-lg text-sm resize-none focus:outline-none focus:ring-1 focus:ring-blue-500/30" />

              {/* AI button wrapper — positioned over textarea bottom-right */}
              <div className="relative -mt-[38px] mb-[6px] flex justify-end pr-2.5 pointer-events-none">
                <div className="pointer-events-auto group/aibtn relative">

                  {/* hover tooltip */}
                  <div className="absolute bottom-full right-0 mb-2.5 w-64 bg-slate-900 rounded-xl p-3.5 shadow-2xl opacity-0 group-hover/aibtn:opacity-100 pointer-events-none transition-all duration-200 translate-y-1 group-hover/aibtn:translate-y-0 z-20">
                    {!form.name.trim() ? (
                      <>
                        <p className="text-[11px] font-semibold text-amber-400 uppercase tracking-wide mb-1.5">🔒 One thing missing</p>
                        <p className="text-xs text-slate-300 leading-relaxed mb-3">AI needs at least a product name to craft a great description. More detail = better copy.</p>
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            <span className="w-4 h-4 rounded-full bg-red-500/20 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" /></span>
                            <span className="text-xs text-red-300 font-medium">Product Name <span className="text-red-500/70 text-[10px]">← start here</span></span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="w-4 h-4 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-slate-500" /></span>
                            <span className="text-xs text-slate-400">Type / Variety <span className="text-slate-600 text-[10px]">optional but helpful</span></span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="w-4 h-4 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-slate-500" /></span>
                            <span className="text-xs text-slate-400">Images <span className="text-slate-600 text-[10px]">optional — AI uses if present</span></span>
                          </div>
                        </div>
                      </>
                    ) : (
                      <>
                        <p className="text-[11px] font-semibold text-emerald-400 uppercase tracking-wide mb-1.5">✦ Ready to generate</p>
                        <p className="text-xs text-slate-300 leading-relaxed mb-3">AI will use all filled fields to write a ~100-word marketing description.</p>
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                            <span className="text-xs text-emerald-300">Product Name</span>
                          </div>
                          {form.product_type && <div className="flex items-center gap-2">
                            <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                            <span className="text-xs text-emerald-300">Type / Variety</span>
                          </div>}
                          <div className="flex items-center gap-2">
                            <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                            <span className="text-xs text-emerald-300">Category <span className="text-slate-500 text-[10px]">({form.category})</span></span>
                          </div>
                          <div className="flex items-center gap-2">
                            {form.images?.length > 0
                              ? <span className="w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0"><Check size={9} className="text-emerald-400" /></span>
                              : <span className="w-4 h-4 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0"><span className="w-1.5 h-1.5 rounded-full bg-slate-500" /></span>}
                            <span className={`text-xs ${form.images?.length > 0 ? 'text-emerald-300' : 'text-slate-400'}`}>Images <span className="text-slate-600 text-[10px]">{form.images?.length > 0 ? `${form.images.length} uploaded` : 'none — text only'}</span></span>
                          </div>
                        </div>
                      </>
                    )}
                    <div className="absolute -bottom-[5px] right-5 w-2.5 h-2.5 bg-slate-900 rotate-45 rounded-sm" />
                  </div>

                  {/* nudge callout on locked-click */}
                  {genDescNudge && (
                    <div className="absolute bottom-full right-0 mb-10 w-56 bg-amber-950/95 border border-amber-500/30 rounded-xl px-3.5 py-2.5 shadow-2xl z-30 pointer-events-none">
                      <p className="text-xs text-amber-300 font-medium leading-snug">Give me a name first! 🎯</p>
                      <p className="text-[11px] text-amber-400/70 mt-0.5">Fill in <span className="text-amber-300 font-semibold">Product Name</span> above to unlock AI writing.</p>
                      <div className="absolute -bottom-[5px] right-5 w-2.5 h-2.5 bg-amber-950 rotate-45 rounded-sm border-r border-b border-amber-500/30" />
                    </div>
                  )}

                  {/* the button */}
                  <button
                    type="button"
                    onClick={generateProductDescription}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 ${
                      generatingDesc
                        ? 'bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow-md shadow-indigo-200 cursor-wait'
                        : form.name.trim()
                          ? 'bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow-md shadow-indigo-200 hover:from-indigo-600 hover:to-violet-600 hover:shadow-lg hover:shadow-indigo-300 hover:-translate-y-px active:translate-y-0 cursor-pointer'
                          : `bg-slate-800/80 text-slate-400 border border-slate-700/60 cursor-pointer hover:bg-slate-700/80 hover:text-slate-300 ${genDescNudge ? 'animate-bounce' : ''}`
                    }`}
                  >
                    {generatingDesc ? (
                      <><div className="w-3 h-3 border-[1.5px] border-white/30 border-t-white rounded-full animate-spin" /><span>Writing…</span></>
                    ) : form.name.trim() ? (
                      <><Wand2 size={11} /><span>Generate with AI</span></>
                    ) : (
                      <><Lock size={10} /><span>Generate with AI</span></>
                    )}
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="flex rounded-lg border border-slate-200 overflow-hidden bg-white">
                  <input
                    value={form.price}
                    onChange={(e) => setForm(prev => ({ ...prev, price: e.target.value }))}
                    placeholder="Price"
                    className="w-[75%] px-3 py-2.5 text-sm bg-white focus:outline-none"
                  />
                  <div className="w-px bg-slate-200 flex-shrink-0" />
                  <select
                    value={form.price_currency}
                    onChange={(e) => setForm(prev => ({ ...prev, price_currency: e.target.value }))}
                    className="w-[25%] px-1 py-2.5 text-xs bg-slate-50 text-slate-600 focus:outline-none cursor-pointer"
                  >
                    {['USD','EUR','GBP','INR','JPY','CAD','AUD','AED','SGD','CHF'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <select
                    value={allCategories.includes(form.category) ? form.category : (customCategories.includes(form.category) ? form.category : '__custom_active__')}
                    onChange={(e) => {
                      if (e.target.value === '__custom__') {
                        setShowCustomCategoryInput(true);
                      } else {
                        setShowCustomCategoryInput(false);
                        setForm(prev => ({ ...prev, category: e.target.value }));
                      }
                    }}
                    className="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-lg text-sm focus:outline-none"
                  >
                    {DEFAULT_CATEGORIES.map(c => (
                      <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
                    ))}
                    {customCategories.map(c => (
                      <option key={`custom-${c}`} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)} ★</option>
                    ))}
                    <option value="__custom__">+ Custom category...</option>
                  </select>
                  {showCustomCategoryInput && (
                    <div className="flex gap-1.5">
                      <input
                        value={customCategoryInput}
                        onChange={(e) => setCustomCategoryInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') addCustomCategory(customCategoryInput); }}
                        placeholder="Enter category name"
                        autoFocus
                        className="flex-1 px-3 py-1.5 bg-white border border-blue-300 rounded-lg text-xs focus:outline-none focus:ring-1 focus:ring-blue-500/30"
                      />
                      <button
                        type="button"
                        onClick={() => addCustomCategory(customCategoryInput)}
                        className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700"
                      >Add &amp; Lock</button>
                      <button type="button" onClick={() => { setShowCustomCategoryInput(false); setCustomCategoryInput(''); }} className="px-2 py-1.5 text-slate-400 hover:text-slate-600 text-xs">✕</button>
                    </div>
                  )}
                </div>
              </div>

              {editingProduct && editingProduct.public_url ? (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 text-xs space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-emerald-800">Public link</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${
                      editingProduct.link_source === 'manual'
                        ? 'bg-blue-100 text-blue-700'
                        : 'bg-emerald-100 text-emerald-700'
                    }`}>
                      {editingProduct.link_source === 'manual' ? 'Manual' : 'Auto'}
                    </span>
                  </div>
                  <a
                    href={editingProduct.public_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-emerald-700 hover:underline truncate"
                    data-testid="product-public-url"
                  >
                    {editingProduct.public_url}
                  </a>
                  {editingProduct.slug ? (
                    <p className="text-slate-500">slug: <code className="text-slate-700">{editingProduct.slug}</code></p>
                  ) : null}
                </div>
              ) : null}

              {/* Image upload */}
              <div
                onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
                onDragLeave={() => setDragActive(false)}
                onDrop={(e) => { e.preventDefault(); setDragActive(false); handleFiles(e.dataTransfer.files); }}
                className={`border-2 border-dashed rounded-lg p-4 transition-colors ${dragActive ? 'border-blue-400 bg-blue-50' : 'border-slate-300 bg-slate-50'}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs text-slate-500">Drag & drop up to {MAX_PRODUCT_IMAGES} images (JPG, PNG, WEBP, max {MAX_PRODUCT_IMAGE_SIZE_MB}MB)</p>
                  <label className="px-3 py-1.5 bg-white border border-slate-200 rounded-md text-xs font-medium text-slate-600 cursor-pointer hover:bg-slate-100">
                    Choose Files
                    <input type="file" accept="image/jpeg,image/png,image/webp" multiple className="hidden" onChange={(e) => handleFiles(e.target.files)} />
                  </label>
                </div>
              </div>

              {imageError && <p className="text-xs text-red-500">{imageError}</p>}

              {(form.images || []).length > 0 && (
                <div className="grid grid-cols-3 gap-3">
                  {form.images.map(img => (
                    <div key={img.id} className="relative rounded-lg overflow-hidden border border-slate-200 bg-white">
                      <img src={resolveMediaUrl(img.dataUrl)} alt={img.name} className="w-full h-24 object-cover" />
                      <button onClick={() => removeImage(img.id)} className="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/60 text-white text-xs flex items-center justify-center">×</button>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex gap-3 pt-1">
                <button onClick={resetForm} className="flex-1 py-2.5 text-sm font-medium text-slate-600 bg-slate-100 rounded-xl hover:bg-slate-200">Cancel</button>
                <button onClick={saveProduct} disabled={saving || !form.name.trim()} className="flex-1 py-2.5 text-sm font-medium text-white bg-blue-600 rounded-xl disabled:opacity-50 flex items-center justify-center gap-2">
                  {saving ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Save size={14} />}
                  {editingProduct ? 'Save Changes' : 'Add Product'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <BulkUploadModal
        isOpen={showBulkUpload}
        onClose={() => { if (!bulkUploading) setShowBulkUpload(false); }}
        title="Bulk Upload Products"
        subtitle="Import products from CSV or Excel in one pass, then create them in batch."
        entityLabel="Product"
        uploading={bulkUploading}
        onUpload={handleBulkUpload}
        templateHeaders={PRODUCT_BULK_TEMPLATE_HEADERS}
        templateSample={PRODUCT_BULK_TEMPLATE_SAMPLE}
        guideRows={PRODUCT_BULK_GUIDE_ROWS}
        requirementsText="Required minimum: name. Image URL columns and embedded XLSX images are imported with the original file."
      />

      {/* ── Delete Confirm Modal ───────────────────────────────── */}
      {deleteTarget && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-[70] flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-sm shadow-xl border border-slate-200" onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center flex-shrink-0">
                  <Trash2 size={18} className="text-red-500" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-slate-900">
                    {deleteTarget.products.length === 1 ? 'Delete product?' : `Delete ${deleteTarget.products.length} products?`}
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">This action cannot be undone.</p>
                </div>
              </div>
              {deleteTarget.products.length === 1 ? (
                <p className="text-sm text-slate-600 mb-5">
                  <span className="font-semibold text-slate-800">{deleteTarget.products[0].name}</span> will be permanently removed.
                </p>
              ) : (
                <div className="mb-5 max-h-36 overflow-y-auto space-y-1">
                  {deleteTarget.products.map(p => (
                    <div key={p.id} className="text-xs text-slate-600 px-2 py-1.5 bg-slate-50 rounded-lg truncate">{p.name}</div>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => setDeleteTarget(null)}
                  disabled={deletingBulk}
                  className="flex-1 py-2.5 text-sm font-medium text-slate-600 bg-slate-100 rounded-xl hover:bg-slate-200 disabled:opacity-50 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={confirmDelete}
                  disabled={deletingBulk}
                  className="flex-1 py-2.5 text-sm font-medium text-white bg-red-600 rounded-xl hover:bg-red-700 disabled:opacity-50 flex items-center justify-center gap-2 transition-colors"
                >
                  {deletingBulk
                    ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    : <Trash2 size={14} />}
                  Delete
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
