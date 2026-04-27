import { useState, useEffect, useCallback, useDeferredValue } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import BulkUploadModal from '@/components/BulkUploadModal';
import {
  Search,
  Plus,
  X,
  Mail,
  Phone,
  Tag,
  MessageSquare,
  AlertTriangle,
  ChevronRight,
  Trash2,
  AlertCircle,
} from 'lucide-react';
import { buildCustomerMethods, CHANNEL_META } from '@/lib/channelUtils';

const SEGMENT_COLORS = { vip: 'bg-amber-50 text-amber-600 border-amber-500/30', enterprise: 'bg-blue-50 text-blue-600 border-blue-200', growth: 'bg-cyan-50 text-cyan-600 border-cyan-500/30', general: 'bg-gray-700/50 text-slate-500 border-gray-600/30' };
const CUSTOMER_BULK_TEMPLATE_HEADERS = ['name', 'email', 'phone', 'company', 'segment', 'tags', 'channels'];
const CUSTOMER_BULK_TEMPLATE_SAMPLE = ['Jordan Reyes', 'jordan@brightworks.com', '+1 646 555 0199', 'BrightWorks', 'growth', 'vip, beta', 'email, whatsapp'];
const CUSTOMER_BULK_GUIDE_ROWS = [
  { column: 'name', help: 'Customer name. Use full name when possible.' },
  { column: 'email', help: 'Optional, but recommended for matching and email outreach.' },
  { column: 'phone', help: 'Optional, but recommended. International format works best, for example +1 646 555 0199.' },
  { column: 'company', help: 'Optional company or organization name.' },
  { column: 'segment', help: 'Optional segment such as general, growth, enterprise, or vip.' },
  { column: 'tags', help: 'Optional comma-separated tags.' },
  { column: 'channels', help: 'Optional comma-separated channels such as email, whatsapp, or instagram.' },
];

function CustomerCardSkeleton() {
  return (
    <div className="animate-pulse rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-full bg-slate-100" />
          <div className="space-y-1.5">
            <div className="h-3.5 w-36 rounded bg-slate-100" />
            <div className="h-3 w-24 rounded bg-slate-100" />
          </div>
        </div>
        <div className="h-5 w-12 rounded-full bg-slate-100" />
      </div>
      <div className="mt-3 flex gap-2">
        <div className="h-3 w-20 rounded bg-slate-100" />
        <div className="h-3 w-16 rounded bg-slate-100" />
      </div>
    </div>
  );
}

// buildCustomerMethods and related utils are now imported from @/lib/channelUtils.

