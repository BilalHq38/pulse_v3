import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  GitMerge,
  RefreshCw,
  Search,
  Shield,
  WandSparkles,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import MergeModal from '@/components/unification/MergeModal';
import ProfileCard from '@/components/unification/ProfileCard';
import ReviewQueuePanel from '@/components/unification/ReviewQueuePanel';
import SplitPanel from '@/components/unification/SplitPanel';
import TagBadge from '@/components/unification/TagBadge';
import { deriveTagKind, useIdentityUnificationStore } from '@/stores/useIdentityUnificationStore';
import { showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import { useSocket } from '@/lib/useSocket';

const TAG_FILTERS = [
  { value: 'all', label: 'All Profiles' },
  { value: 'review_required', label: 'Needs Review' },
  { value: 'manual_merge', label: 'Manual Merge' },
  { value: 'deterministic', label: 'Deterministic' },
  { value: 'high_confidence_probabilistic', label: 'High Confidence' },
  { value: 'probabilistic', label: 'Probabilistic' },
  { value: 'ai_match', label: 'AI Match' },
];

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
  const { confirmDialog } = useConfirmDialog();

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
  const toggleProfileSelection = useIdentityUnificationStore((state) => state.toggleProfileSelection);
  const clearSelection = useIdentityUnificationStore((state) => state.clearSelection);
  const fetchProfileDetail = useIdentityUnificationStore((state) => state.fetchProfileDetail);
  const fetchReviewQueue = useIdentityUnificationStore((state) => state.fetchReviewQueue);
  const refreshAll = useIdentityUnificationStore((state) => state.refreshAll);
  const runMerge = useIdentityUnificationStore((state) => state.runMerge);
  const runSplit = useIdentityUnificationStore((state) => state.runSplit);
  const runAutoDetect = useIdentityUnificationStore((state) => state.runAutoDetect);
  const resolveReviewSuggestion = useIdentityUnificationStore((state) => state.resolveReviewSuggestion);

  const [mergeModalOpen, setMergeModalOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [tagFilter, setTagFilter] = useState('all');
  const [selectedDetailId, setSelectedDetailId] = useState('');
  const lastOperationToastRef = useRef(0);
  const lastErrorToastRef = useRef('');

  const isAdmin = user?.role === 'admin' || user?.role === 'super_admin';

  useEffect(() => {
    if (!user?.id) return;
    initializeTenantContext(user);
    refreshAll();
  }, [
    user,
    initializeTenantContext,
    refreshAll,
  ]);

  useEffect(() => {
    const operationTimestamp = Number(lastOperation?.timestamp || 0) || 0;
    if (!operationTimestamp || operationTimestamp === lastOperationToastRef.current) return;
    lastOperationToastRef.current = operationTimestamp;

    const type = String(lastOperation?.type || '').trim();
    const response = lastOperation?.response || {};
    const requestPayload = lastOperation?.requestPayload || {};

    if (type === 'merge') {
      showToast({
        type: 'success',
        title: 'Profiles Merged',
        message: `${Array.isArray(requestPayload.profile_ids) ? requestPayload.profile_ids.length : 0} profiles were merged into one identity.`,
      });
      return;
    }
    if (type === 'split') {
      showToast({
        type: 'success',
        title: 'Split Complete',
        message: `${response.moved_mapping_count || 0} mapping${response.moved_mapping_count === 1 ? '' : 's'} and ${response.moved_fingerprint_count || 0} fingerprint${response.moved_fingerprint_count === 1 ? '' : 's'} were moved to a new profile.`,
      });
      return;
    }
    if (type === 'auto-detect') {
      showToast({
        type: 'success',
        title: 'Scan Complete',
        message: `Found ${response.new_suggestions || 0} new merge suggestion${response.new_suggestions === 1 ? '' : 's'}.`,
      });
      return;
    }
    if (type === 'review-resolve') {
      showToast({
        type: 'success',
        title: 'Review Updated',
        message: `Suggestion ${response.suggestion_id || response.resolution_id || ''} was processed successfully.`,
      });
      return;
    }
    if (type === 'resolve' || type === 'unify') {
      showToast({
        type: 'success',
        title: type === 'unify' ? 'Profiles Linked' : 'Identity Resolved',
        message: `Profile ${(response.profile || {}).display_name || response.customer_id || 'record'} was updated successfully.`,
      });
    }
  }, [lastOperation]);

  useEffect(() => {
    const safeError = String(errorMessage || '').trim();
    if (!safeError || safeError === lastErrorToastRef.current) return;
    lastErrorToastRef.current = safeError;
    showToast({
      type: 'error',
      title: 'Action Failed',
      message: safeError,
    });
  }, [errorMessage]);

  const handleIdentitySocketEvent = useCallback(async () => {
    await refreshAll();
    if (selectedDetailId) {
      await fetchProfileDetail(selectedDetailId, { force: true });
    }
  }, [fetchProfileDetail, refreshAll, selectedDetailId]);

  useSocket((eventName) => {
    if (['identity_merged', 'identity_split', 'identity_resolved'].includes(eventName)) {
      void handleIdentitySocketEvent();
    }
  });

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

  const handleSplit = async ({ profileId, customerId, mappingIds, fingerprintIds }) => {
    // SplitPanel already shows SplitConfirmModal before invoking this handler,
    // so run the split directly — no second confirmation dialog needed.
    const result = await runSplit({ profileId, customerId, mappingIds, fingerprintIds });
    if (result) {
      await fetchReviewQueue();
    }
  };

  const handleResolveSuggestion = async ({ suggestionId, action, notes }) => {
    await resolveReviewSuggestion({ suggestionId, action, notes });
  };

  return (
    <div className="space-y-6 pb-8">
      <section className="rounded-3xl border border-slate-200 bg-white shadow-sm">
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

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={refreshAll}
                disabled={loadingProfiles || loadingReviewQueue || actionInFlight}
                className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-bold text-slate-800 shadow-md shadow-slate-200/70 hover:bg-slate-100 disabled:opacity-50"
              >
                <RefreshCw size={18} className={loadingProfiles || loadingReviewQueue ? 'animate-spin' : ''} />
                Refresh Data
              </button>
              <button
                type="button"
                onClick={runAutoDetect}
                disabled={!isAdmin || actionInFlight}
                className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 text-sm font-bold text-white shadow-md shadow-blue-200 hover:bg-blue-500 disabled:opacity-50"
              >
                <WandSparkles size={18} />
                Auto Detect Matches
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

      <ReviewQueuePanel
        queue={reviewQueue}
        loading={loadingReviewQueue}
        actionInFlight={actionInFlight}
        isAdmin={isAdmin}
        onResolve={handleResolveSuggestion}
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2.15fr),minmax(20rem,0.85fr)]">
        <div className="space-y-6">
          <section className="rounded-2xl border border-slate-200 bg-white p-5">
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
      {confirmDialog}
    </div>
  );
}
