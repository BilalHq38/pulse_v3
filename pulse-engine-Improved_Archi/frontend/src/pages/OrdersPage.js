import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import {
  ClipboardList,
  Search,
  RefreshCw,
  Eye,
  Check,
  X,
  MessageSquare,
  Package,
  Truck,
} from 'lucide-react';

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'collecting_details', label: 'Collecting details' },
  { value: 'awaiting_confirmation', label: 'Awaiting confirmation' },
  { value: 'admin_review', label: 'Admin review' },
  { value: 'placed', label: 'Placed' },
  { value: 'pending', label: 'Pending' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'shipped', label: 'Shipped' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
];

const STATUS_LABELS = STATUS_OPTIONS.reduce((acc, item) => {
  if (item.value) acc[item.value] = item.label;
  return acc;
}, {});

function formatStatus(status) {
  return STATUS_LABELS[status] || String(status || 'Unknown').replace(/_/g, ' ');
}

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
}

function customerLabel(order) {
  return order.customer_name || order.customer_phone || order.conversation_customer_name || 'Customer';
}

function StatusBadge({ status }) {
  let tone;
  switch (status) {
    case 'cancelled':
      tone = 'bg-red-50 text-red-700 border-red-100';
      break;
    case 'completed':
    case 'delivered':
      tone = 'bg-emerald-50 text-emerald-700 border-emerald-100';
      break;
    case 'shipped':
      tone = 'bg-indigo-50 text-indigo-700 border-indigo-100';
      break;
    case 'confirmed':
      tone = 'bg-blue-50 text-blue-700 border-blue-100';
      break;
    case 'pending':
      tone = 'bg-slate-100 text-slate-700 border-slate-200';
      break;
    default:
      tone = 'bg-amber-50 text-amber-700 border-amber-100';
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-1 text-xs font-medium ${tone}`}>
      {formatStatus(status)}
    </span>
  );
}

export default function OrdersPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState('');
  const selectedOrderId = searchParams.get('order') || '';

  const loadOrders = useCallback(async (options = {}) => {
    const silent = Boolean(options?.silent);
    if (!silent) setLoading(true);
    try {
      const res = await api.get('/orders', {
        params: {
          status: status || undefined,
          customer: search || undefined,
          limit: 200,
        },
      });
      setOrders(Array.isArray(res.data?.items) ? res.data.items : []);
    } catch (err) {
      if (!silent) {
        showToast({
          type: 'error',
          title: 'Orders unavailable',
          message: getErrorMessage(err, 'We could not load orders.'),
        });
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [search, status]);

  const loadOrderDetail = useCallback(async (orderId) => {
    if (!orderId) return;
    setDetailLoading(true);
    try {
      const res = await api.get(`/orders/${orderId}`);
      setSelected(res.data || null);
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set('order', orderId);
        return next;
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Order unavailable',
        message: getErrorMessage(err, 'We could not load that order.'),
      });
    } finally {
      setDetailLoading(false);
    }
  }, [setSearchParams]);

  useEffect(() => {
    loadOrders();
  }, [loadOrders]);

  useEffect(() => {
    const refresh = () => loadOrders({ silent: true });
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, 15000);
    return () => {
      window.removeEventListener('focus', onFocus);
      window.clearInterval(interval);
    };
  }, [loadOrders]);

  useEffect(() => {
    if (selectedOrderId && !selected) {
      loadOrderDetail(selectedOrderId);
    }
  }, [loadOrderDetail, selected, selectedOrderId]);

  const closeDetail = () => {
    setSelected(null);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('order');
      return next;
    });
  };

  const updateStatus = async (orderId, nextStatus) => {
    setActionLoading(`${orderId}:${nextStatus}`);
    try {
      const res = await api.patch(`/orders/${orderId}/status`, { status: nextStatus });
      const updated = res.data || {};
      setOrders((prev) => prev.map((order) => (order.id === orderId ? { ...order, ...updated } : order)));
      setSelected((prev) => (prev && prev.id === orderId ? { ...prev, ...updated } : prev));
      showToast({
        type: 'success',
        title: 'Order updated',
        message: `Order marked ${formatStatus(nextStatus)}.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Update failed',
        message: getErrorMessage(err, 'We could not update that order.'),
      });
    } finally {
      setActionLoading('');
    }
  };

  const visibleOrders = useMemo(() => orders, [orders]);

  return (
    <div className="min-h-screen bg-slate-50 px-4 py-6 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-slate-500">
              <ClipboardList size={18} />
              <span className="text-sm font-medium">Orders</span>
            </div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-900">Order Management</h1>
          </div>
          <button
            type="button"
            onClick={() => loadOrders()}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
          >
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>

        <div className="mb-4 grid gap-3 md:grid-cols-[220px_1fr]">
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none"
            aria-label="Filter by status"
          >
            {STATUS_OPTIONS.map((item) => (
              <option key={item.value || 'all'} value={item.value}>{item.label}</option>
            ))}
          </select>
          <div className="relative">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search customer, phone, or product"
              className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none"
            />
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Order</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Customer</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Product</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Qty</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Status</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Channel</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Created</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {loading ? (
                  <tr>
                    <td colSpan="8" className="px-4 py-10 text-center text-sm text-slate-500">Loading orders...</td>
                  </tr>
                ) : visibleOrders.length === 0 ? (
                  <tr>
                    <td colSpan="8" className="px-4 py-10 text-center text-sm text-slate-500">No orders found.</td>
                  </tr>
                ) : visibleOrders.map((order) => (
                  <tr key={order.id} data-testid={`order-row-${order.id}`} className="hover:bg-slate-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-slate-900">{String(order.id || '').slice(0, 8)}</td>
                    <td className="px-4 py-3 text-sm text-slate-700">{customerLabel(order)}</td>
                    <td className="px-4 py-3 text-sm text-slate-700">
                      <div className="flex max-w-xs items-center gap-2 truncate">
                        <Package size={15} className="shrink-0 text-slate-400" />
                        <span className="truncate">{order.product_name || 'Product pending'}</span>
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-700">{order.quantity || '-'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm"><StatusBadge status={order.status} /></td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-700">{order.source_channel || '-'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-500">{formatDate(order.created_at)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                      <div className="inline-flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => loadOrderDetail(order.id)}
                          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
                          aria-label="View order"
                        >
                          <Eye size={16} />
                        </button>
                        {order.status === 'admin_review' || order.status === 'placed' ? (
                          <button
                            type="button"
                            disabled={Boolean(actionLoading)}
                            onClick={() => updateStatus(order.id, 'confirmed')}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-emerald-200 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                            aria-label="Mark confirmed"
                          >
                            <Check size={16} />
                          </button>
                        ) : null}
                        {order.status === 'confirmed' ? (
                          <button
                            type="button"
                            disabled={Boolean(actionLoading)}
                            onClick={() => updateStatus(order.id, 'shipped')}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-indigo-200 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"
                            aria-label="Mark shipped"
                          >
                            <Truck size={16} />
                          </button>
                        ) : null}
                        {order.status === 'shipped' ? (
                          <button
                            type="button"
                            disabled={Boolean(actionLoading)}
                            onClick={() => updateStatus(order.id, 'delivered')}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-emerald-200 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                            aria-label="Mark delivered"
                          >
                            <Package size={16} />
                          </button>
                        ) : null}
                        {order.status === 'delivered' ? (
                          <button
                            type="button"
                            disabled={Boolean(actionLoading)}
                            onClick={() => updateStatus(order.id, 'completed')}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-blue-200 text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                            aria-label="Mark completed"
                          >
                            <Check size={16} />
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {selected ? (
        <div
          data-testid="order-detail-modal"
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          onClick={closeDetail}
        >
          <div
            className="w-full max-w-2xl rounded-lg bg-white shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Order {String(selected.id || '').slice(0, 8)}</h2>
                <p className="text-sm text-slate-500">{formatDate(selected.created_at)}</p>
              </div>
              <button
                type="button"
                onClick={closeDetail}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100"
                aria-label="Close order detail"
              >
                <X size={18} />
              </button>
            </div>
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
              <Detail label="Customer" value={customerLabel(selected)} />
              <Detail label="Email" value={selected.customer_email} />
              <Detail label="Phone" value={selected.customer_phone} />
              <Detail label="Product" value={selected.product_name} />
              <Detail label="Quantity" value={selected.quantity} />
              <Detail label="Status" value={<StatusBadge status={selected.status} />} />
              <Detail label="Channel" value={selected.source_channel} />
              <Detail label="Assigned" value={selected.assigned_name || selected.assigned_to} />
              <div className="sm:col-span-2">
                <Detail label="Delivery address" value={selected.delivery_address} />
              </div>
              {selected.notes ? (
                <div className="sm:col-span-2">
                  <Detail label="Notes" value={selected.notes} />
                </div>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-4">
              <button
                type="button"
                onClick={() => navigate(`/inbox?conversation=${selected.conversation_id || ''}`)}
                disabled={!selected.conversation_id}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                <MessageSquare size={16} />
                Open conversation
              </button>
              <div className="flex flex-wrap items-center gap-2">
                {selected.status === 'admin_review' || selected.status === 'placed' ? (
                  <button
                    type="button"
                    disabled={Boolean(actionLoading)}
                    onClick={() => updateStatus(selected.id, 'confirmed')}
                    className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <Check size={16} />
                    Confirm
                  </button>
                ) : null}
                {selected.status === 'confirmed' ? (
                  <button
                    type="button"
                    disabled={Boolean(actionLoading)}
                    onClick={() => updateStatus(selected.id, 'shipped')}
                    className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    <Truck size={16} />
                    Mark shipped
                  </button>
                ) : null}
                {selected.status === 'shipped' ? (
                  <button
                    type="button"
                    disabled={Boolean(actionLoading)}
                    onClick={() => updateStatus(selected.id, 'delivered')}
                    className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <Package size={16} />
                    Mark delivered
                  </button>
                ) : null}
                {selected.status === 'delivered' ? (
                  <button
                    type="button"
                    disabled={Boolean(actionLoading)}
                    onClick={() => updateStatus(selected.id, 'completed')}
                    className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    <Check size={16} />
                    Complete
                  </button>
                ) : null}
                {!['cancelled', 'completed'].includes(selected.status) ? (
                  <button
                    type="button"
                    disabled={Boolean(actionLoading)}
                    onClick={() => updateStatus(selected.id, 'cancelled')}
                    className="inline-flex items-center gap-2 rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                  >
                    <X size={16} />
                    Cancel
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {detailLoading && !selected ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/20 text-sm text-white">
          Loading order...
        </div>
      ) : null}
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-sm text-slate-900">{value || '-'}</div>
    </div>
  );
}
