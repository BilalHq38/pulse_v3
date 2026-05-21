import { useState, useEffect, useCallback, Fragment } from 'react';
import api from '@/lib/api';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import {
  Shield, Users, MessageSquare, Target, UserCheck, Package, Ticket,
  Search, RefreshCw, ChevronDown, ChevronUp, ChevronRight, Building2, Mail, Phone,
  Calendar, Activity, Pause, Ban, CheckCircle, XCircle, Loader,
  Eye, Globe, Clock, Filter, UserPlus, Trash2
} from 'lucide-react';

export default function SuperAdminDashboardPage() {
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [expandedTree, setExpandedTree] = useState(new Set());
  const [actionLoading, setActionLoading] = useState('');
  const [expandedUser, setExpandedUser] = useState(null);
  const [sortBy, setSortBy] = useState('created_at');
  const [sortDir, setSortDir] = useState('desc');
  const [activeTab, setActiveTab] = useState('users');
  const [tenants, setTenants] = useState([]);
  const [tenantsLoading, setTenantsLoading] = useState(false);
  const [planEditTenant, setPlanEditTenant] = useState(null);
  const [planEditForm, setPlanEditForm] = useState({ monthly_conversation_limit: '', max_users: '' });
  const [planSaveLoading, setPlanSaveLoading] = useState('');
  const [adminLogs, setAdminLogs] = useState([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logSearch, setLogSearch] = useState('');
  const [logLevelFilter, setLogLevelFilter] = useState('all');
  const [logCompanyFilter, setLogCompanyFilter] = useState('all');
  const [authLogs, setAuthLogs] = useState([]);
  const [loginSessions, setLoginSessions] = useState([]);
  const [loadingSecurity, setLoadingSecurity] = useState(false);
  const [visitors, setVisitors] = useState([]);
  const [loadingVisitors, setLoadingVisitors] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [overviewRes, usersRes] = await Promise.all([
        api.get('/admin/overview'),
        api.get('/admin/users'),
      ]);
      setOverview(overviewRes.data);
      setUsers(usersRes.data || []);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load data');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const fetchLogs = useCallback(async () => {
    setLoadingLogs(true);
    try {
      const r = await api.get('/admin/logs?limit=200');
      setAdminLogs(r.data || []);
    } catch (e) { /* ignore */ } finally { setLoadingLogs(false); }
  }, []);

  const fetchSecurity = useCallback(async () => {
    setLoadingSecurity(true);
    try {
      const [authR, sessR] = await Promise.all([
        api.get('/admin/auth-logs?limit=100'),
        api.get('/admin/login-sessions?limit=100'),
      ]);
      setAuthLogs(authR.data || []);
      setLoginSessions(sessR.data || []);
    } catch (e) { /* ignore */ } finally { setLoadingSecurity(false); }
  }, []);

  const fetchTenants = useCallback(async () => {
    setTenantsLoading(true);
    try {
      const r = await api.get('/admin/tenants?limit=500');
      setTenants(r.data?.tenants || []);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load tenants');
    } finally {
      setTenantsLoading(false);
    }
  }, []);

  const fetchVisitors = useCallback(async () => {
    setLoadingVisitors(true);
    try {
      const r = await api.get('/visitor/tracking?limit=100');
      setVisitors(r.data?.sessions || []);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load visitor tracking');
    } finally {
      setLoadingVisitors(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'logs') fetchLogs();
    if (activeTab === 'security') fetchSecurity();
    if (activeTab === 'tenants') fetchTenants();
    if (activeTab === 'visitors') fetchVisitors();
  }, [activeTab, fetchLogs, fetchSecurity, fetchTenants, fetchVisitors]);

  const openPlanEdit = (t) => {
    setPlanEditTenant(t);
    setPlanEditForm({
      monthly_conversation_limit: String(t.monthly_conversation_limit ?? ''),
      max_users: String(t.max_users_per_account ?? ''),
    });
  };

  const savePlanLimits = async () => {
    if (!planEditTenant?.company_id) return;
    setPlanSaveLoading(planEditTenant.company_id);
    try {
      const payload = {};
      const mc = planEditForm.monthly_conversation_limit.trim();
      const mu = planEditForm.max_users.trim();
      if (mc !== '') payload.monthly_conversation_limit = parseInt(mc, 10);
      if (mu !== '') payload.max_users = parseInt(mu, 10);
      if (!Object.keys(payload).length) {
        showToast({
          type: 'error',
          title: 'Limits Missing',
          message: 'Enter at least one limit before saving plan changes.',
        });
        return;
      }
      await api.put(`/admin/tenants/${planEditTenant.company_id}/plan-limits`, payload);
      showToast({
        type: 'success',
        title: 'Limits Updated',
        message: `${planEditTenant.company_name || 'The workspace'} plan limits were updated.`,
      });
      setPlanEditTenant(null);
      await fetchTenants();
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: getErrorMessage(err, 'We could not update those plan limits.'),
      });
    } finally {
      setPlanSaveLoading('');
    }
  };

  const updateUserStatus = async (userId, newStatus, userName) => {
    setActionLoading(userId + newStatus);
    try {
      await api.put(`/admin/users/${userId}/status`, { status: newStatus });
      setUsers(prev => prev.map(u => u.id === userId ? { ...u, status: newStatus } : u));
      showToast({
        type: 'success',
        title: 'Status Updated',
        message: `${userName || 'The user'} is now ${newStatus}.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Action Failed',
        message: getErrorMessage(err, 'We could not update that user status.'),
      });
    } finally { setActionLoading(''); }
  };

  const reviewUser = async (userId) => {
    try {
      await api.post(`/admin/users/${userId}/review`, {});
    } catch {
      // Review is informational; keep the detail panel usable if logging fails.
    }
    setExpandedUser(prev => prev === userId ? null : userId);
  };

  const deleteUser = async (userId, userName) => {
    requestConfirmation({
      title: 'Delete User',
      description: `Delete ${userName || 'this user'} permanently. This action cannot be undone.`,
      confirmLabel: 'Delete user',
      onConfirm: async () => {
        setActionLoading(userId + 'delete');
        try {
          await api.delete(`/admin/users/${userId}`);
          setExpandedUser(prev => prev === userId ? null : prev);
          setExpandedTree(new Set());
          await fetchData();
          showToast({
            type: 'success',
            title: 'User Deleted',
            message: `${userName || 'The user'} was removed.`,
          });
        } catch (err) {
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that user.'),
          });
        } finally {
          setActionLoading('');
        }
      },
    });
  };

  /* Build admin → agent tree */
  const matchesFilter = (u) => {
    if (statusFilter !== 'all' && u.status !== statusFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (u.name || '').toLowerCase().includes(q) ||
             (u.email || '').toLowerCase().includes(q) ||
             (u.company_name || '').toLowerCase().includes(q);
    }
    return true;
  };

  const sortFn = (a, b) => {
    let va = a[sortBy] ?? '', vb = b[sortBy] ?? '';
    if (typeof va === 'number') return sortDir === 'asc' ? va - vb : vb - va;
    return sortDir === 'asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
  };

  const nonSuper = users.filter(u => u.role !== 'super_admin');
  const superAdmins = users.filter(u => u.role === 'super_admin').filter(matchesFilter).sort(sortFn);
  const allAgents = nonSuper.filter(u => u.role === 'company_agent');
  const agentMap = {};
  allAgents.forEach(a => { const k = a.company_id || ''; (agentMap[k] = agentMap[k] || []).push(a); });
  const admins = nonSuper.filter(u => u.role === 'admin').filter(matchesFilter).sort(sortFn);
  const tree = admins.map(adm => ({
    admin: adm,
    agents: (agentMap[adm.company_id || ''] || []).filter(matchesFilter).sort(sortFn),
  }));
  const adminCids = new Set(admins.map(a => a.company_id || ''));
  const orphanAgents = allAgents.filter(a => !adminCids.has(a.company_id || '')).filter(matchesFilter).sort(sortFn);
  const totalVisible = superAdmins.length + tree.reduce((s, t) => s + 1 + t.agents.length, 0) + orphanAgents.length;
  const toggleTree = (id) => setExpandedTree(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const statusCfg = {
    pending_approval: { color: '#d97706', bg: '#fffbeb', border: '#fde68a', label: 'Pending Approval', icon: Clock },
    active:   { color: '#16a34a', bg: '#ecfdf5', border: '#bbf7d0', label: 'Active',   icon: CheckCircle },
    rejected: { color: '#dc2626', bg: '#fef2f2', border: '#fecaca', label: 'Not Allowed', icon: XCircle },
    paused:   { color: '#d97706', bg: '#fffbeb', border: '#fde68a', label: 'Paused',   icon: Pause },
    blocked:  { color: '#dc2626', bg: '#fef2f2', border: '#fecaca', label: 'Blocked',  icon: Ban },
    inactive: { color: '#64748b', bg: '#f8fafc', border: '#e2e8f0', label: 'Inactive', icon: XCircle },
  };

  const getStatusCfg = (s) => statusCfg[s] || statusCfg.inactive;

  /* User lookup map (id or sub → user record) */
  const userMap = {};
  users.forEach(u => {
    if (u.id)  userMap[u.id]  = u;
    if (u.sub) userMap[u.sub] = u;
  });

  /* Role display helpers */
  const roleLabel = (role) =>
    role === 'admin'         ? 'Admin' :
    role === 'company_agent' ? 'Company Agent' :
    role === 'super_admin'   ? 'Super Admin' : (role || null);

  const roleBadgeCls = (role) =>
    role === 'admin'         ? 'bg-blue-50 text-blue-600 border border-blue-100' :
    role === 'company_agent' ? 'bg-emerald-50 text-emerald-700 border border-emerald-100' :
    role === 'super_admin'   ? 'bg-violet-50 text-violet-600 border border-violet-200' :
                               'bg-slate-100 text-slate-500 border border-slate-200';

  const getUserStats = (user) => [
    { icon: MessageSquare, label: 'Conversations', value: `${Number(user?.conversations_used ?? user?.conversations_count ?? 0).toLocaleString()} / ${Number(user?.conversation_limit ?? 0).toLocaleString() || '0'}`, color: '#7c3aed' },
    { icon: Users, label: 'Users', value: `${Number(user?.users_used ?? 0).toLocaleString()} / ${Number(user?.user_limit ?? 0).toLocaleString() || '0'}`, color: '#2563eb' },
    { icon: Target,        label: 'Leads',         value: user?.leads_count ?? 0,         color: '#0891b2' },
    { icon: UserCheck,     label: 'Customers',     value: user?.customers_count ?? 0,     color: '#059669' },
    { icon: Ticket,        label: 'Tickets',       value: user?.tickets_count ?? 0,       color: '#ea580c' },
    { icon: Package,       label: 'Products',      value: user?.products_count ?? 0,      color: '#6d28d9' },
  ];

  const pageTitle = 'Platform User Management';
  const pageDescription = 'Handle all platform users, account status changes, and deletion from one place';

  const statCards = overview ? [
    { label: 'Total Users',    value: overview.total_users,         icon: Users,          color: '#2563eb', bg: '#eff6ff' },
    { label: 'Total Agents',   value: overview.total_agents,        icon: Users,          color: '#0f766e', bg: '#ecfeff' },
    { label: 'Active',         value: overview.active_users,        icon: UserCheck,      color: '#16a34a', bg: '#ecfdf5' },
    { label: 'Pending',        value: overview.pending_approval_users || 0, icon: Clock,   color: '#d97706', bg: '#fffbeb' },
    { label: 'Not Allowed',    value: overview.rejected_users || 0,  icon: XCircle,       color: '#dc2626', bg: '#fef2f2' },
    { label: 'Paused',         value: overview.paused_users,        icon: Pause,          color: '#d97706', bg: '#fffbeb' },
    { label: 'Blocked',        value: overview.blocked_users,       icon: Ban,            color: '#dc2626', bg: '#fef2f2' },
    { label: 'Conversations',  value: overview.total_conversations, icon: MessageSquare,  color: '#7c3aed', bg: '#f5f3ff' },
    { label: 'Leads',          value: overview.total_leads,         icon: Target,         color: '#0891b2', bg: '#ecfeff' },
    { label: 'Customers',      value: overview.total_customers,     icon: UserPlus,       color: '#059669', bg: '#ecfdf5' },
    { label: 'Tickets',        value: overview.total_tickets,       icon: Ticket,         color: '#ea580c', bg: '#fff7ed' },
    { label: 'Products',       value: overview.total_products,      icon: Package,        color: '#6d28d9', bg: '#f5f3ff' },
  ] : [];

  if (loading) return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 rounded-full animate-spin" style={{ border: '3px solid #e2e8f0', borderTopColor: '#2563eb' }} />
        <span className="text-sm text-slate-400">Loading platform data…</span>
      </div>
    </div>
  );

  return (
    <div className="p-5 md:p-7 max-w-[1400px] mx-auto" data-testid="super-admin-dashboard">
      {/* ─── Header ─── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight">{pageTitle}</h1>
          <p className="text-xs text-slate-500 mt-0.5">{pageDescription}</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchData} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-slate-200 bg-white text-slate-600 text-sm font-medium hover:bg-slate-50 transition-colors">
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* ─── Feedback ─── */}
      {error && (
        <div className="mb-4 px-4 py-2.5 rounded-lg text-sm flex items-center gap-2 bg-red-50 border border-red-200 text-red-600">
          <XCircle size={15} /> {error}
        </div>
      )}

      {/* ─── Stat Cards ─── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6 gap-3 mb-6">
        {statCards.map((s, i) => (
          <div key={i} className="bg-white rounded-xl border border-slate-100 p-4 hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between mb-3">
              <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: s.bg }}>
                <s.icon size={17} color={s.color} />
              </div>
            </div>
            <p className="text-2xl font-extrabold text-slate-900 tracking-tight">{(s.value ?? 0).toLocaleString()}</p>
            <p className="text-xs text-slate-500 mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      {/* ─── Tab Navigation ─── */}
      <div className="flex items-center bg-white border border-slate-200 rounded-xl overflow-hidden mb-5 w-fit shadow-sm">
        {[
          { id: 'users',    label: 'Users',        icon: Users    },
          { id: 'tenants',  label: 'Tenants',      icon: Building2 },
          { id: 'visitors', label: 'Visitors',     icon: Globe },
          { id: 'logs',     label: 'System Logs',  icon: Activity },
          { id: 'security', label: 'Security',     icon: Shield   },
        ].map((tab, idx) => (
          <Fragment key={tab.id}>
            {idx > 0 && <div className="w-px h-9 bg-slate-100 flex-shrink-0" />}
            <button onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all ${
                activeTab === tab.id
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-500 hover:text-blue-600 hover:bg-blue-50'
              }`}>
              <tab.icon size={14} />
              {tab.label}
            </button>
          </Fragment>
        ))}
      </div>

      {/* ═══ TENANTS TAB ═══ */}
      {activeTab === 'tenants' && (
        <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-slate-50/50">
            <div>
              <p className="text-sm font-semibold text-slate-800">Workspaces & plan limits</p>
              <p className="text-xs text-slate-500">Monthly conversation totals across all channels; seat caps per subscription.</p>
            </div>
            <button
              type="button"
              onClick={() => fetchTenants()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              <RefreshCw size={13} /> Refresh
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50/80 text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                  <th className="px-3 py-3 border-b border-slate-100">Workspace</th>
                  <th className="px-3 py-3 border-b border-slate-100">Plan</th>
                  <th className="px-3 py-3 border-b border-slate-100">Users</th>
                  <th className="px-3 py-3 border-b border-slate-100">Conversation usage (mo)</th>
                  <th className="px-3 py-3 border-b border-slate-100">Stored limits</th>
                  <th className="px-3 py-3 border-b border-slate-100 w-28" />
                </tr>
              </thead>
              <tbody>
                {tenantsLoading ? (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">Loading tenants…</td></tr>
                ) : !(tenants || []).length ? (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">No tenants found</td></tr>
                ) : (
                  tenants.map((t) => {
                    const u = t.total_users ?? 0;
                    const cap = t.effective_max_users ?? 0;
                    const usage = Number(t.monthly_conversation_usage ?? 0);
                    const elimit = Number(t.effective_monthly_conversation_limit ?? 0);
                    return (
                      <tr key={t.company_id} className="border-b border-slate-100 hover:bg-slate-50/50">
                        <td className="px-3 py-2.5">
                          <div className="font-medium text-slate-900">{t.company_name || '—'}</div>
                        </td>
                        <td className="px-3 py-2.5 text-slate-700">
                          <span className="inline-flex px-2 py-0.5 rounded-md bg-slate-100 text-xs font-semibold">
                            {t.subscription_plan || 'free'}
                          </span>
                          <div className="text-[11px] text-slate-400 mt-0.5">{t.subscription_status || ''}</div>
                        </td>
                        <td className="px-3 py-2.5 text-slate-700">
                          {u}
                          <span className="text-slate-400"> / </span>
                          {cap || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-slate-700">
                          {usage.toLocaleString()}
                          <span className="text-slate-400"> / </span>
                          {elimit ? elimit.toLocaleString() : '—'}
                        </td>
                        <td className="px-3 py-2.5 text-xs text-slate-600">
                          conv cap DB: {t.monthly_conversation_limit ?? 0}, seats DB: {t.max_users_per_account ?? 0}
                          <div className="text-[11px] text-slate-400">0 = inherit from plan catalog</div>
                        </td>
                        <td className="px-3 py-2.5">
                          <button
                            type="button"
                            onClick={() => openPlanEdit(t)}
                            className="text-xs font-semibold text-blue-600 hover:text-blue-800"
                          >
                            Edit limits
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {planEditTenant && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog">
              <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-5 border border-slate-200">
                <h3 className="text-base font-bold text-slate-900 mb-1">Edit plan limits</h3>
                <p className="text-xs text-slate-500 mb-4">{planEditTenant.company_name || 'Selected workspace'}</p>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Monthly conversation limit</label>
                <input
                  type="number"
                  min={1}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mb-3"
                  value={planEditForm.monthly_conversation_limit}
                  onChange={(e) => setPlanEditForm((f) => ({ ...f, monthly_conversation_limit: e.target.value }))}
                  placeholder="e.g. 2500"
                />
                <label className="block text-xs font-semibold text-slate-600 mb-1">Max users (seats)</label>
                <input
                  type="number"
                  min={1}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mb-4"
                  value={planEditForm.max_users}
                  onChange={(e) => setPlanEditForm((f) => ({ ...f, max_users: e.target.value }))}
                  placeholder="e.g. 3"
                />
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    className="px-3 py-2 text-sm rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
                    onClick={() => setPlanEditTenant(null)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={!!planSaveLoading}
                    className="px-3 py-2 text-sm rounded-lg bg-blue-600 text-white font-semibold hover:bg-blue-700 disabled:opacity-50"
                    onClick={savePlanLimits}
                  >
                    {planSaveLoading ? 'Saving…' : 'Save'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ USERS TAB ═══ */}
      {activeTab === 'visitors' && (
        <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-slate-50/50">
            <div>
              <p className="text-sm font-semibold text-slate-800">Website visitors</p>
              <p className="text-xs text-slate-500">Anonymous public-page sessions; sensitive form values, tokens, and app-page activity are not collected.</p>
            </div>
            <button type="button" onClick={fetchVisitors} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-medium text-slate-600 hover:bg-slate-50">
              <RefreshCw size={13} /> Refresh
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50/80 text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                  <th className="px-3 py-3 border-b border-slate-100">Visitor</th>
                  <th className="px-3 py-3 border-b border-slate-100">First seen</th>
                  <th className="px-3 py-3 border-b border-slate-100">Last activity</th>
                  <th className="px-3 py-3 border-b border-slate-100">Page</th>
                  <th className="px-3 py-3 border-b border-slate-100">Device</th>
                  <th className="px-3 py-3 border-b border-slate-100">Visits</th>
                </tr>
              </thead>
              <tbody>
                {loadingVisitors ? (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">Loading visitors...</td></tr>
                ) : !(visitors || []).length ? (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">No visitor sessions found</td></tr>
                ) : visitors.map((v) => {
                  const meta = typeof v.metadata === 'object' && v.metadata ? v.metadata : {};
                  return (
                    <tr key={v.id} className="border-b border-slate-100 hover:bg-slate-50/50">
                      <td className="px-3 py-2.5 font-mono text-xs text-slate-600">{String(v.id || '').slice(0, 28)}</td>
                      <td className="px-3 py-2.5 text-slate-600">{v.first_seen_at ? new Date(v.first_seen_at).toLocaleString() : '-'}</td>
                      <td className="px-3 py-2.5 text-slate-600">{(v.last_activity || v.last_seen_at) ? new Date(v.last_activity || v.last_seen_at).toLocaleString() : '-'}</td>
                      <td className="px-3 py-2.5 text-slate-700 max-w-[280px] truncate" title={v.page_url || v.landing_path || ''}>{v.page_url || v.landing_path || '-'}</td>
                      <td className="px-3 py-2.5 text-slate-600">{[meta.browser, meta.os, meta.device].filter(Boolean).join(' / ') || 'Unknown'}</td>
                      <td className="px-3 py-2.5 text-slate-700">{Number(v.visit_count || 0).toLocaleString()}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === 'users' && (
        <>
          {/* ─── Filters ─── */}
          <div className="bg-white rounded-xl border border-slate-100 p-4 mb-4">
            <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center">
              {/* Search */}
              <div className="relative flex-1 min-w-[200px]">
                <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search} onChange={e => setSearch(e.target.value)}
                  placeholder="Search by name, email, company…"
                  className="w-full pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 bg-white" />
              </div>
              {/* Status filter */}
              <div className="flex items-center gap-1.5">
                <Filter size={13} className="text-slate-400" />
                <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                  className="text-sm border border-slate-200 rounded-lg px-3 py-2 outline-none bg-white text-slate-700 focus:border-blue-400">
                  <option value="all">All Status</option>
                  <option value="pending_approval">Pending Approval</option>
                  <option value="active">Active</option>
                  <option value="rejected">Not Allowed</option>
                  <option value="paused">Paused</option>
                  <option value="blocked">Blocked</option>
                  <option value="inactive">Inactive</option>
                </select>
              </div>
              <span className="text-xs text-slate-400 whitespace-nowrap">{totalVisible} user{totalVisible !== 1 ? 's' : ''}</span>
            </div>
          </div>

          {/* ─── Users Table ─── */}
          <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50/80">
                    {[
                      { key: null,                  label: '' },
                      { key: 'name',               label: 'User' },
                      { key: 'company_name',        label: 'Company' },
                      { key: 'status',              label: 'Status' },
                      { key: 'email_verified',      label: 'Verified' },
                      { key: 'conversations_count', label: 'Conversations' },
                      { key: 'leads_count',         label: 'Leads' },
                      { key: 'customers_count',     label: 'Customers' },
                      { key: 'products_count',      label: 'Products' },
                      { key: 'created_at',          label: 'Joined' },
                      { key: null,                  label: 'Actions' },
                    ].map(col => (
                      <th key={col.label || '_tree'}
                        onClick={() => col.key && (sortBy === col.key ? setSortDir(d => d === 'asc' ? 'desc' : 'asc') : (setSortBy(col.key), setSortDir('desc')))}
                        className={`px-3 py-3 text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider border-b border-slate-100 ${col.key ? 'cursor-pointer select-none hover:text-slate-600' : ''}`}>
                        <span className="inline-flex items-center gap-1">
                          {col.label}
                          {sortBy === col.key && (sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {totalVisible === 0 ? (
                    <tr><td colSpan={11} className="text-center py-12 text-slate-400">No users match your filters</td></tr>
                  ) : (
                    <>
                      {/* ── Super Admin rows ── */}
                      {superAdmins.map(sa => {
                        const sc = getStatusCfg(sa.status);
                        const isDetailOpen = expandedUser === sa.id;
                        return (
                          <Fragment key={sa.id}>
                            <tr className="border-b border-slate-100 hover:bg-violet-50/30 transition-colors bg-white">
                              <td className="pl-4 pr-1 py-3 w-8">
                                <Shield size={14} className="text-violet-500 mx-auto" />
                              </td>
                              <td className="px-3 py-3">
                                <div className="flex items-center gap-3">
                                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-violet-100 to-purple-200 flex items-center justify-center flex-shrink-0">
                                    <span className="text-sm font-bold text-violet-700">{(sa.name || '?')[0].toUpperCase()}</span>
                                  </div>
                                  <div className="min-w-0">
                                    <div className="flex items-center gap-2">
                                      <p className="font-semibold text-slate-900 truncate">{sa.name || '—'}</p>
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-50 text-violet-600 border border-violet-200">Super Admin</span>
                                    </div>
                                    <p className="text-xs text-slate-400 truncate">{sa.email}</p>
                                  </div>
                                </div>
                              </td>
                              <td className="px-3 py-3 text-xs text-slate-400">Platform</td>
                              <td className="px-3 py-3">
                                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold"
                                  style={{ background: sc.bg, color: sc.color, border: `1px solid ${sc.border}` }}>
                                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: sc.color }} />
                                  {sc.label}
                                </span>
                              </td>
                              <td className="px-3 py-3 text-center text-xs">
                                <span className={`font-semibold ${sa.email_verified ? 'text-emerald-600' : 'text-amber-600'}`}>
                                  {sa.email_verified ? 'Yes' : 'No'}
                                </span>
                              </td>
                              <td className="px-3 py-3 text-center text-xs text-slate-400">—</td>
                              <td className="px-3 py-3 text-center text-xs text-slate-400">—</td>
                              <td className="px-3 py-3 text-center text-xs text-slate-400">—</td>
                              <td className="px-3 py-3 text-center text-xs text-slate-400">—</td>
                              <td className="px-3 py-3 text-xs text-slate-500">
                                {sa.created_at ? new Date(sa.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                              </td>
                              <td className="px-3 py-3">
                                <button onClick={() => reviewUser(sa.id)}
                                  className="p-1.5 rounded-md hover:bg-slate-100 text-slate-500 transition-colors" title="Review / Manage access">
                                  <Eye size={15} />
                                </button>
                              </td>
                            </tr>
                            {isDetailOpen && (
                              <tr className="bg-violet-50/30">
                                <td colSpan={11} className="px-6 py-4">
                                  <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-500">
                                    <span className="inline-flex items-center gap-1"><Mail size={12} /> {sa.email}</span>
                                    {sa.phone && <span className="inline-flex items-center gap-1"><Phone size={12} /> {sa.phone}</span>}
                                    <span className="inline-flex items-center gap-1"><Calendar size={12} /> Joined {sa.created_at ? new Date(sa.created_at).toLocaleDateString() : '—'}</span>
                                    {sa.last_login && <span className="inline-flex items-center gap-1"><Clock size={12} /> Last login {new Date(sa.last_login).toLocaleString()}</span>}
                                    <span className="inline-flex items-center gap-1"><Activity size={12} /> Auth: {sa.auth_provider || 'email'}</span>
                                  </div>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                      {/* ── Admin → Agent tree ── */}
                      {tree.map(({ admin, agents: agentList }) => {
                        const sc = getStatusCfg(admin.status);
                        const isTreeOpen = expandedTree.has(admin.id);
                        const isDetailOpen = expandedUser === admin.id;
                        const agentCount = agentList.length;
                        return (
                          <Fragment key={admin.id}>
                            {/* ── Admin row ── */}
                            <tr
                              data-treeid={admin.id}
                              className="border-b border-slate-100 hover:bg-blue-50/30 transition-colors bg-white"
                            >
                              <td className="pl-4 pr-1 py-3 w-8">
                                <button onClick={() => agentCount > 0 && toggleTree(admin.id)}
                                  className={`p-1 rounded transition-colors ${agentCount > 0 ? 'hover:bg-slate-100 text-slate-400' : 'text-slate-200 cursor-default'}`}>
                                  {isTreeOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                </button>
                              </td>
                              <td className="px-3 py-3">
                                <div className="flex items-center gap-3">
                                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-100 to-indigo-100 flex items-center justify-center flex-shrink-0">
                                    <span className="text-sm font-bold text-blue-700">{(admin.name || '?')[0].toUpperCase()}</span>
                                  </div>
                                  <div className="min-w-0">
                                    <div className="flex items-center gap-2">
                                      <p className="font-semibold text-slate-900 truncate">{admin.name || '—'}</p>
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-50 text-blue-600 border border-blue-100">Admin</span>
                                      {agentCount > 0 && (
                                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-500">
                                          {agentCount} agent{agentCount !== 1 ? 's' : ''}
                                        </span>
                                      )}
                                    </div>
                                    <p className="text-xs text-slate-400 truncate">{admin.email}</p>
                                    <p className="text-[11px] text-slate-400 truncate">
                                      {(admin.subscription_plan || 'free')} | users {admin.users_used ?? 0}/{admin.user_limit ?? '-'} | conversations {admin.conversations_used ?? admin.conversations_count ?? 0}/{admin.conversation_limit ?? '-'}
                                    </p>
                                  </div>
                                </div>
                              </td>
                              <td className="px-3 py-3">
                                <div className="flex items-center gap-2">
                                  <Building2 size={13} className="text-slate-400 flex-shrink-0" />
                                  <span className="text-slate-600 truncate max-w-[140px]">{admin.company_name || '—'}</span>
                                </div>
                              </td>
                              <td className="px-3 py-3">
                                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold"
                                  style={{ background: sc.bg, color: sc.color, border: `1px solid ${sc.border}` }}>
                                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: sc.color }} />
                                  {sc.label}
                                </span>
                              </td>
                              <td className="px-3 py-3 text-center text-xs">
                                <span className={`font-semibold ${admin.email_verified ? 'text-emerald-600' : 'text-amber-600'}`}>
                                  {admin.email_verified ? 'Yes' : 'No'}
                                </span>
                              </td>
                              <td className="px-3 py-3 text-center"><span className="font-semibold text-slate-700">{admin.conversations_count ?? 0}</span></td>
                              <td className="px-3 py-3 text-center"><span className="font-semibold text-slate-700">{admin.leads_count ?? 0}</span></td>
                              <td className="px-3 py-3 text-center"><span className="font-semibold text-slate-700">{admin.customers_count ?? 0}</span></td>
                              <td className="px-3 py-3 text-center"><span className="font-semibold text-slate-700">{admin.products_count ?? 0}</span></td>
                              <td className="px-3 py-3 text-xs text-slate-500">
                                {admin.created_at ? new Date(admin.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                              </td>
                              <td className="px-3 py-3">
                                <div className="flex items-center gap-1.5">
                                  <button onClick={() => reviewUser(admin.id)}
                                    className="p-1.5 rounded-md hover:bg-slate-100 text-slate-500 transition-colors" title="Review / Manage access">
                                    <Eye size={15} />
                                  </button>
                                  {admin.status !== 'active' && (
                                    <button onClick={() => updateUserStatus(admin.id, 'active', admin.name)} disabled={!!actionLoading}
                                      className="p-1.5 rounded-md hover:bg-emerald-50 text-emerald-600 transition-colors disabled:opacity-40" title={['paused', 'blocked', 'inactive'].includes(admin.status) ? 'Resume' : 'Approve / Allow'}>
                                      {actionLoading === admin.id + 'active' ? <Loader size={14} className="animate-spin" /> : <CheckCircle size={15} />}
                                    </button>
                                  )}
                                  {admin.status !== 'rejected' && (
                                    <button onClick={() => updateUserStatus(admin.id, 'rejected', admin.name)} disabled={!!actionLoading}
                                      className="p-1.5 rounded-md hover:bg-red-50 text-red-600 transition-colors disabled:opacity-40" title="Not Allowed / Reject">
                                      {actionLoading === admin.id + 'rejected' ? <Loader size={14} className="animate-spin" /> : <XCircle size={15} />}
                                    </button>
                                  )}
                                  {admin.status !== 'paused' && admin.status !== 'blocked' && (
                                    <button onClick={() => updateUserStatus(admin.id, 'paused', admin.name)} disabled={!!actionLoading}
                                      className="p-1.5 rounded-md hover:bg-amber-50 text-amber-600 transition-colors disabled:opacity-40" title="Pause">
                                      {actionLoading === admin.id + 'paused' ? <Loader size={14} className="animate-spin" /> : <Pause size={15} />}
                                    </button>
                                  )}
                                  {admin.status !== 'blocked' && (
                                    <button onClick={() => updateUserStatus(admin.id, 'blocked', admin.name)} disabled={!!actionLoading}
                                      className="p-1.5 rounded-md hover:bg-red-50 text-red-500 transition-colors disabled:opacity-40" title="Block">
                                      {actionLoading === admin.id + 'blocked' ? <Loader size={14} className="animate-spin" /> : <Ban size={15} />}
                                    </button>
                                  )}
                                  <button onClick={() => deleteUser(admin.id, admin.name)} disabled={!!actionLoading}
                                    className="p-1.5 rounded-md hover:bg-red-50 text-red-700 transition-colors disabled:opacity-40" title="Delete user">
                                    {actionLoading === admin.id + 'delete' ? <Loader size={14} className="animate-spin" /> : <Trash2 size={15} />}
                                  </button>
                                </div>
                              </td>
                            </tr>
                            {/* Admin detail expand */}
                            {isDetailOpen && (
                              <tr className="bg-slate-50/60">
                                <td colSpan={11} className="px-6 py-4">
                                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-4">
                                    {getUserStats(admin).map((s, i) => (
                                      <div key={i} className="bg-white rounded-lg border border-slate-100 p-3 flex items-center gap-3">
                                        <s.icon size={16} color={s.color} />
                                        <div>
                                          <p className="text-lg font-bold text-slate-800">{s.value}</p>
                                          <p className="text-[11px] text-slate-400">{s.label}</p>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                  <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-500">
                                    <span className="inline-flex items-center gap-1"><Mail size={12} /> {admin.email}</span>
                                    {admin.phone && <span className="inline-flex items-center gap-1"><Phone size={12} /> {admin.phone}</span>}
                                    <span className="inline-flex items-center gap-1"><Building2 size={12} /> {admin.company_name || 'No company'}</span>
                                    <span className="inline-flex items-center gap-1"><Calendar size={12} /> Joined {admin.created_at ? new Date(admin.created_at).toLocaleDateString() : '—'}</span>
                                    {admin.last_login && <span className="inline-flex items-center gap-1"><Clock size={12} /> Last login {new Date(admin.last_login).toLocaleString()}</span>}
                                    <span className="inline-flex items-center gap-1"><Activity size={12} /> Auth: {admin.auth_provider || 'email'}</span>
                                  </div>
                                  <div className="mt-3 flex flex-wrap gap-2">
                                    {['active', 'rejected', 'paused', 'blocked', 'inactive'].filter(s => s !== admin.status).map(st => {
                                      const cfg = statusCfg[st];
                                      const Icon = cfg.icon;
                                      return (
                                        <button key={st} onClick={() => updateUserStatus(admin.id, st, admin.name)} disabled={!!actionLoading}
                                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-40"
                                          style={{ background: cfg.bg, color: cfg.color, borderColor: cfg.border }}>
                                          {actionLoading === admin.id + st ? <Loader size={12} className="animate-spin" /> : <Icon size={12} />}
                                          Set {cfg.label}
                                        </button>
                                      );
                                    })}
                                    <button onClick={() => deleteUser(admin.id, admin.name)} disabled={!!actionLoading}
                                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-red-200 bg-red-50 text-red-700 transition-colors disabled:opacity-40">
                                      {actionLoading === admin.id + 'delete' ? <Loader size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                      Delete User
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            )}
                            {/* ── Agent sub-rows ── */}
                                  {isTreeOpen && agentList.map((agent, idx) => {
                              const asc = getStatusCfg(agent.status);
                              const isLast = idx === agentList.length - 1;
                              const isAgentDetail = expandedUser === agent.id;
                              return (
                                <Fragment key={agent.id}>
                                  <tr
                                    data-treeid={admin.id}
                                    className="border-b border-slate-50 bg-slate-50/40 hover:bg-slate-100/60 transition-colors"
                                  >
                                    <td className="pl-4 pr-1 py-2 w-8">
                                      <div className="flex items-center justify-center text-slate-300 text-sm font-mono select-none">{isLast ? '└' : '├'}</div>
                                    </td>
                                    <td className="px-3 py-2">
                                      <div className="flex items-center gap-2.5 pl-2">
                                        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center flex-shrink-0">
                                          <span className="text-xs font-bold text-slate-600">{(agent.name || '?')[0].toUpperCase()}</span>
                                        </div>
                                        <div className="min-w-0">
                                          <div className="flex items-center gap-2">
                                            <p className="font-medium text-slate-700 truncate text-[13px]">{agent.name || '—'}</p>
                                            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${roleBadgeCls('company_agent')}`}>{roleLabel('company_agent')}</span>
                                          </div>
                                          <p className="text-[11px] text-slate-400 truncate">{agent.email}</p>
                                          <p className="text-[10px] text-slate-400 truncate">
                                            {(agent.subscription_plan || 'free')} | users {agent.users_used ?? 0}/{agent.user_limit ?? '-'} | conversations {agent.conversations_used ?? agent.conversations_count ?? 0}/{agent.conversation_limit ?? '-'}
                                          </p>
                                        </div>
                                      </div>
                                    </td>
                                    <td className="px-3 py-2">
                                      <div className="flex items-center gap-2">
                                        <Building2 size={13} className="text-slate-400 flex-shrink-0" />
                                        <span className="text-slate-600 truncate max-w-[140px]">{agent.company_name || '—'}</span>
                                      </div>
                                    </td>
                                    <td className="px-3 py-2">
                                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                                        style={{ background: asc.bg, color: asc.color, border: `1px solid ${asc.border}` }}>
                                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: asc.color }} />
                                        {asc.label}
                                      </span>
                                    </td>
                                    <td className="px-3 py-2 text-center text-xs">
                                      <span className={`font-semibold ${agent.email_verified ? 'text-emerald-600' : 'text-amber-600'}`}>
                                        {agent.email_verified ? 'Yes' : 'No'}
                                      </span>
                                    </td>
                                    <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.conversations_count ?? 0}</span></td>
                                    <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.leads_count ?? 0}</span></td>
                                    <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.customers_count ?? 0}</span></td>
                                    <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.products_count ?? 0}</span></td>
                                    <td className="px-3 py-2 text-xs text-slate-400">
                                      {agent.created_at ? new Date(agent.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                                    </td>
                                    <td className="px-3 py-2">
                                      <div className="flex items-center gap-1.5">
                                        <button onClick={() => reviewUser(agent.id)}
                                          className="p-1 rounded-md hover:bg-slate-100 text-slate-400 transition-colors" title="Review / Manage access">
                                          <Eye size={13} />
                                        </button>
                                        {agent.status !== 'active' && (
                                          <button onClick={() => updateUserStatus(agent.id, 'active', agent.name)} disabled={!!actionLoading}
                                            className="p-1 rounded-md hover:bg-emerald-50 text-emerald-600 transition-colors disabled:opacity-40" title={['paused', 'blocked', 'inactive'].includes(agent.status) ? 'Resume' : 'Approve / Allow'}>
                                            {actionLoading === agent.id + 'active' ? <Loader size={12} className="animate-spin" /> : <CheckCircle size={13} />}
                                          </button>
                                        )}
                                        {agent.status !== 'rejected' && (
                                          <button onClick={() => updateUserStatus(agent.id, 'rejected', agent.name)} disabled={!!actionLoading}
                                            className="p-1 rounded-md hover:bg-red-50 text-red-600 transition-colors disabled:opacity-40" title="Not Allowed / Reject">
                                            {actionLoading === agent.id + 'rejected' ? <Loader size={12} className="animate-spin" /> : <XCircle size={13} />}
                                          </button>
                                        )}
                                        {agent.status !== 'paused' && agent.status !== 'blocked' && (
                                          <button onClick={() => updateUserStatus(agent.id, 'paused', agent.name)} disabled={!!actionLoading}
                                            className="p-1 rounded-md hover:bg-amber-50 text-amber-600 transition-colors disabled:opacity-40" title="Pause">
                                            {actionLoading === agent.id + 'paused' ? <Loader size={12} className="animate-spin" /> : <Pause size={13} />}
                                          </button>
                                        )}
                                        {agent.status !== 'blocked' && (
                                          <button onClick={() => updateUserStatus(agent.id, 'blocked', agent.name)} disabled={!!actionLoading}
                                            className="p-1 rounded-md hover:bg-red-50 text-red-500 transition-colors disabled:opacity-40" title="Block">
                                            {actionLoading === agent.id + 'blocked' ? <Loader size={12} className="animate-spin" /> : <Ban size={13} />}
                                          </button>
                                        )}
                                        <button onClick={() => deleteUser(agent.id, agent.name)} disabled={!!actionLoading}
                                          className="p-1 rounded-md hover:bg-red-50 text-red-700 transition-colors disabled:opacity-40" title="Delete user">
                                          {actionLoading === agent.id + 'delete' ? <Loader size={12} className="animate-spin" /> : <Trash2 size={13} />}
                                        </button>
                                      </div>
                                    </td>
                                  </tr>
                                  {isAgentDetail && (
                                    <tr data-treeid={admin.id} className="bg-blue-50/20 border-b border-slate-100">
                                      <td colSpan={11} className="px-6 py-4">
                                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-4">
                                          {getUserStats(agent).map((s, i) => (
                                            <div key={i} className="bg-white rounded-lg border border-slate-100 p-3 flex items-center gap-3">
                                              <s.icon size={16} color={s.color} />
                                              <div>
                                                <p className="text-lg font-bold text-slate-800">{s.value}</p>
                                                <p className="text-[11px] text-slate-400">{s.label}</p>
                                              </div>
                                            </div>
                                          ))}
                                        </div>
                                        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-500">
                                          <span className="inline-flex items-center gap-1"><Mail size={12} /> {agent.email}</span>
                                          {agent.phone && <span className="inline-flex items-center gap-1"><Phone size={12} /> {agent.phone}</span>}
                                          <span className="inline-flex items-center gap-1"><Building2 size={12} /> {agent.company_name || 'No company'}</span>
                                          <span className="inline-flex items-center gap-1"><Calendar size={12} /> Joined {agent.created_at ? new Date(agent.created_at).toLocaleDateString() : '—'}</span>
                                          {agent.last_login && <span className="inline-flex items-center gap-1"><Clock size={12} /> Last login {new Date(agent.last_login).toLocaleString()}</span>}
                                          <span className="inline-flex items-center gap-1"><Activity size={12} /> Auth: {agent.auth_provider || 'email'}</span>
                                          <span className="inline-flex items-center gap-1">Role: {agent.role === 'company_agent' ? 'Company Agent' : (agent.role || 'Company Agent')}</span>
                                        </div>
                                        <div className="mt-3 flex flex-wrap gap-2">
                                          {['active', 'rejected', 'paused', 'blocked', 'inactive'].filter(s => s !== agent.status).map(st => {
                                            const cfg = statusCfg[st]; const Icon = cfg.icon;
                                            return (
                                              <button key={st} onClick={() => updateUserStatus(agent.id, st, agent.name)} disabled={!!actionLoading}
                                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-40"
                                                style={{ background: cfg.bg, color: cfg.color, borderColor: cfg.border }}>
                                                {actionLoading === agent.id + st ? <Loader size={12} className="animate-spin" /> : <Icon size={12} />}
                                                Set {cfg.label}
                                              </button>
                                            );
                                          })}
                                          <button onClick={() => deleteUser(agent.id, agent.name)} disabled={!!actionLoading}
                                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-red-200 bg-red-50 text-red-700 transition-colors disabled:opacity-40">
                                            {actionLoading === agent.id + 'delete' ? <Loader size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                            Delete User
                                          </button>
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </Fragment>
                              );
                            })}
                          </Fragment>
                        );
                      })}
                      {/* Orphan agents (no matching admin) */}
                      {orphanAgents.map((agent, idx) => {
                        const asc = getStatusCfg(agent.status);
                        const isAgentDetail = expandedUser === agent.id;
                        return (
                          <Fragment key={agent.id}>
                            <tr
                              className="border-b border-slate-50 bg-amber-50/20 hover:bg-amber-50/40 transition-colors"
                            >
                              <td className="pl-4 pr-1 py-2 w-8" />
                              <td className="px-3 py-2">
                                <div className="flex items-center gap-2.5">
                                  <div className="w-7 h-7 rounded-full bg-gradient-to-br from-amber-100 to-amber-200 flex items-center justify-center flex-shrink-0">
                                    <span className="text-xs font-bold text-amber-700">{(agent.name || '?')[0].toUpperCase()}</span>
                                  </div>
                                  <div className="min-w-0">
                                    <div className="flex items-center gap-2">
                                      <p className="font-medium text-slate-700 truncate text-[13px]">{agent.name || '—'}</p>
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-50 text-amber-600 border border-amber-200">Unassigned</span>
                                    </div>
                                    <p className="text-[11px] text-slate-400 truncate">{agent.email}</p>
                                    <p className="text-[10px] text-slate-400 truncate">
                                      {(agent.subscription_plan || 'free')} | users {agent.users_used ?? 0}/{agent.user_limit ?? '-'} | conversations {agent.conversations_used ?? agent.conversations_count ?? 0}/{agent.conversation_limit ?? '-'}
                                    </p>
                                  </div>
                                </div>
                              </td>
                              <td className="px-3 py-2">
                                <div className="flex items-center gap-2">
                                  <Building2 size={13} className="text-slate-400 flex-shrink-0" />
                                  <span className="text-slate-600 truncate max-w-[140px]">{agent.company_name || '—'}</span>
                                </div>
                              </td>
                              <td className="px-3 py-2">
                                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                                  style={{ background: asc.bg, color: asc.color, border: `1px solid ${asc.border}` }}>
                                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: asc.color }} /> {asc.label}
                                </span>
                              </td>
                              <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.conversations_count ?? 0}</span></td>
                              <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.leads_count ?? 0}</span></td>
                              <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.customers_count ?? 0}</span></td>
                              <td className="px-3 py-2 text-center"><span className="font-semibold text-slate-700">{agent.products_count ?? 0}</span></td>
                              <td className="px-3 py-2 text-xs text-slate-400">
                                {agent.created_at ? new Date(agent.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                              </td>
                              <td className="px-3 py-2">
                                <div className="flex items-center gap-1.5">
                                  <button onClick={() => reviewUser(agent.id)}
                                    className="p-1 rounded-md hover:bg-slate-100 text-slate-400 transition-colors" title="Review / Manage access">
                                    <Eye size={13} />
                                  </button>
                                  {agent.status !== 'active' && (
                                    <button onClick={() => updateUserStatus(agent.id, 'active', agent.name)} disabled={!!actionLoading}
                                      className="p-1 rounded-md hover:bg-emerald-50 text-emerald-600 disabled:opacity-40" title={['paused', 'blocked', 'inactive'].includes(agent.status) ? 'Resume' : 'Approve / Allow'}><CheckCircle size={13} /></button>
                                  )}
                                  {agent.status !== 'rejected' && (
                                    <button onClick={() => updateUserStatus(agent.id, 'rejected', agent.name)} disabled={!!actionLoading}
                                      className="p-1 rounded-md hover:bg-red-50 text-red-600 disabled:opacity-40" title="Not Allowed / Reject"><XCircle size={13} /></button>
                                  )}
                                  {agent.status !== 'paused' && agent.status !== 'blocked' && (
                                    <button onClick={() => updateUserStatus(agent.id, 'paused', agent.name)} disabled={!!actionLoading}
                                      className="p-1 rounded-md hover:bg-amber-50 text-amber-600 disabled:opacity-40"><Pause size={13} /></button>
                                  )}
                                  {agent.status !== 'blocked' && (
                                    <button onClick={() => updateUserStatus(agent.id, 'blocked', agent.name)} disabled={!!actionLoading}
                                      className="p-1 rounded-md hover:bg-red-50 text-red-500 disabled:opacity-40"><Ban size={13} /></button>
                                  )}
                                  <button onClick={() => deleteUser(agent.id, agent.name)} disabled={!!actionLoading}
                                    className="p-1 rounded-md hover:bg-red-50 text-red-700 disabled:opacity-40" title="Delete user">
                                    {actionLoading === agent.id + 'delete' ? <Loader size={12} className="animate-spin" /> : <Trash2 size={13} />}
                                  </button>
                                </div>
                              </td>
                            </tr>
                            {isAgentDetail && (
                              <tr className="bg-amber-50/30">
                                <td colSpan={11} className="px-6 py-4">
                                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-4">
                                    {getUserStats(agent).map((s, i) => (
                                      <div key={i} className="bg-white rounded-lg border border-slate-100 p-3 flex items-center gap-3">
                                        <s.icon size={16} color={s.color} />
                                        <div>
                                          <p className="text-lg font-bold text-slate-800">{s.value}</p>
                                          <p className="text-[11px] text-slate-400">{s.label}</p>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                  <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-500">
                                    <span className="inline-flex items-center gap-1"><Mail size={12} /> {agent.email}</span>
                                    {agent.phone && <span className="inline-flex items-center gap-1"><Phone size={12} /> {agent.phone}</span>}
                                    <span className="inline-flex items-center gap-1"><Building2 size={12} /> {agent.company_name || 'No company'}</span>
                                    <span className="inline-flex items-center gap-1"><Calendar size={12} /> Joined {agent.created_at ? new Date(agent.created_at).toLocaleDateString() : '—'}</span>
                                    {agent.last_login && <span className="inline-flex items-center gap-1"><Clock size={12} /> Last login {new Date(agent.last_login).toLocaleString()}</span>}
                                    <span className="inline-flex items-center gap-1"><Activity size={12} /> Auth: {agent.auth_provider || 'email'}</span>
                                    <span className="inline-flex items-center gap-1">Role: {agent.role === 'company_agent' ? 'Company Agent' : (agent.role || 'Company Agent')}</span>
                                  </div>
                                  <div className="mt-3 flex flex-wrap gap-2">
                                    {['active', 'rejected', 'paused', 'blocked', 'inactive'].filter(s => s !== agent.status).map(st => {
                                      const cfg = statusCfg[st];
                                      const Icon = cfg.icon;
                                      return (
                                        <button key={st} onClick={() => updateUserStatus(agent.id, st, agent.name)} disabled={!!actionLoading}
                                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-40"
                                          style={{ background: cfg.bg, color: cfg.color, borderColor: cfg.border }}>
                                          {actionLoading === agent.id + st ? <Loader size={12} className="animate-spin" /> : <Icon size={12} />}
                                          Set {cfg.label}
                                        </button>
                                      );
                                    })}
                                    <button onClick={() => deleteUser(agent.id, agent.name)} disabled={!!actionLoading}
                                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-red-200 bg-red-50 text-red-700 transition-colors disabled:opacity-40">
                                      {actionLoading === agent.id + 'delete' ? <Loader size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                      Delete User
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* ─── Platform Status Footer ─── */}
          <div className="mt-6 px-5 py-3 bg-slate-50 rounded-xl border border-slate-100 flex items-center gap-6 flex-wrap text-xs text-slate-500">
            <div className="flex items-center gap-1.5"><Globe size={13} className="text-slate-400" /> <span className="font-medium text-slate-600">Platform Status</span></div>
            {['API: Healthy', 'Database: Connected', 'AI Service: Active'].map(s => (
              <div key={s} className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                {s}
              </div>
            ))}
          </div>
        </>
      )}

      {/* ═══ SYSTEM LOGS TAB ═══ */}
      {activeTab === 'logs' && (
        <div className="space-y-4">
          {/* Header */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="flex-1">
              <h2 className="text-base font-semibold text-slate-900">System Logs</h2>
              <p className="text-xs text-slate-500 mt-0.5">All platform activity across every account</p>
            </div>
            <button onClick={fetchLogs} disabled={loadingLogs}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-600 text-sm hover:bg-slate-50 disabled:opacity-50">
              {loadingLogs ? <Loader size={14} className="animate-spin" /> : <RefreshCw size={14} />} Refresh
            </button>
          </div>

          {/* Filters */}
          <div className="bg-white rounded-xl border border-slate-100 p-3 flex flex-col sm:flex-row gap-3 items-start sm:items-center">
            <div className="relative flex-1 min-w-[200px]">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={logSearch} onChange={e => setLogSearch(e.target.value)}
                placeholder="Search action, user, company, details…"
                className="w-full pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100" />
            </div>
            <select value={logLevelFilter} onChange={e => setLogLevelFilter(e.target.value)}
              className="text-sm border border-slate-200 rounded-lg px-3 py-2 outline-none bg-white text-slate-700 focus:border-blue-400">
              <option value="all">All Levels</option>
              <option value="info">Info</option>
              <option value="warn">Warning</option>
              <option value="error">Error</option>
            </select>
            <select value={logCompanyFilter} onChange={e => setLogCompanyFilter(e.target.value)}
              className="text-sm border border-slate-200 rounded-lg px-3 py-2 outline-none bg-white text-slate-700 focus:border-blue-400 max-w-[200px]">
              <option value="all">All Companies</option>
              {[...new Set(adminLogs.map(l => l.company_name || l.company_id).filter(Boolean))].sort().map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* Logs table */}
          {loadingLogs ? (
            <div className="flex items-center justify-center py-12 bg-white rounded-xl border border-slate-100">
              <Loader size={20} className="animate-spin text-slate-400" />
            </div>
          ) : (() => {
            const filteredLogs = adminLogs.filter(l => {
              if (logLevelFilter !== 'all' && (l.level || 'info') !== logLevelFilter) return false;
              if (logCompanyFilter !== 'all' && (l.company_name || l.company_id) !== logCompanyFilter) return false;
              if (logSearch) {
                const q = logSearch.toLowerCase();
                return (l.action || '').toLowerCase().includes(q) ||
                       (l.user_name || l.user_id || '').toLowerCase().includes(q) ||
                       (l.company_name || l.company_id || '').toLowerCase().includes(q) ||
                       (typeof l.details === 'string' ? l.details : JSON.stringify(l.details || '')).toLowerCase().includes(q);
              }
              return true;
            });
            return filteredLogs.length === 0 ? (
              <div className="text-center py-12 text-slate-400 text-sm bg-white rounded-xl border border-slate-100">No logs match your filters.</div>
            ) : (
              <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
                <div className="px-4 py-2.5 border-b border-slate-100">
                  <span className="text-xs text-slate-400">{filteredLogs.length} log{filteredLogs.length !== 1 ? 's' : ''}</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-slate-50/80 border-b border-slate-100">
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Time</th>
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Company</th>
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">User</th>
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Level</th>
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Action</th>
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Entity</th>
                        <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredLogs.map((l, i) => (
                        <tr key={l.id || i} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                          <td className="px-4 py-2.5 text-xs text-slate-400 whitespace-nowrap">{l.created_at ? new Date(l.created_at).toLocaleString() : '—'}</td>
                          <td className="px-4 py-2.5 text-xs text-slate-600 max-w-[120px] truncate">{l.company_name || 'Unknown company'}</td>
                          <td className="px-4 py-2.5 text-xs max-w-[140px]">
                            <p className="text-slate-700 font-medium truncate">{l.user_name || 'Unknown user'}</p>
                            {(l.company_name || (userMap[l.user_id] || {}).company_name) && <p className="text-slate-400 text-[11px] truncate">{l.company_name || (userMap[l.user_id] || {}).company_name}</p>}
                          </td>
                          <td className="px-4 py-2.5">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                              l.level === 'error' ? 'bg-red-50 text-red-600' :
                              l.level === 'warn'  ? 'bg-amber-50 text-amber-600' :
                              'bg-slate-100 text-slate-500'
                            }`}>{l.level || 'info'}</span>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-slate-700 font-medium max-w-[160px] truncate">{l.action || '—'}</td>
                          <td className="px-4 py-2.5 text-xs max-w-[160px]">
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[10px] text-slate-400 font-mono">{l.entity_type || '—'}</span>
                              {(l.user_name || (userMap[l.user_id] || {}).name) && <span className="text-slate-700 font-medium truncate">{l.user_name || (userMap[l.user_id] || {}).name}</span>}
                              {(l.company_name || (userMap[l.user_id] || {}).company_name) && <span className="text-slate-400 text-[11px] truncate">{l.company_name || (userMap[l.user_id] || {}).company_name}</span>}
                              {(() => { const role = (userMap[l.user_id] || {}).role || l.user_role; if (!role) return null; return <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium w-fit ${roleBadgeCls(role)}`}>{roleLabel(role)}</span>; })()}
                            </div>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[220px] truncate">{typeof l.details === 'object' ? JSON.stringify(l.details) : (l.details || '—')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* ═══ SECURITY TAB ═══ */}
      {activeTab === 'security' && (
        <div className="space-y-5">
          {/* Header */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="flex-1">
              <h2 className="text-base font-semibold text-slate-900">Security Center</h2>
              <p className="text-xs text-slate-500 mt-0.5">Platform-wide security overview and access monitoring</p>
            </div>
            <button onClick={fetchSecurity} disabled={loadingSecurity}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-600 text-sm hover:bg-slate-50 disabled:opacity-50">
              {loadingSecurity ? <Loader size={14} className="animate-spin" /> : <RefreshCw size={14} />} Refresh
            </button>
          </div>

          {/* Security metric cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Total Sessions',  value: loginSessions.length,                                      icon: Clock,        color: '#2563eb', bg: '#eff6ff' },
              { label: 'Failed Logins',   value: authLogs.filter(l => l.event_type === 'login_failed').length, icon: XCircle,   color: '#dc2626', bg: '#fef2f2' },
              { label: 'Blocked Users',   value: users.filter(u => u.status === 'blocked').length,           icon: Ban,          color: '#d97706', bg: '#fffbeb' },
              { label: 'Active Users',    value: users.filter(u => u.status === 'active').length,            icon: CheckCircle,  color: '#059669', bg: '#ecfdf5' },
            ].map((c, i) => (
              <div key={i} className="bg-white rounded-xl border border-slate-100 p-4 hover:shadow-md transition-shadow">
                <div className="w-9 h-9 rounded-lg flex items-center justify-center mb-3" style={{ background: c.bg }}>
                  <c.icon size={17} color={c.color} />
                </div>
                <p className="text-2xl font-extrabold text-slate-900">{c.value}</p>
                <p className="text-xs text-slate-500 mt-0.5">{c.label}</p>
              </div>
            ))}
          </div>

          {loadingSecurity ? (
            <div className="flex items-center justify-center py-12 bg-white rounded-xl border border-slate-100">
              <Loader size={20} className="animate-spin text-slate-400" />
            </div>
          ) : (
            <>
              {/* Blocked users quick-action */}
              {users.filter(u => u.status === 'blocked').length > 0 && (
                <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
                  <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
                    <Ban size={15} className="text-red-500" />
                    <span className="text-sm font-semibold text-slate-800">Blocked Accounts</span>
                    <span className="ml-auto text-xs text-slate-400">{users.filter(u => u.status === 'blocked').length} blocked</span>
                  </div>
                  <div className="divide-y divide-slate-50">
                    {users.filter(u => u.status === 'blocked').map(u => (
                      <div key={u.id} className="px-4 py-3 flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-red-50 flex items-center justify-center flex-shrink-0">
                          <span className="text-xs font-bold text-red-600">{(u.name || '?')[0].toUpperCase()}</span>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium text-slate-800 truncate">{u.name || '—'}</p>
                            {u.role && <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${roleBadgeCls(u.role)}`}>{roleLabel(u.role)}</span>}
                          </div>
                          <p className="text-xs text-slate-400 truncate">{u.email} · {u.company_name || 'No company'}</p>
                        </div>
                        <button onClick={() => updateUserStatus(u.id, 'active', u.name)} disabled={!!actionLoading}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 transition-colors disabled:opacity-40">
                          <CheckCircle size={12} /> Unblock
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Auth / security events log */}
              <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
                <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
                  <Activity size={15} className="text-blue-500" />
                  <span className="text-sm font-semibold text-slate-800">Recent Auth Events</span>
                  <span className="ml-auto text-xs text-slate-400">{authLogs.length} events</span>
                </div>
                {authLogs.length === 0 ? (
                  <div className="text-center py-8 text-slate-400 text-sm">No auth events recorded.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50/80 border-b border-slate-100">
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Time</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Event</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">User</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Company</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">IP / Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {authLogs.slice(0, 50).map((l, i) => (
                          <tr key={l.id || i} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                            <td className="px-4 py-2.5 text-xs text-slate-400 whitespace-nowrap">{l.event_time ? new Date(l.event_time).toLocaleString() : '—'}</td>
                            <td className="px-4 py-2.5">
                              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                                l.event_type === 'login_failed' ? 'bg-red-50 text-red-600' :
                                l.event_type === 'login'        ? 'bg-emerald-50 text-emerald-600' :
                                l.event_type === 'logout'       ? 'bg-slate-100 text-slate-500' :
                                'bg-blue-50 text-blue-600'
                              }`}>{l.event_type || 'unknown'}</span>
                            </td>
                            <td className="px-4 py-2.5 text-xs max-w-[140px]">
                              <p className="text-slate-700 font-medium truncate">{l.user_name || 'Unknown user'}</p>
                              {(userMap[l.user_id] || {}).role && <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${roleBadgeCls((userMap[l.user_id] || {}).role)}`}>{roleLabel((userMap[l.user_id] || {}).role)}</span>}
                            </td>
                            <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[120px] truncate">{l.company_name || 'Unknown company'}</td>
                            <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[180px] truncate">{l.ip_address || l.details || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Recent Login Activity — all users */}
              <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
                <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
                  <Clock size={15} className="text-slate-500" />
                  <span className="text-sm font-semibold text-slate-800">Recent Login Activity</span>
                  <span className="ml-auto text-xs text-slate-400">{authLogs.length} events</span>
                </div>
                {authLogs.length === 0 ? (
                  <div className="text-center py-8 text-slate-400 text-sm">No login activity recorded.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50/80 border-b border-slate-100">
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Status</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Event</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">User</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Company</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Auth Method</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">IP Address</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Date & Time</th>
                        </tr>
                      </thead>
                      <tbody>
                        {authLogs.slice(0, 100).map((h, i) => {
                          const isSuccess = h.event_type === 'login' || h.event_type?.includes('login_success');
                          const isFailed  = h.event_type === 'login_failed' || h.event_type?.includes('failed');
                          const isLogout  = h.event_type === 'logout';
                          const isReset   = h.event_type?.includes('password_reset');
                          const dotColor  = isSuccess ? 'bg-emerald-400' : isFailed ? 'bg-red-400' : isLogout ? 'bg-slate-300' : isReset ? 'bg-blue-400' : 'bg-amber-400';
                          const badgeCls  = isSuccess ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : isFailed  ? 'bg-red-50 text-red-600 border-red-200'
                            : isLogout  ? 'bg-slate-100 text-slate-500 border-slate-200'
                            : isReset   ? 'bg-blue-50 text-blue-600 border-blue-200'
                            : 'bg-amber-50 text-amber-600 border-amber-200';
                          const eventLabel = (h.event_type || 'unknown').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                          return (
                            <tr key={h.id || i} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                              <td className="px-4 py-2.5"><span className={`w-2 h-2 rounded-full inline-block ${dotColor}`} /></td>
                              <td className="px-4 py-2.5"><span className={`text-[10px] px-2 py-0.5 rounded border font-medium ${badgeCls}`}>{eventLabel}</span></td>
                              <td className="px-4 py-2.5 text-xs max-w-[140px]">
                                <p className="text-slate-700 font-medium truncate">{h.user_name || 'Unknown user'}</p>
                                {(userMap[h.user_id] || {}).role && <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${roleBadgeCls((userMap[h.user_id] || {}).role)}`}>{roleLabel((userMap[h.user_id] || {}).role)}</span>}
                              </td>
                              <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[120px] truncate">{h.company_name || 'Unknown company'}</td>
                              <td className="px-4 py-2.5"><span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 font-medium capitalize">{h.auth_provider || 'email'}</span></td>
                              <td className="px-4 py-2.5 text-xs text-slate-400 font-mono">{h.ip_address || '—'}</td>
                              <td className="px-4 py-2.5 text-xs text-slate-400 whitespace-nowrap">{h.event_time ? new Date(h.event_time).toLocaleString() : '—'}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Login sessions */}
              <div className="bg-white rounded-xl border border-slate-100 overflow-hidden shadow-sm">
                <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
                  <Globe size={15} className="text-indigo-500" />
                  <span className="text-sm font-semibold text-slate-800">Login Sessions</span>
                  <span className="ml-auto text-xs text-slate-400">{loginSessions.length} sessions</span>
                </div>
                {loginSessions.length === 0 ? (
                  <div className="text-center py-8 text-slate-400 text-sm">No sessions recorded.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50/80 border-b border-slate-100">
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">User</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Company</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Login Time</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">Auth Method</th>
                          <th className="text-left text-[11px] font-bold text-slate-400 uppercase tracking-wider px-4 py-3">IP</th>
                        </tr>
                      </thead>
                      <tbody>
                        {loginSessions.slice(0, 50).map((s, i) => (
                          <tr key={s.id || i} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                            <td className="px-4 py-2.5 text-xs max-w-[140px]">
                              <p className="text-slate-700 font-medium truncate">{s.user_name || 'Unknown user'}</p>
                              {(userMap[s.user_id] || {}).role && <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${roleBadgeCls((userMap[s.user_id] || {}).role)}`}>{roleLabel((userMap[s.user_id] || {}).role)}</span>}
                            </td>
                            <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[120px] truncate">{s.company_name || 'Unknown company'}</td>
                            <td className="px-4 py-2.5 text-xs text-slate-400 whitespace-nowrap">{s.login_time ? new Date(s.login_time).toLocaleString() : '—'}</td>
                            <td className="px-4 py-2.5 text-xs">
                              <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-medium">{s.auth_provider || 'email'}</span>
                            </td>
                            <td className="px-4 py-2.5 text-xs text-slate-400">{s.ip_address || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
      {confirmDialog}
    </div>
  );
}
