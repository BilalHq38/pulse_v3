import { create } from 'zustand';
import { identityUnificationApi, isTenantConfigUnavailableError } from '@/lib/identityUnificationApi';

const DEFAULT_TENANT_ID = String(process.env.REACT_APP_DEFAULT_TENANT_ID || '').trim();

const normalizeId = (value) => String(value || '').trim();
const normalizeRole = (value) => String(value || '').trim().toLowerCase();

const getErrorMessage = (error, fallbackMessage) => {
  const detail =
    error?.response?.data?.detail ||
    error?.response?.data?.error ||
    error?.message ||
    '';
  return String(detail || fallbackMessage || 'Request failed.');
};

const isAdminRole = (role) => {
  const normalized = normalizeRole(role);
  return normalized === 'admin' || normalized === 'super_admin';
};

const hasManualMergeHistory = (profile = {}) => {
  const history = Array.isArray(profile.merge_history) ? profile.merge_history : [];
  return history.some((entry) => {
    const mergedBy = normalizeRole(entry?.merged_by);
    const mergeReason = String(entry?.merge_reason || '').toLowerCase();
    return mergedBy === 'manual' || mergeReason.includes('manual');
  });
};

export const deriveTagKind = (profile = {}, profileMeta = null) => {
  if (profileMeta?.reviewRequired || profile.review_required) {
    return 'review_required';
  }

  if (profileMeta?.manualMerge || hasManualMergeHistory(profile)) {
    return 'manual_merge';
  }

  const matchType = normalizeRole(profileMeta?.matchType || profile.match_type);
  if (matchType === 'deterministic') return 'deterministic';
  if (matchType === 'probabilistic') return 'probabilistic';
  if (matchType.includes('ai') || matchType.includes('vector')) return 'ai_match';

  const confidence =
    Number(
      profileMeta?.confidenceScore ||
        profile.confidence_score ||
        profile.profile_confidence ||
        0,
    ) || 0;

  if (confidence >= 0.9) return 'deterministic';
  if (confidence >= 0.6) return 'probabilistic';
  return 'ai_match';
};

export const buildTenantContextFromUser = (user = null) => {
  const userRole = normalizeRole(user?.role) || 'company_agent';
  const companyTenantId = normalizeId(user?.company_id);

  return {
    tenantId: companyTenantId || DEFAULT_TENANT_ID,
    userRole,
  };
};

const upsertProfile = (profiles, incomingProfile) => {
  const nextProfile = { ...(incomingProfile || {}) };
  const profileId = normalizeId(nextProfile.id || nextProfile.customer_id);
  if (!profileId) return profiles;

  nextProfile.id = profileId;
  nextProfile.customer_id = normalizeId(nextProfile.customer_id || profileId);

  const index = profiles.findIndex((item) => normalizeId(item.id) === profileId);
  if (index < 0) return [nextProfile, ...profiles];

  const clone = [...profiles];
  clone[index] = { ...clone[index], ...nextProfile };
  return clone;
};

const updateProfileMeta = (currentMeta, profileId, metaPatch) => {
  const key = normalizeId(profileId);
  if (!key) return currentMeta;
  return {
    ...currentMeta,
    [key]: {
      ...(currentMeta[key] || {}),
      ...(metaPatch || {}),
      updatedAt: Date.now(),
    },
  };
};

