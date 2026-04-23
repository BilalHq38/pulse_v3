import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowUpRight,
  GitMerge,
  RefreshCw,
  Search,
  Shield,
  Sparkles,
  WandSparkles,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import MergeModal from '@/components/unification/MergeModal';
import ProfileCard from '@/components/unification/ProfileCard';
import ReviewQueuePanel from '@/components/unification/ReviewQueuePanel';
import SplitPanel from '@/components/unification/SplitPanel';
import TagBadge from '@/components/unification/TagBadge';
import { deriveTagKind, useIdentityUnificationStore } from '@/stores/useIdentityUnificationStore';

const TAG_FILTERS = [
  { value: 'all', label: 'All Profiles' },
  { value: 'review_required', label: 'Needs Review' },
  { value: 'manual_merge', label: 'Manual Merge' },
  { value: 'deterministic', label: 'Deterministic' },
  { value: 'high_confidence_probabilistic', label: 'High Confidence' },
  { value: 'probabilistic', label: 'Probabilistic' },
  { value: 'ai_match', label: 'AI Match' },
];

const EMPTY_RESOLVE_FORM = {
  platform: 'pulse_customer',
  platform_user_id: '',
  phone_number: '',
  email_address: '',
  full_name: '',
  username: '',
};

const normalizeId = (value) => String(value || '').trim();

const operationLabel = (operation) => {
  const type = String(operation?.type || '').trim();
  if (type === 'resolve') return 'Identity resolve completed';
  if (type === 'unify') return 'Identity unify completed';
  if (type === 'merge') return 'Manual merge completed';
  if (type === 'split') return 'Split operation completed';
  if (type === 'auto-detect') return 'Auto-detect completed';
  if (type === 'review-resolve') return 'Review decision applied';
  return 'Operation completed';
};

const createResolvePayload = (draft) => {
  return {
    platform: draft.platform,
    platform_user_id: normalizeId(draft.platform_user_id),
    phone_number: normalizeId(draft.phone_number) || null,
    email_address: normalizeId(draft.email_address) || null,
    full_name: normalizeId(draft.full_name) || null,
    username: normalizeId(draft.username) || null,
  };
};

function StatCard({ title, value, hint, badge }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      <p className="mt-2 text-2xl font-bold text-slate-900">{value}</p>
      <div className="mt-2 flex items-center justify-between gap-2">
        <p className="text-xs text-slate-500">{hint}</p>
        {badge ? <TagBadge kind={badge.kind} label={badge.label} compact /> : null}
      </div>
    </div>
  );
}