export default function CustomersPage() {
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  const [customers, setCustomers] = useState([]);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search);
  const [filterSeg, setFilterSeg] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', email: '', phone: '', company: '', segment: 'general', tags: '' });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const [showMethodPicker, setShowMethodPicker] = useState(false);
  const [activeCustomerForMethods, setActiveCustomerForMethods] = useState(null);
  const [messageMethods, setMessageMethods] = useState([]);

  const [contactPrompt, setContactPrompt] = useState({ open: false, mode: 'message', customer: null, method: null });
  const [contactForm, setContactForm] = useState({ phone: '', email: '' });
  const [emailComposer, setEmailComposer] = useState({
    open: false,
    customer: null,
    subject: '',
    body: '',
    originalSubject: '',
    originalBody: '',
    sending: false,
  });
  const [deletingCustomerId, setDeletingCustomerId] = useState(null);
  const [showEditCustomer, setShowEditCustomer] = useState(false);
  const [editCustomerForm, setEditCustomerForm] = useState({ name: '', email: '', phone: '', company: '', segment: 'general', tags: '' });
  const [detailTab, setDetailTab] = useState('overview');
  const [customerProfile, setCustomerProfile] = useState(null);
  const [customerPurchases, setCustomerPurchases] = useState([]);
  const [customerJourney, setCustomerJourney] = useState([]);
  const [customerUnifiedProfile, setCustomerUnifiedProfile] = useState(null);
  const [showBulkUpload, setShowBulkUpload] = useState(false);
  const [bulkUploading, setBulkUploading] = useState(false);

  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const closeCustomerDetail = useCallback(() => {
    setSelected(null);
    setCustomerProfile(null);
    setCustomerPurchases([]);
    setCustomerJourney([]);
    setCustomerUnifiedProfile(null);
    if (searchParams.get('customer')) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete('customer');
      setSearchParams(nextParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const loadCustomers = useCallback(async () => {
    setLoadError('');
    try {
      const params = {};
      if (deferredSearch) params.search = deferredSearch;
      if (filterSeg) params.segment = filterSeg;
      const res = await api.get('/customers', { params });
      setCustomers(res.data);
    } catch (err) {
      console.error(err);
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 401 || status === 403) {
        setLoadError('Session expired. Please refresh the page.');
      } else {
        setLoadError(detail || 'Failed to load customers. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  }, [deferredSearch, filterSeg]);

  useEffect(() => { loadCustomers(); }, [loadCustomers]);
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        closeCustomerDetail();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [closeCustomerDetail]);

  const selectCustomer = useCallback(async (cust) => {
    setDetailTab('overview');
    setCustomerUnifiedProfile(null);
    if (cust?.id) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.set('customer', cust.id);
      setSearchParams(nextParams, { replace: true });
    }
    try {
      const res = await api.get(`/customers/${cust.id}`);
      setSelected(res.data);
      api.get(`/customers/${cust.id}/profile`).then(r => setCustomerProfile(r.data)).catch(() => setCustomerProfile(null));
      api.get('/purchases', { params: { customer_id: cust.id } }).then(r => setCustomerPurchases(r.data || [])).catch(() => setCustomerPurchases([]));
      api.get('/journey/tracking', { params: { customer_id: cust.id } }).then(r => setCustomerJourney(r.data || [])).catch(() => setCustomerJourney([]));
      api.get(`/identity/customer/${cust.id}`).then(r => {
        if (r.data?.unified) setCustomerUnifiedProfile(r.data.profile);
      }).catch(() => {});
    } catch (err) { setSelected(cust); }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    const requestedCustomerId = searchParams.get('customer');
    if (!requestedCustomerId || loading || selected?.id === requestedCustomerId) return;
    const customer = customers.find((item) => item.id === requestedCustomerId);
    if (customer) {
      selectCustomer(customer);
    }
  }, [customers, loading, searchParams, selectCustomer, selected?.id]);

  const upsertCustomerState = (customer) => {
    if (!customer) return;
    setCustomers((prev) => prev.map((item) => (item.id === customer.id ? { ...item, ...customer } : item)));
    if (selected?.id === customer.id) setSelected((prev) => ({ ...prev, ...customer }));
  };

  const createCustomer = async () => {
    try {
      await api.post('/customers', { ...form, tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean), channels: [] });
      showToast({
        type: 'success',
        title: 'Customer Added',
        message: `${form.name || 'The customer'} is ready to manage.`,
      });
      setShowForm(false);
      setForm({ name: '', email: '', phone: '', company: '', segment: 'general', tags: '' });
      loadCustomers();
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Create Failed',
        message: getErrorMessage(err, 'We could not add that customer.'),
      });
    }
  };

  const openInboxForCustomer = (customer, method) => {
    navigate(`/inbox?contactName=${encodeURIComponent(customer.name || 'Customer')}&contactPhone=${encodeURIComponent(customer.phone)}&channel=${encodeURIComponent(method.channel)}&source=${encodeURIComponent(method.source || 'customer_profile')}`);
  };

  const askForContacts = (customer, mode, method = null) => {
    setContactForm({ phone: customer?.phone || '', email: customer?.email || '' });
    setContactPrompt({ open: true, mode, customer, method });
  };

  const openMessagePicker = (customer) => {
    const methods = buildCustomerMethods(customer);
    if (methods.length === 0) {
      showToast({
        type: 'error',
        title: 'No Channels',
        message: 'No messaging channels are available for this customer yet.',
      });
      return;
    }
    setActiveCustomerForMethods(customer);
    setMessageMethods(methods);
    setShowMethodPicker(true);
  };

  const buildCustomerEmailDraft = (customer) => ({
    subject: `Pulse Engine follow up for ${customer?.name || 'customer'}`,
    body: `Hi ${customer?.name || 'there'},\n\nThis is a follow-up from Pulse Engine.\n\nBest regards,\nPulse Engine Team`,
  });

  const closeEmailComposer = () => {
    setEmailComposer({
      open: false,
      customer: null,
      subject: '',
      body: '',
      originalSubject: '',
      originalBody: '',
      sending: false,
    });
  };

  const openEmailComposer = (customer) => {
    if (!customer?.email) {
      askForContacts(customer, 'email');
      return;
    }
    const draft = buildCustomerEmailDraft(customer);
    setEmailComposer({
      open: true,
      customer,
      subject: draft.subject,
      body: draft.body,
      originalSubject: draft.subject,
      originalBody: draft.body,
      sending: false,
    });
  };

  const submitCustomerEmail = async (mode = 'direct') => {
    const customer = emailComposer.customer;
    if (!customer?.email || emailComposer.sending) return;

    const subject = mode === 'direct' ? emailComposer.originalSubject : emailComposer.subject.trim();
    const body = mode === 'direct' ? emailComposer.originalBody : emailComposer.body.trim();

    if (!subject) {
      showToast({
        type: 'error',
        title: 'Subject Missing',
        message: 'Add an email subject before sending.',
      });
      return;
    }
    if (!body) {
      showToast({
        type: 'error',
        title: 'Body Missing',
        message: 'Add email content before sending.',
      });
      return;
    }

    setEmailComposer((prev) => ({ ...prev, sending: true }));
    try {
      await api.post('/communications/email/send', {
        to_email: customer.email,
        subject,
        body,
      });
      closeEmailComposer();
      showToast({
        type: 'success',
        title: 'Email Sent',
        message: `${customer.name || customer.email} received an email on ${customer.email}.`,
      });
    } catch (err) {
      console.error(err);
      setEmailComposer((prev) => ({ ...prev, sending: false }));
      showToast({
        type: 'error',
        title: 'Email Failed',
        message: getErrorMessage(err, `We could not send an email to ${customer.email}.`),
      });
    }
  };

  const handlePickMethod = (method) => {
    if (!activeCustomerForMethods) return;
    setShowMethodPicker(false);
    if (!method.isPrimaryContact) {
      navigate(`/inbox?outbound=1&channel=${encodeURIComponent(method.channel)}&name=${encodeURIComponent(activeCustomerForMethods.name || 'Customer')}&phone=${encodeURIComponent(activeCustomerForMethods.phone || '')}`);
      return;
    }
    if (!activeCustomerForMethods.phone) {
      askForContacts(activeCustomerForMethods, 'message', method);
      return;
    }
    if (method.profileUrl) window.open(method.profileUrl, '_blank', 'noopener,noreferrer');
    openInboxForCustomer(activeCustomerForMethods, method);
  };

  const handleEmailCustomer = (customer) => {
    openEmailComposer(customer);
  };

  const submitContacts = async () => {
    const customer = contactPrompt.customer;
    if (!customer) return;

    const phone = contactForm.phone.trim();
    const email = contactForm.email.trim();

    if (contactPrompt.mode === 'message' && !phone) {
      showToast({
        type: 'error',
        title: 'Phone Missing',
        message: 'Add a phone number before starting a chat conversation.',
      });
      return;
    }

    if (contactPrompt.mode === 'email' && !email) {
      showToast({
        type: 'error',
        title: 'Email Missing',
        message: 'Add an email address before sending an email.',
      });
      return;
    }

    try {
      const updates = {};
      if (phone && phone !== (customer.phone || '')) updates.phone = phone;
      if (email && email !== (customer.email || '')) updates.email = email;

      let updatedCustomer = customer;
      if (Object.keys(updates).length > 0) {
        const res = await api.put(`/customers/${customer.id}`, updates);
        updatedCustomer = res.data;
        upsertCustomerState(updatedCustomer);
      }

      setContactPrompt({ open: false, mode: 'message', customer: null, method: null });

      if (contactPrompt.mode === 'email') {
        openEmailComposer(updatedCustomer);
        return;
      }

      if (contactPrompt.method?.profileUrl) {
        window.open(contactPrompt.method.profileUrl, '_blank', 'noopener,noreferrer');
      }
      openInboxForCustomer(updatedCustomer, contactPrompt.method || { channel: 'web_chat', source: 'customer_profile' });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save those contact details.'),
      });
    }
  };

  const deleteCustomer = async (customer, event) => {
    if (event) event.stopPropagation();
    requestConfirmation({
      title: 'Delete Customer',
      description: `Delete ${customer.name || 'this customer'} permanently. This action cannot be undone.`,
      confirmLabel: 'Delete customer',
      onConfirm: async () => {
        setDeletingCustomerId(customer.id);
        try {
          await api.delete(`/customers/${customer.id}`);
          setCustomers((prev) => prev.filter((item) => item.id !== customer.id));
          if (selected?.id === customer.id) closeCustomerDetail();
          showToast({
            type: 'success',
            title: 'Customer Deleted',
            message: `${customer.name || 'The customer'} was removed.`,
          });
        } catch (err) {
          console.error(err);
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, `We could not delete ${customer.name || 'that customer'}.`),
          });
        } finally {
          setDeletingCustomerId(null);
        }
      },
    });
  };

  const openEditCustomer = (customer) => {
    setEditCustomerForm({
      name: customer?.name || '',
      email: customer?.email || '',
      phone: customer?.phone || '',
      company: customer?.company || '',
      segment: customer?.segment || 'general',
      tags: Array.isArray(customer?.tags) ? customer.tags.join(', ') : '',
    });
    setShowEditCustomer(true);
  };

  const saveCustomerEdits = async () => {
    if (!selected) return;
    try {
      const payload = {
        ...editCustomerForm,
        tags: editCustomerForm.tags.split(',').map((t) => t.trim()).filter(Boolean),
      };
      const res = await api.put(`/customers/${selected.id}`, payload);
      upsertCustomerState(res.data);
      setShowEditCustomer(false);
      loadCustomers();
      showToast({
        type: 'success',
        title: 'Customer Updated',
        message: `${res.data?.name || selected.name || 'The customer'} was updated.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: getErrorMessage(err, `We could not update ${selected.name || 'that customer'}.`),
      });
    }
  };

  const handleBulkUpload = async (file) => {
    setBulkUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await api.post('/customers/bulk-upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const result = res.data || {};
      await loadCustomers();
      setShowBulkUpload(false);
      const message = `Created ${result.created || 0}, updated ${result.updated || 0}, skipped ${result.skipped || 0}.`;
      if (Array.isArray(result.errors) && result.errors.length > 0) {
        const firstError = result.errors[0];
        showToast({
          type: 'warning',
          title: 'Import Completed with Warnings',
          message: `${message} First issue: row ${firstError.row} - ${firstError.error}`,
        });
      } else {
        showToast({
          type: 'success',
          title: 'Customer Import Complete',
          message,
        });
      }
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Import Failed',
        message: getErrorMessage(err, 'We could not import that customer spreadsheet.'),
      });
    } finally {
      setBulkUploading(false);
    }
  };

  return (
    <>
    <div className="p-6 lg:p-8 space-y-6" data-testid="customers-page">
      {loadError && (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
          <span className="flex-1">{loadError}</span>
          <button
            type="button"
            onClick={() => setLoadError('')}
            className="flex-shrink-0 text-red-400 hover:text-red-600"
            aria-label="Dismiss error"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Customers</h1>
          <p className="text-slate-400 text-sm mt-1">{customers.length} total customers</p>
        </div>
        <div className="flex items-center gap-3">
          <button onClick={() => setShowBulkUpload(true)} className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-all hover:bg-slate-50" data-testid="bulk-upload-customers-btn">
            Bulk Upload
          </button>
          <button onClick={() => setShowForm(true)} className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15" data-testid="add-customer-btn">
            <Plus size={16} /> Add Customer
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search customers..." className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="customer-search" />
        </div>
        {['', 'vip', 'enterprise', 'growth', 'general'].map((s) => (
          <button key={s} onClick={() => setFilterSeg(s)} className={`px-3 py-1.5 text-xs rounded-lg font-medium capitalize transition-colors ${filterSeg === s ? 'bg-blue-50 text-blue-600 border border-blue-200' : 'text-slate-400 hover:text-slate-600 border border-slate-200'}`} data-testid={`filter-segment-${s || 'all'}`}>
            {s || 'All'}
          </button>
        ))}
      </div>

      <div className="bg-white border border-slate-100 rounded-xl overflow-hidden" data-testid="customer-table">
        <table className="w-full">
          <thead>
            <tr className="border-b border-slate-100">
              <th className="text-left text-xs text-slate-400 font-medium px-6 py-3">Customer</th>
              <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Company</th>
              <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Segment</th>
              <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">LTV</th>
              <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Sentiment</th>
              <th className="text-left text-xs text-slate-400 font-medium px-4 py-3">Conversations</th>
              <th className="text-right text-xs text-slate-400 font-medium px-6 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((cust) => (
              <tr key={cust.id} className="border-b border-slate-100 hover:bg-slate-50 transition-colors cursor-pointer" onClick={() => selectCustomer(cust)} data-testid={`customer-row-${cust.id}`}>
                <td className="px-6 py-4">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500/30 to-blue-600/30 flex items-center justify-center text-sm font-bold text-blue-600">{cust.name?.charAt(0)}</div>
                    <div>
                      <p className="text-sm font-medium text-slate-700">{cust.name}</p>
                      <p className="text-xs text-slate-400">{cust.email}</p>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-4 text-sm text-slate-500">{cust.company}</td>
                <td className="px-4 py-4">
                  <span className={`text-[10px] px-2 py-0.5 rounded-md font-medium border capitalize ${SEGMENT_COLORS[cust.segment] || SEGMENT_COLORS.general}`}>{cust.segment}</span>
                </td>
                <td className="px-4 py-4 text-sm text-slate-600 font-medium">${(cust.lifetime_value || 0).toLocaleString()}</td>
                <td className="px-4 py-4">
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${cust.avg_sentiment > 0.3 ? 'bg-emerald-500' : cust.avg_sentiment < -0.3 ? 'bg-red-500' : 'bg-gray-500'}`}></div>
                    <span className="text-xs text-slate-500">{cust.avg_sentiment?.toFixed(1)}</span>
                  </div>
                </td>
                <td className="px-4 py-4 text-sm text-slate-500">{cust.total_conversations}</td>
                <td className="px-6 py-4 text-right">
                  <button
                    onClick={(e) => { e.stopPropagation(); openMessagePicker(cust); }}
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-blue-200 text-blue-600 hover:bg-blue-50 mr-2"
                    data-testid={`message-customer-${cust.id}`}
                  >
                    <MessageSquare size={12} /> Message
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleEmailCustomer(cust); }}
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-emerald-200 text-emerald-700 hover:bg-emerald-50 mr-2"
                    data-testid={`email-customer-${cust.id}`}
                  >
                    <Mail size={12} /> Email
                  </button>
                  <button
                    onClick={(e) => deleteCustomer(cust, e)}
                    disabled={deletingCustomerId === cust.id}
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-red-200 text-red-700 hover:bg-red-50 mr-2 disabled:opacity-60"
                    data-testid={`delete-customer-${cust.id}`}
                  >
                    <Trash2 size={12} /> Delete
                  </button>
                  <ChevronRight size={16} className="text-slate-300 inline" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div
          className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          data-testid="customer-detail-modal"
          onClick={closeCustomerDetail}
        >
          <div
            className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg max-h-[80vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-6">
              <div className="flex items-start justify-between mb-6">
                <div className="flex items-center gap-4">
                  <div className="w-14 h-14 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-xl font-bold text-white">{selected.name?.charAt(0)}</div>
                  <div>
                    <h3 className="text-xl font-bold text-slate-900">{selected.name}</h3>
                    <p className="text-sm text-slate-400">{selected.company}</p>
                    <span className={`inline-block mt-1 text-[10px] px-2 py-0.5 rounded-md border font-medium capitalize ${SEGMENT_COLORS[selected.segment] || SEGMENT_COLORS.general}`}>{selected.segment}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEditCustomer(selected)}
                    className="px-2.5 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 rounded-lg text-xs font-medium hover:bg-slate-100 transition-colors"
                    data-testid="edit-customer-btn"
                  >
                    Edit
                  </button>
                  <button onClick={closeCustomerDetail} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3 mb-4">
                <div className="bg-slate-50 rounded-lg p-3 text-center">
                  <p className="text-lg font-bold text-slate-900">${((selected.lifetime_value || 0) / 1000).toFixed(0)}k</p>
                  <p className="text-[10px] text-slate-400">LTV</p>
                </div>
                <div className="bg-slate-50 rounded-lg p-3 text-center">
                  <p className="text-lg font-bold text-slate-900">{selected.total_conversations}</p>
                  <p className="text-[10px] text-slate-400">Conversations</p>
                </div>
                <div className="bg-slate-50 rounded-lg p-3 text-center">
                  <p className={`text-lg font-bold ${selected.avg_sentiment > 0.3 ? 'text-emerald-600' : selected.avg_sentiment < -0.3 ? 'text-red-500' : 'text-slate-600'}`}>{selected.avg_sentiment?.toFixed(1)}</p>
                  <p className="text-[10px] text-slate-400">Sentiment</p>
                </div>
              </div>

              {/* Unified Profile Badge */}
              {customerUnifiedProfile && (
                <div className="mb-4 p-3 bg-gradient-to-r from-indigo-50 to-purple-50 border border-indigo-200 rounded-xl">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
                      {(customerUnifiedProfile.display_name || '?').charAt(0)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[9px] font-bold text-indigo-600 bg-indigo-100 px-1.5 py-0.5 rounded-full uppercase tracking-wider">Unified Profile</span>
                        <span className="text-[9px] text-slate-400">{customerUnifiedProfile.members?.length || 0} linked profiles</span>
                      </div>
                      <p className="text-xs text-slate-600 mt-0.5 truncate">
                        Platforms: {customerUnifiedProfile.platforms_used || 'N/A'} • {customerUnifiedProfile.total_interactions || 0} total interactions
                      </p>
                    </div>
                    <button onClick={() => navigate('/unification')}
                      className="text-[10px] font-medium text-indigo-600 hover:text-indigo-700 px-2 py-1 rounded-lg hover:bg-indigo-50 flex-shrink-0">
                      View All →
                    </button>
                  </div>
                  {/* Show linked profiles */}
                  <div className="mt-2 flex items-center gap-1 flex-wrap">
                    {(customerUnifiedProfile.members || []).map((m) => (
                      <span key={m.customer_id} className={`text-[9px] px-1.5 py-0.5 rounded-full border font-medium ${m.is_primary ? 'bg-indigo-100 text-indigo-700 border-indigo-200' : 'bg-white text-slate-500 border-slate-200'}`}>
                        {m.name || 'Unknown'} ({m.match_method?.replace('auto_', '').replace('manual', 'Manual') || 'Match'})
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="flex gap-1 mb-4 border-b border-slate-100">
                {['overview', 'profile', 'purchases', 'journey'].map(tab => (
                  <button key={tab} onClick={() => setDetailTab(tab)} className={`px-3 py-1.5 text-xs font-medium capitalize rounded-t-lg transition-colors ${detailTab === tab ? 'bg-blue-50 text-blue-600 border-b-2 border-blue-600' : 'text-slate-400 hover:text-slate-600'}`}>{tab}</button>
                ))}
              </div>

              {detailTab === 'overview' && (<>
                <div>
                {selected.email && <p className="text-sm text-slate-600 flex items-center gap-2"><Mail size={14} className="text-slate-400" /> {selected.email}</p>}
                {selected.phone && <p className="text-sm text-slate-600 flex items-center gap-2"><Phone size={14} className="text-slate-400" /> {selected.phone}</p>}
              </div>

              <div className="grid grid-cols-2 gap-2 mb-6">
                <button
                  onClick={() => openMessagePicker(selected)}
                  className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100"
                  data-testid="message-customer-detail-btn"
                >
                  <MessageSquare size={14} /> Message
                </button>
                <button
                  onClick={() => handleEmailCustomer(selected)}
                  className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                  data-testid="email-customer-detail-btn"
                >
                  <Mail size={14} /> Email
                </button>
              </div>
              <button
                onClick={() => deleteCustomer(selected)}
                disabled={deletingCustomerId === selected.id}
                className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 mb-6 disabled:opacity-60"
                data-testid="delete-customer-detail-btn"
              >
                <Trash2 size={14} /> {deletingCustomerId === selected.id ? 'Deleting...' : 'Delete Customer'}
              </button>

              {selected.churn_risk && (
                <div className={`p-4 rounded-xl border mb-6 ${selected.churn_risk.risk_level === 'critical' || selected.churn_risk.risk_level === 'high' ? 'bg-red-50 border-red-500/20' : 'bg-slate-50 border-slate-200'}`}>
                  <div className="flex items-center gap-2 mb-2">
                    <AlertTriangle size={14} className={selected.churn_risk.risk_level === 'low' ? 'text-emerald-600' : 'text-amber-600'} />
                    <span className="text-xs font-medium text-slate-600 capitalize">Churn Risk: {selected.churn_risk.risk_level}</span>
                    <span className="text-xs font-bold text-slate-700 ml-auto">{Math.round(selected.churn_risk.risk_score * 100)}%</span>
                  </div>
                  <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${selected.churn_risk.risk_score > 0.7 ? 'bg-red-500' : selected.churn_risk.risk_score > 0.3 ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${selected.churn_risk.risk_score * 100}%` }}></div>
                  </div>
                  {selected.churn_risk.factors?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {selected.churn_risk.factors.map((f, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 bg-slate-50 rounded text-slate-500">{f}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div>
                <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-2">Tags</p>
                <div className="flex flex-wrap gap-1.5">
                  {(selected.tags || []).map((tag) => (
                    <span key={tag} className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200"><Tag size={10} className="inline mr-1" />{tag}</span>
                  ))}
                </div>
              </div>
              </>)}

              {detailTab === 'profile' && (
                <div className="space-y-3">
                  <p className="text-xs font-medium text-slate-600">Customer Profile</p>
                  {customerProfile ? (
                    <>
                      <div className="bg-slate-50 rounded-lg p-3">
                        <p className="text-[10px] text-slate-400 uppercase mb-1">Engagement Level</p>
                        <p className="text-sm font-medium text-slate-700 capitalize">{customerProfile.engagement_level || 'N/A'}</p>
                      </div>
                      {customerProfile.preferences && Object.keys(customerProfile.preferences).length > 0 && (
                        <div className="bg-slate-50 rounded-lg p-3">
                          <p className="text-[10px] text-slate-400 uppercase mb-1">Preferences</p>
                          {Object.entries(customerProfile.preferences).map(([k, v]) => (
                            <p key={k} className="text-xs text-slate-600"><span className="font-medium">{k}:</span> {String(v)}</p>
                          ))}
                        </div>
                      )}
                      {customerProfile.last_interaction && (
                        <p className="text-xs text-slate-400">Last interaction: {new Date(customerProfile.last_interaction).toLocaleDateString()}</p>
                      )}
                    </>
                  ) : <p className="text-xs text-slate-400">No profile data yet.</p>}
                </div>
              )}

              {detailTab === 'purchases' && (
                <div className="space-y-3">
                  <p className="text-xs font-medium text-slate-600">Purchase History</p>
                  {customerPurchases.length > 0 ? customerPurchases.map(p => (
                    <div key={p.id} className="bg-slate-50 rounded-lg p-3 flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-slate-700">{p.product_category || 'Purchase'}</p>
                        <p className="text-[10px] text-slate-400">{new Date(p.purchase_date || p.created_at).toLocaleDateString()}</p>
                      </div>
                      <p className="text-sm font-bold text-slate-900">{p.currency || '$'}{p.amount?.toFixed(2)}</p>
                    </div>
                  )) : <p className="text-xs text-slate-400">No purchases recorded.</p>}
                </div>
              )}

              {detailTab === 'journey' && (
                <div className="space-y-3">
                  <p className="text-xs font-medium text-slate-600">Customer Journey</p>
                  {customerJourney.length > 0 ? customerJourney.map(j => (
                    <div key={j.id} className="flex items-center gap-3">
                      <div className="w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                      <div>
                        <p className="text-sm font-medium text-slate-700 capitalize">{j.phase}</p>
                        <p className="text-[10px] text-slate-400">{new Date(j.started_at || j.created_at).toLocaleDateString()}</p>
                      </div>
                    </div>
                  )) : <p className="text-xs text-slate-400">No journey records yet.</p>}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {showEditCustomer && selected && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="edit-customer-modal" onClick={() => setShowEditCustomer(false)}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-900">Edit Customer</h3>
                <button onClick={() => setShowEditCustomer(false)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="space-y-4">
                <input value={editCustomerForm.name} onChange={(e) => setEditCustomerForm({ ...editCustomerForm, name: e.target.value })} placeholder="Name" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <input value={editCustomerForm.email} onChange={(e) => setEditCustomerForm({ ...editCustomerForm, email: e.target.value })} placeholder="Email" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <input value={editCustomerForm.phone} onChange={(e) => setEditCustomerForm({ ...editCustomerForm, phone: e.target.value })} placeholder="Phone" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <input value={editCustomerForm.company} onChange={(e) => setEditCustomerForm({ ...editCustomerForm, company: e.target.value })} placeholder="Company" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <select value={editCustomerForm.segment} onChange={(e) => setEditCustomerForm({ ...editCustomerForm, segment: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm">
                  <option value="general">General</option>
                  <option value="growth">Growth</option>
                  <option value="enterprise">Enterprise</option>
                  <option value="vip">VIP</option>
                </select>
                <input value={editCustomerForm.tags} onChange={(e) => setEditCustomerForm({ ...editCustomerForm, tags: e.target.value })} placeholder="Tags (comma separated)" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm" />
                <button onClick={saveCustomerEdits} className="w-full py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700">Save Changes</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showMethodPicker && activeCustomerForMethods && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="customer-message-methods-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-md">
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-bold text-slate-900">Choose Message Method</h3>
                <button onClick={() => setShowMethodPicker(false)} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
              </div>
              <p className="text-sm text-slate-500 mb-4">Email is available from the separate Email button.</p>
              <div className="space-y-2">
                {messageMethods.map((method, idx) => {
                  const meta = CHANNEL_META[method.channel] || CHANNEL_META.whatsapp;
                  const Icon = meta.icon;
                  return (
                    <button
                      key={`${method.channel}-${idx}`}
                      onClick={() => handlePickMethod(method)}
                      className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg border border-slate-200 hover:border-blue-200 hover:bg-blue-50/40"
                      data-testid={`customer-method-${method.channel}-${idx}`}
                    >
                      <span className="inline-flex items-center gap-2 text-sm text-slate-700"><Icon size={14} /> {meta.label}</span>
                      <span className="text-xs text-slate-400">{method.isPrimaryContact ? `Person is on ${meta.label}` : 'Open'}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {contactPrompt.open && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="customer-contact-prompt-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-md">
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-bold text-slate-900">Add Contact Details</h3>
                <button onClick={() => setContactPrompt({ open: false, mode: 'message', customer: null, method: null })} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
              </div>
              <p className="text-sm text-slate-500 mb-4">
                {contactPrompt.mode === 'email' ? 'Email is required before sending an email.' : 'Phone is required before starting a chat. Add any missing contacts below.'}
              </p>
              <div className="space-y-3">
                <input
                  value={contactForm.phone}
                  onChange={(e) => setContactForm({ ...contactForm, phone: e.target.value })}
                  placeholder="Phone"
                  className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm"
                />
                <input
                  value={contactForm.email}
                  onChange={(e) => setContactForm({ ...contactForm, email: e.target.value })}
                  placeholder="Email"
                  className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm"
                />
                <button onClick={submitContacts} className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">Save and Continue</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {emailComposer.open && emailComposer.customer && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="customer-email-composer-modal" onClick={closeEmailComposer}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-xl" onClick={(e) => e.stopPropagation()}>
            <div className="p-6 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-bold text-slate-900">Send Email</h3>
                  <p className="text-sm text-slate-500">Send the prepared draft as-is, or edit it first.</p>
                </div>
                <button onClick={closeEmailComposer} className="text-slate-400 hover:text-slate-600">
                  <X size={18} />
                </button>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">To</label>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700">
                    {emailComposer.customer.email}
                  </div>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Subject</label>
                  <input
                    value={emailComposer.subject}
                    onChange={(e) => setEmailComposer((prev) => ({ ...prev, subject: e.target.value }))}
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Body</label>
                  <textarea
                    value={emailComposer.body}
                    onChange={(e) => setEmailComposer((prev) => ({ ...prev, body: e.target.value }))}
                    rows={7}
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 resize-none"
                  />
                </div>
              </div>

              <p className="text-xs text-slate-400">
                “Send As Is” uses the original generated draft. “Send Edited Email” uses the subject and body shown above.
              </p>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => { void submitCustomerEmail('direct'); }}
                  disabled={emailComposer.sending}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {emailComposer.sending ? 'Sending...' : 'Send As Is'}
                </button>
                <button
                  type="button"
                  onClick={() => { void submitCustomerEmail('edited'); }}
                  disabled={emailComposer.sending}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                >
                  {emailComposer.sending ? 'Sending...' : 'Send Edited Email'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="new-customer-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-lg">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-900">Add Customer</h3>
                <button onClick={() => setShowForm(false)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="space-y-4">
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Name *" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-cust-name" required />
                <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="Email" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-cust-email" />
                <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="Phone" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-cust-phone" />
                <input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} placeholder="Company" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-cust-company" />
                <select value={form.segment} onChange={(e) => setForm({ ...form, segment: e.target.value })} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-cust-segment">
                  <option value="general">General</option>
                  <option value="growth">Growth</option>
                  <option value="enterprise">Enterprise</option>
                  <option value="vip">VIP</option>
                </select>
                <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="Tags (comma separated)" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="new-cust-tags" />
                <button onClick={createCustomer} className="w-full py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15" data-testid="create-customer-submit">Add Customer</button>
              </div>
            </div>
          </div>
        </div>
      )}

      <BulkUploadModal
        isOpen={showBulkUpload}
        onClose={() => { if (!bulkUploading) setShowBulkUpload(false); }}
        title="Bulk Upload Customers"
        subtitle="Import a spreadsheet of customers in one pass, with duplicate-safe create or update behavior."
        entityLabel="Customer"
        uploading={bulkUploading}
        onUpload={handleBulkUpload}
        templateHeaders={CUSTOMER_BULK_TEMPLATE_HEADERS}
        templateSample={CUSTOMER_BULK_TEMPLATE_SAMPLE}
        guideRows={CUSTOMER_BULK_GUIDE_ROWS}
      />

      {loading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Loading customers">
          {Array.from({ length: 6 }).map((_, i) => <CustomerCardSkeleton key={i} />)}
        </div>
      )}
    </div>
    {confirmDialog}
    </>
  );
}