export const useIdentityUnificationStore = create((set, get) => ({
  tenantContext: {
    tenantId: DEFAULT_TENANT_ID,
    userRole: 'company_agent',
  },
  profiles: [],
  profileDetails: {},
  profileMeta: {},
  reviewQueue: [],
  selectedProfileIds: [],
  lastOperation: null,
  errorMessage: '',
  loadingProfiles: false,
  loadingDetails: false,
  loadingReviewQueue: false,
  actionInFlight: false,

  initializeTenantContext: (user) => {
    const context = buildTenantContextFromUser(user);
    get().setTenantContext(context, { persist: false });
    return context;
  },

  setTenantContext: (tenantContext, options = {}) => {
    const current = get().tenantContext;

    const nextTenantId = normalizeId(tenantContext?.tenantId || current.tenantId);
    const nextUserRole = normalizeRole(tenantContext?.userRole || current.userRole || 'company_agent');

    const tenantChanged = normalizeId(current.tenantId) !== nextTenantId;

    set((state) => ({
      tenantContext: {
        tenantId: nextTenantId,
        userRole: nextUserRole,
      },
      ...(tenantChanged
        ? {
            profiles: [],
            profileDetails: {},
            profileMeta: {},
            reviewQueue: [],
            selectedProfileIds: [],
            lastOperation: null,
          }
        : {}),
      errorMessage: '',
    }));
  },

  toggleProfileSelection: (profileId) => {
    const key = normalizeId(profileId);
    if (!key) return;
    set((state) => {
      const exists = state.selectedProfileIds.includes(key);
      return {
        selectedProfileIds: exists
          ? state.selectedProfileIds.filter((id) => id !== key)
          : [...state.selectedProfileIds, key],
      };
    });
  },

  clearSelection: () => set({ selectedProfileIds: [] }),

  setOperationResult: (operation) => set({ lastOperation: operation || null }),

  clearError: () => set({ errorMessage: '' }),

  fetchProfiles: async () => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);

    set({ loadingProfiles: true, errorMessage: '' });

    try {
      const profiles = await identityUnificationApi.listProfiles();
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return;

      set((state) => {
        const merged = profiles.reduce((acc, profile) => upsertProfile(acc, profile), state.profiles);
        return { profiles: merged };
      });
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return;
      if (isTenantConfigUnavailableError(error)) {
        set({ profiles: [], errorMessage: '' });
        return;
      }
      set({ errorMessage: getErrorMessage(error, 'Failed to load unified profiles.') });
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ loadingProfiles: false });
      }
    }
  },

  fetchProfileDetail: async (profileId, options = {}) => {
    const key = normalizeId(profileId);
    if (!key) return null;

    const force = Boolean(options.force);
    const existingDetail = get().profileDetails[key];
    if (existingDetail && !force) return existingDetail;

    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);
    set({ loadingDetails: true, errorMessage: '' });

    try {
      const detail = await identityUnificationApi.getProfileDetail(key);
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      set((state) => ({
        profiles: upsertProfile(state.profiles, detail),
        profileDetails: {
          ...state.profileDetails,
          [key]: detail,
        },
        profileMeta: updateProfileMeta(state.profileMeta, key, {
          confidenceScore:
            Number(detail.profile_confidence || detail.confidence_score || 0) || 0,
          manualMerge: hasManualMergeHistory(detail),
        }),
      }));

      return detail;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      set({ errorMessage: getErrorMessage(error, 'Failed to load profile detail.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ loadingDetails: false });
      }
    }
  },

  fetchReviewQueue: async () => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);

    set({ loadingReviewQueue: true, errorMessage: '' });

    try {
      const queue = await identityUnificationApi.listSuggestions();
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return;

      set((state) => {
        let profileMeta = { ...state.profileMeta };
        queue.forEach((item) => {
          const sourceId = normalizeId(item.customer_id_a);
          const targetId = normalizeId(item.customer_id_b);
          if (sourceId) {
            profileMeta = updateProfileMeta(profileMeta, sourceId, {
              reviewRequired: true,
              reviewStatus: String(item.status || 'pending'),
            });
          }
          if (targetId) {
            profileMeta = updateProfileMeta(profileMeta, targetId, {
              reviewRequired: true,
              reviewStatus: String(item.status || 'pending'),
            });
          }
        });

        return {
          reviewQueue: Array.isArray(queue) ? queue : [],
          profileMeta,
        };
      });
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return;
      if (isTenantConfigUnavailableError(error)) {
        set({ reviewQueue: [], errorMessage: '' });
        return;
      }
      set({ errorMessage: getErrorMessage(error, 'Failed to load review queue.') });
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ loadingReviewQueue: false });
      }
    }
  },

  refreshAll: async () => {
    await Promise.all([get().fetchProfiles(), get().fetchReviewQueue()]);
  },

  runResolve: async (payload) => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);
    set({ actionInFlight: true, errorMessage: '' });

    try {
      const result = await identityUnificationApi.resolve(payload);
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      const profile = result.profile || {};
      const profileId = normalizeId(profile.id || result.customer_id);

      set((state) => ({
        profiles: profileId ? upsertProfile(state.profiles, { ...profile, id: profileId }) : state.profiles,
        profileMeta: profileId
          ? updateProfileMeta(state.profileMeta, profileId, {
              matchType: result.match_type,
              reviewRequired: Boolean(result.review_required),
              reviewStatus: result.review_required ? 'pending' : '',
              confidenceScore: Number(result.confidence_score || 0) || 0,
            })
          : state.profileMeta,
        lastOperation: {
          type: 'resolve',
          requestPayload: payload,
          response: result,
          timestamp: Date.now(),
        },
      }));

      return result;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      if (isTenantConfigUnavailableError(error)) {
        try {
          const publicResult = await identityUnificationApi.submitPublic(payload);
          set({
            lastOperation: {
              type: 'public-unification',
              requestPayload: payload,
              response: publicResult,
              timestamp: Date.now(),
            },
            errorMessage: '',
          });
          return publicResult;
        } catch (publicError) {
          set({ errorMessage: getErrorMessage(publicError, 'Public unification submission failed.') });
          return null;
        }
      }
      set({ errorMessage: getErrorMessage(error, 'Identity resolve failed.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ actionInFlight: false });
      }
    }
  },

  runUnify: async (payload) => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);
    set({ actionInFlight: true, errorMessage: '' });

    try {
      const result = await identityUnificationApi.unify(payload);
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      const profile = result.profile || {};
      const profileId = normalizeId(profile.id || result.customer_id);

      set((state) => ({
        profiles: profileId ? upsertProfile(state.profiles, { ...profile, id: profileId }) : state.profiles,
        profileMeta: profileId
          ? updateProfileMeta(state.profileMeta, profileId, {
              matchType: result.match_type,
              reviewRequired: Boolean(result.review_required),
              reviewStatus: result.review_required ? 'pending' : '',
              confidenceScore: Number(result.confidence_score || 0) || 0,
            })
          : state.profileMeta,
        lastOperation: {
          type: 'unify',
          requestPayload: payload,
          response: result,
          timestamp: Date.now(),
        },
      }));

      return result;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      if (isTenantConfigUnavailableError(error)) {
        try {
          const publicResult = await identityUnificationApi.submitPublic(payload);
          set({
            lastOperation: {
              type: 'public-unification',
              requestPayload: payload,
              response: publicResult,
              timestamp: Date.now(),
            },
            errorMessage: '',
          });
          return publicResult;
        } catch (publicError) {
          set({ errorMessage: getErrorMessage(publicError, 'Public unification submission failed.') });
          return null;
        }
      }
      set({ errorMessage: getErrorMessage(error, 'Identity unification failed.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ actionInFlight: false });
      }
    }
  },

  runMerge: async (profileIds = null) => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);
    const ids = Array.isArray(profileIds) ? profileIds : get().selectedProfileIds;
    const normalized = ids.map((item) => normalizeId(item)).filter(Boolean);

    if (normalized.length < 2) {
      set({ errorMessage: 'Select at least two profiles to merge.' });
      return null;
    }

    set({ actionInFlight: true, errorMessage: '' });

    try {
      const profile = await identityUnificationApi.merge(normalized);
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      const profileId = normalizeId(profile.id || profile.customer_id);
      set((state) => ({
        profiles: profileId ? upsertProfile(state.profiles, { ...profile, id: profileId }) : state.profiles,
        profileMeta: profileId
          ? updateProfileMeta(state.profileMeta, profileId, {
              manualMerge: true,
              matchType: 'manual_merge',
              reviewRequired: false,
              reviewStatus: '',
            })
          : state.profileMeta,
        selectedProfileIds: [],
        lastOperation: {
          type: 'merge',
          requestPayload: { profile_ids: normalized },
          response: profile,
          timestamp: Date.now(),
        },
      }));

      await get().fetchProfiles();
      return profile;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      set({ errorMessage: getErrorMessage(error, 'Manual merge failed.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ actionInFlight: false });
      }
    }
  },

  runSplit: async ({ profileId, customerId = '', mappingIds = [], fingerprintIds = [] }) => {
    const safeProfileId = normalizeId(profileId);
    if (!safeProfileId) {
      set({ errorMessage: 'Profile ID is required for split.' });
      return null;
    }

    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);

    set({ actionInFlight: true, errorMessage: '' });

    try {
      const response = await identityUnificationApi.split(
        {
          profileId: safeProfileId,
          customerId: customerId || safeProfileId,
          mappingIds,
          fingerprintIds,
        },
      );

      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      set({
        lastOperation: {
          type: 'split',
          requestPayload: {
            profile_id: safeProfileId,
            customer_id: customerId || safeProfileId,
            mapping_ids: mappingIds,
            fingerprint_ids: fingerprintIds,
          },
          response,
          timestamp: Date.now(),
        },
      });

      await Promise.all([get().fetchProfiles(), get().fetchProfileDetail(safeProfileId, { force: true })]);
      return response;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      set({ errorMessage: getErrorMessage(error, 'Profile split failed.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ actionInFlight: false });
      }
    }
  },

  runAutoDetect: async () => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);

    if (!isAdminRole(tenantContext.userRole)) {
      set({ errorMessage: 'Auto-detect requires admin role.' });
      return null;
    }

    set({ actionInFlight: true, errorMessage: '' });

    try {
      const result = await identityUnificationApi.autoDetect();
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      set({
        lastOperation: {
          type: 'auto-detect',
          requestPayload: {},
          response: result,
          timestamp: Date.now(),
        },
      });
      await get().fetchReviewQueue();
      return result;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      set({ errorMessage: getErrorMessage(error, 'Auto-detect failed.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ actionInFlight: false });
      }
    }
  },

  resolveReviewSuggestion: async ({ suggestionId, action, notes = '' }) => {
    const tenantContext = get().tenantContext;
    const requestTenant = normalizeId(tenantContext.tenantId);

    if (!isAdminRole(tenantContext.userRole)) {
      set({ errorMessage: 'Review actions require admin role.' });
      return null;
    }

    set({ actionInFlight: true, errorMessage: '' });

    try {
      const response = await identityUnificationApi.resolveSuggestion(
        suggestionId,
        action,
        notes,
      );

      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;

      set({
        lastOperation: {
          type: 'review-resolve',
          requestPayload: { suggestion_id: suggestionId, action, notes },
          response,
          timestamp: Date.now(),
        },
      });

      await Promise.all([get().fetchReviewQueue(), get().fetchProfiles()]);
      return response;
    } catch (error) {
      if (normalizeId(get().tenantContext.tenantId) !== requestTenant) return null;
      set({ errorMessage: getErrorMessage(error, 'Review resolution failed.') });
      return null;
    } finally {
      if (normalizeId(get().tenantContext.tenantId) === requestTenant) {
        set({ actionInFlight: false });
      }
    }
  },

  isAdmin: () => isAdminRole(get().tenantContext.userRole),
}));