export default function UnificationPage() {
  const { user } = useAuth();

  const tenantContext = useIdentityUnificationStore((state) => state.tenantContext);
  const profiles = useIdentityUnificationStore((state) => state.profiles);
  const profileDetails = useIdentityUnificationStore((state) => state.profileDetails);
  const profileMeta = useIdentityUnificationStore((state) => state.profileMeta);
  const reviewQueue = useIdentityUnificationStore((state) => state.reviewQueue);
  const selectedProfileIds = useIdentityUnificationStore((state) => state.selectedProfileIds);
  const lastOperation = useIdentityUnificationStore((state) => state.lastOperation);
  const errorMessage = useIdentityUnificationStore((state) => state.errorMessage);
  const loadingProfiles = useIdentityUnificationStore((state) => state.loadingProfiles);
  const loadingDetails = useIdentityUnificationStore((state) => state.loadingDetails);
  const loadingReviewQueue = useIdentityUnificationStore((state) => state.loadingReviewQueue);
  const actionInFlight = useIdentityUnificationStore((state) => state.actionInFlight);

  const initializeTenantContext = useIdentityUnificationStore((state) => state.initializeTenantContext);
  const setTenantContext = useIdentityUnificationStore((state) => state.setTenantContext);
  const toggleProfileSelection = useIdentityUnificationStore((state) => state.toggleProfileSelection);
  const clearSelection = useIdentityUnificationStore((state) => state.clearSelection);
  const fetchProfileDetail = useIdentityUnificationStore((state) => state.fetchProfileDetail);
  const fetchReviewQueue = useIdentityUnificationStore((state) => state.fetchReviewQueue);
  const refreshAll = useIdentityUnificationStore((state) => state.refreshAll);
  const runResolve = useIdentityUnificationStore((state) => state.runResolve);
  const runUnify = useIdentityUnificationStore((state) => state.runUnify);
  const runMerge = useIdentityUnificationStore((state) => state.runMerge);
  const runSplit = useIdentityUnificationStore((state) => state.runSplit);
  const runAutoDetect = useIdentityUnificationStore((state) => state.runAutoDetect);
  const resolveReviewSuggestion = useIdentityUnificationStore((state) => state.resolveReviewSuggestion);

  const [mergeModalOpen, setMergeModalOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [tagFilter, setTagFilter] = useState('all');
  const [tenantDraft, setTenantDraft] = useState({ tenantId: '', apiKey: '' });
  const [selectedDetailId, setSelectedDetailId] = useState('');
  const [resolveDraft, setResolveDraft] = useState(EMPTY_RESOLVE_FORM);
  const [resolveResult, setResolveResult] = useState(null);

  const isAdmin = user?.role === 'admin' || user?.role === 'super_admin';
  const canSwitchTenant = user?.role === 'super_admin';

  useEffect(() => {
    if (!user?.id) return;
    const context = initializeTenantContext(user);
    setTenantDraft({ tenantId: context.tenantId, apiKey: context.apiKey });
    refreshAll();
  }, [
    user,
    initializeTenantContext,
    refreshAll,
  ]);

  const filteredProfiles = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();

    return profiles.filter((profile) => {
      const id = normalizeId(profile.id);
      const meta = profileMeta[id] || {};
      const tagKind = deriveTagKind(profile, meta);

      if (tagFilter !== 'all') {
        if (tagFilter === 'review_required' && !(profile.review_required || meta.reviewRequired)) {
          return false;
        }
        if (tagFilter !== 'review_required' && tagKind !== tagFilter) {
          return false;
        }
      }

      if (!normalizedSearch) return true;

      const haystack = [
        profile.display_name,
        profile.id,
        profile.customer_id,
        profile.platforms_used,
      ]
        .map((value) => String(value || '').toLowerCase())
        .join(' ');

      return haystack.includes(normalizedSearch);
    });
  }, [profiles, profileMeta, searchTerm, tagFilter]);

  const selectedProfileDetail = useMemo(() => {
    const detailId = normalizeId(selectedDetailId);
    if (!detailId) return null;
    return profileDetails[detailId] || profiles.find((profile) => normalizeId(profile.id) === detailId) || null;
  }, [selectedDetailId, profileDetails, profiles]);

  const pendingReviewCount = Array.isArray(reviewQueue) ? reviewQueue.length : 0;
  const totalLinkedProfiles = profiles.reduce((sum, profile) => sum + (Number(profile.member_count || 0) || 0), 0);

  const handleApplyTenantScope = async () => {
    const nextTenantId = normalizeId(tenantDraft.tenantId);
    const nextApiKey = normalizeId(tenantDraft.apiKey);
    if (!nextTenantId || !nextApiKey) return;

    setTenantContext({
      tenantId: nextTenantId,
      apiKey: nextApiKey,
      userRole: user?.role,
    });

    setSelectedDetailId('');
    setResolveResult(null);
    await refreshAll();
  };

  const handleOpenProfile = async (profileId) => {
    const safe = normalizeId(profileId);
    if (!safe) return;
    setSelectedDetailId(safe);
    await fetchProfileDetail(safe, { force: true });
  };

  const handleMergeConfirm = async (profileIds) => {
    const response = await runMerge(profileIds);
    if (response) {
      setMergeModalOpen(false);
      setSelectedDetailId(normalizeId(response.id || response.customer_id));
      if (response.id || response.customer_id) {
        await fetchProfileDetail(response.id || response.customer_id, { force: true });
      }
    }
  };

  const handleSplit = async ({ profileId, mappingIds, fingerprintIds }) => {
    const result = await runSplit({ profileId, mappingIds, fingerprintIds });
    if (result) {
      await fetchReviewQueue();
    }
  };

  const handleResolveSuggestion = async ({ suggestionId, action, notes }) => {
    await resolveReviewSuggestion({ suggestionId, action, notes });
  };

  const handleResolveAction = async (actionType) => {
    const payload = createResolvePayload(resolveDraft);
    if (!payload.platform_user_id) return;

    const result = actionType === 'unify' ? await runUnify(payload) : await runResolve(payload);
    if (result) {
      setResolveResult(result);
      if (result.customer_id) {
        setSelectedDetailId(result.customer_id);
        await fetchProfileDetail(result.customer_id, { force: true });
      }
      setResolveDraft((prev) => ({
        ...prev,
        platform_user_id: '',
      }));
    }
  };

  return (
    <div className="space-y-6 pb-8">
      <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
        <div className="bg-[radial-gradient(circle_at_top_left,_rgba(37,99,235,0.16),_transparent_50%),radial-gradient(circle_at_bottom_right,_rgba(2,132,199,0.2),_transparent_46%)] px-6 py-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-blue-700">
                <Shield size={12} />
                Tenant Scoped Identity
              </p>
              <h1 className="mt-3 text-2xl font-bold text-slate-900">Customer Identity Unification</h1>
              <p className="mt-2 max-w-2xl text-sm text-slate-600">
                Review candidate matches, merge verified identities, split noisy signals, and preserve auditable tenant boundaries.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={refreshAll}
                disabled={loadingProfiles || loadingReviewQueue || actionInFlight}
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                <RefreshCw size={13} className={loadingProfiles || loadingReviewQueue ? 'animate-spin' : ''} />
                Refresh Data
              </button>
              <button
                type="button"
                onClick={runAutoDetect}
                disabled={!isAdmin || actionInFlight}
                className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 disabled:opacity-50"
              >
                <WandSparkles size={13} />
                Auto-Detect Matches
              </button>
            </div>
          </div>
        </div>
      </section>

      {lastOperation ? (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-700">
          {operationLabel(lastOperation)}
        </div>
      ) : null}

      {errorMessage ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">
          <div className="flex items-center gap-2">
            <AlertCircle size={14} />
            <span>{errorMessage}</span>
          </div>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="Unified Profiles"
          value={profiles.length}
          hint="Profiles with at least two linked identities"
          badge={{ kind: 'deterministic', label: 'Identity Graph' }}
        />
        <StatCard
          title="Pending Reviews"
          value={pendingReviewCount}
          hint="Suggestions waiting for adjudication"
          badge={{ kind: 'review_required', label: 'Queue' }}
        />
        <StatCard
          title="Linked Members"
          value={totalLinkedProfiles}
          hint="Total individual members inside unified profiles"
          badge={{ kind: 'probabilistic', label: 'Signals' }}
        />
        <StatCard
          title="Selected For Merge"
          value={selectedProfileIds.length}
          hint="Profiles currently selected for manual merge"
          badge={{ kind: 'manual_merge', label: 'Manual Ops' }}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.4fr,1fr]">
        <div className="space-y-6">
          <section className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">Tenant Request Scope</h2>
                <p className="mt-1 text-xs text-slate-500">
                  Every call includes tenant and role headers. Requests are isolated per tenant in the gateway.
                </p>
              </div>
              <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
                <Shield size={12} />
                {tenantContext.userRole || 'company_agent'}
              </span>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-600">
                <span className="mb-1.5 block font-semibold text-slate-500">Tenant ID</span>
                <input
                  value={tenantDraft.tenantId}
                  onChange={(event) => setTenantDraft((prev) => ({ ...prev, tenantId: event.target.value }))}
                  disabled={!canSwitchTenant}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700 outline-none ring-blue-300 transition focus:border-blue-300 focus:ring disabled:cursor-not-allowed disabled:bg-slate-100"
                />
              </label>
              <label className="text-xs text-slate-600">
                <span className="mb-1.5 block font-semibold text-slate-500">Identity API Key</span>
                <input
                  value={tenantDraft.apiKey}
                  onChange={(event) => setTenantDraft((prev) => ({ ...prev, apiKey: event.target.value }))}
                  type="password"
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700 outline-none ring-blue-300 transition focus:border-blue-300 focus:ring"
                />
              </label>
            </div>

            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-slate-500">
                Active tenant: <span className="font-mono text-slate-700">{tenantContext.tenantId}</span>
              </p>
              <button
                type="button"
                onClick={handleApplyTenantScope}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-100 px-3 py-1.5 text-[11px] font-medium text-slate-700 hover:bg-slate-200"
              >
                <ArrowUpRight size={12} />
                Apply Scope
              </button>
            </div>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">Identity Resolve Console</h2>
                <p className="mt-1 text-xs text-slate-500">Resolve or unify incoming cross-platform records using the backend identity contract.</p>
              </div>
              <TagBadge kind="ai_match" label="Live API" compact />
            </div>

            <div className="grid gap-2 md:grid-cols-2">
              <label className="text-xs text-slate-500">
                Platform
                <select
                  value={resolveDraft.platform}
                  onChange={(event) => setResolveDraft((prev) => ({ ...prev, platform: event.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
                >
                  <option value="pulse_customer">pulse_customer</option>
                  <option value="whatsapp">whatsapp</option>
                  <option value="facebook">facebook</option>
                  <option value="instagram">instagram</option>
                  <option value="email">email</option>
                  <option value="web_chat">web_chat</option>
                </select>
              </label>
              <label className="text-xs text-slate-500">
                Platform User ID
                <input
                  value={resolveDraft.platform_user_id}
                  onChange={(event) => setResolveDraft((prev) => ({ ...prev, platform_user_id: event.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
                  placeholder="required"
                />
              </label>
              <label className="text-xs text-slate-500">
                Email
                <input
                  value={resolveDraft.email_address}
                  onChange={(event) => setResolveDraft((prev) => ({ ...prev, email_address: event.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
                  placeholder="optional"
                />
              </label>
              <label className="text-xs text-slate-500">
                Phone
                <input
                  value={resolveDraft.phone_number}
                  onChange={(event) => setResolveDraft((prev) => ({ ...prev, phone_number: event.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
                  placeholder="optional"
                />
              </label>
              <label className="text-xs text-slate-500">
                Full Name
                <input
                  value={resolveDraft.full_name}
                  onChange={(event) => setResolveDraft((prev) => ({ ...prev, full_name: event.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
                  placeholder="optional"
                />
              </label>
              <label className="text-xs text-slate-500">
                Username
                <input
                  value={resolveDraft.username}
                  onChange={(event) => setResolveDraft((prev) => ({ ...prev, username: event.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
                  placeholder="optional"
                />
              </label>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => handleResolveAction('resolve')}
                disabled={actionInFlight || !normalizeId(resolveDraft.platform_user_id)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-50"
              >
                <Search size={12} />
                Resolve
              </button>
              <button
                type="button"
                onClick={() => handleResolveAction('unify')}
                disabled={actionInFlight || !normalizeId(resolveDraft.platform_user_id)}
                className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
              >
                <Sparkles size={12} />
                Unify
              </button>
            </div>

            {resolveResult ? (
              <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs">
                <p className="font-semibold text-slate-700">Result</p>
                <p className="mt-1 text-slate-600">Customer ID: {resolveResult.customer_id || 'n/a'}</p>
                <p className="text-slate-600">Match Type: {resolveResult.match_type || 'n/a'}</p>
                <p className="text-slate-600">Confidence: {Math.round((Number(resolveResult.confidence_score || 0) || 0) * 100)}%</p>
                <p className="text-slate-600">Review Required: {resolveResult.review_required ? 'yes' : 'no'}</p>
              </div>
            ) : null}
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">Unified Profiles</h2>
                <p className="mt-1 text-xs text-slate-500">Browse unified identities and select candidates for manual merge.</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={clearSelection}
                  className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
                  disabled={!selectedProfileIds.length}
                >
                  Clear Selection
                </button>
                <button
                  type="button"
                  onClick={() => setMergeModalOpen(true)}
                  className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
                  disabled={!isAdmin}
                >
                  <GitMerge size={12} />
                  Merge Selected
                </button>
              </div>
            </div>

            <div className="mb-3 flex flex-wrap gap-2">
              <div className="relative min-w-[14rem] flex-1">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-2.5 text-slate-400" />
                <input
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  placeholder="Search name, profile ID, platform"
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-8 pr-2.5 text-xs text-slate-700"
                />
              </div>
              <select
                value={tagFilter}
                onChange={(event) => setTagFilter(event.target.value)}
                className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-700"
              >
                {TAG_FILTERS.map((filter) => (
                  <option key={filter.value} value={filter.value}>
                    {filter.label}
                  </option>
                ))}
              </select>
            </div>

            {loadingProfiles ? (
              <div className="space-y-2">
                {[1, 2, 3].map((item) => (
                  <div key={item} className="h-40 animate-pulse rounded-2xl border border-slate-200 bg-slate-100" />
                ))}
              </div>
            ) : filteredProfiles.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-10 text-center text-xs text-slate-500">
                No profiles match the current filters.
              </div>
            ) : (
              <div className="space-y-3">
                {filteredProfiles.map((profile) => (
                  <ProfileCard
                    key={profile.id}
                    profile={profile}
                    profileMeta={profileMeta[normalizeId(profile.id)]}
                    selected={selectedProfileIds.includes(normalizeId(profile.id))}
                    onToggleSelect={toggleProfileSelection}
                    onOpenDetail={handleOpenProfile}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        <div className="space-y-6">
          <ReviewQueuePanel
            queue={reviewQueue}
            loading={loadingReviewQueue}
            actionInFlight={actionInFlight}
            isAdmin={isAdmin}
            onResolve={handleResolveSuggestion}
          />

          <SplitPanel
            profileDetail={selectedProfileDetail}
            loading={loadingDetails}
            actionInFlight={actionInFlight}
            isAdmin={isAdmin}
            onRefresh={handleOpenProfile}
            onSubmitSplit={handleSplit}
          />
        </div>
      </div>

      <MergeModal
        isOpen={mergeModalOpen}
        onClose={() => setMergeModalOpen(false)}
        profiles={profiles}
        selectedIds={selectedProfileIds}
        onToggleSelected={toggleProfileSelection}
        onClearSelection={clearSelection}
        onConfirm={handleMergeConfirm}
        actionInFlight={actionInFlight}
      />
    </div>
  );
}
