import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
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
    <div className="rounded-xl border border-slate-200 bg-white p-2.5 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      <p className="mt-1 text-xl font-bold text-slate-900">{value}</p>
      <div className="mt-1.5 flex items-center justify-between gap-2">
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
  const [guideExpanded, setGuideExpanded] = useState(true);
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
    const timer = window.setTimeout(() => setGuideExpanded(false), 3500);
    return () => window.clearTimeout(timer);
  }, []);

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
        message: `${response.moved_mapping_count || 0} mapping${response.moved_mapping_count === 1 ? '' : 's'} and ${response.moved_fingerprint_count || 0} device signal${response.moved_fingerprint_count === 1 ? '' : 's'} were moved to a new profile.`,
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
        message: 'The selected merge suggestion was processed successfully.',
      });
      return;
    }
    if (type === 'resolve' || type === 'unify') {
      showToast({
        type: 'success',
        title: type === 'unify' ? 'Profiles Linked' : 'Identity Resolved',
        message: `Profile ${(response.profile || {}).display_name || 'record'} was updated successfully.`,
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
        profile.platforms_used,
        profile.primary_email,
        profile.primary_phone,
        ...(Array.isArray(profile.source_channels) ? profile.source_channels : []),
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
    // SplitPanel already confirms before invoking this handler, so run the split directly.
    const result = await runSplit({ profileId, customerId, mappingIds, fingerprintIds });
    if (result) {
      await fetchReviewQueue();
    }
  };

  const handleResolveSuggestion = async ({ suggestionId, action, notes }) => {
    await resolveReviewSuggestion({ suggestionId, action, notes });
  };

  return (
    <div className="space-y-3 pb-5">
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700">
              <Shield size={11} />
              Tenant Scoped Identity
            </p>
            <h1 className="mt-2 text-xl font-bold text-slate-900">Customer Identity Unification</h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              Review matches, merge verified identities, and split noisy signals without exposing internal identifiers.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={refreshAll}
              disabled={loadingProfiles || loadingReviewQueue || actionInFlight}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 shadow-sm hover:bg-slate-100 disabled:opacity-50"
            >
              <RefreshCw size={16} className={loadingProfiles || loadingReviewQueue ? 'animate-spin' : ''} />
              Refresh Data
            </button>
            <button
              type="button"
              onClick={runAutoDetect}
              disabled={!isAdmin || actionInFlight}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 disabled:opacity-50"
            >
              <WandSparkles size={16} />
              Auto Detect Matches
            </button>
          </div>
        </div>

        {guideExpanded ? (
          <div className="mt-3 grid gap-2 md:grid-cols-4">
            {[
              ['1', 'Auto-detect', 'Find likely duplicates'],
              ['2', 'Review queue', 'Accept or skip suggestions'],
              ['3', 'Manual merge', 'Select profiles to combine'],
              ['4', 'Split signals', 'Detach incorrect links'],
            ].map(([step, title, hint]) => (
              <div key={step} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white">
                  {step}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-slate-800">{title}</p>
                  <p className="truncate text-[10px] text-slate-500">{hint}</p>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        <div className="mt-2 flex justify-center">
          <button
            type="button"
            onClick={() => setGuideExpanded((value) => !value)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-sm transition-colors hover:bg-slate-50 hover:text-slate-700"
            aria-label={guideExpanded ? 'Collapse guide' : 'Expand guide'}
          >
            {guideExpanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
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

      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
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

      <div className="grid gap-3 xl:grid-cols-[minmax(0,2.2fr),minmax(20rem,0.8fr)]">
        <div className="space-y-3">
          <section className="rounded-xl border border-slate-200 bg-white p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">Unified Profiles</h2>
                <p className="mt-1 text-xs text-slate-500">Browse compact identities and select profiles for manual merge.</p>
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
                  placeholder="Search name, contact, platform"
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
                  <div key={item} className="h-24 animate-pulse rounded-xl border border-slate-200 bg-slate-100" />
                ))}
              </div>
            ) : filteredProfiles.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-10 text-center text-xs text-slate-500">
                No profiles match the current filters.
              </div>
            ) : (
              <div className="space-y-2">
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

        <div className="space-y-3 xl:sticky xl:top-4 xl:self-start">
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
