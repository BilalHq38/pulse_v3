import api from '@/lib/api';

const asArray = (value) => (Array.isArray(value) ? value : []);

const asObject = (value) => (value && typeof value === 'object' ? value : {});

const isTenantConfigUnavailableError = (error) => {
  const detail = String(
    error?.response?.data?.detail ||
      error?.response?.data?.error ||
      error?.message ||
      '',
  )
    .trim()
    .toLowerCase();
  return (
    detail.includes('identity tenant configuration unavailable') ||
    detail.includes('tenant configuration unavailable') ||
    detail.includes('tenant identity configuration missing')
  );
};

const normalizeProfileSummary = (profile = {}) => {
  const item = asObject(profile);
  const id = String(item.id || item.customer_id || '').trim();
  return {
    id,
    customer_id: String(item.customer_id || id || '').trim(),
    display_name: String(item.display_name || '').trim(),
    primary_email: String(item.primary_email || '').trim(),
    primary_phone: String(item.primary_phone || '').trim(),
    avatar_url: String(item.avatar_url || item.profile_picture_url || '').trim(),
    platforms_used: String(item.platforms_used || '').trim(),
    source_channels: asArray(item.source_channels),
    member_count: Number(item.member_count || 0) || 0,
    members: asArray(item.members),
    total_interactions: Number(item.total_interactions || 0) || 0,
    lifetime_value: Number(item.lifetime_value || 0) || 0,
    all_conversations: asArray(item.all_conversations),
    match_type: String(item.match_type || '').trim(),
    review_required: Boolean(item.review_required),
    review_status: String(item.review_status || '').trim(),
    confidence_score: Number(item.confidence_score || 0) || 0,
    profile_confidence: Number(item.profile_confidence || item.confidence_score || 0) || 0,
  };
};

const normalizeCandidateProfile = (candidate = {}, fallback = {}) => {
  const item = asObject(candidate);
  const legacy = asObject(fallback);
  const id = String(item.id || item.customer_id || legacy.customer_id || '').trim();
  return {
    id,
    customer_id: String(item.customer_id || id || '').trim(),
    display_name: String(item.display_name || item.name || legacy.name || id || 'Unknown profile').trim(),
    email: String(item.email || legacy.email || '').trim(),
    phone: String(item.phone || legacy.phone || '').trim(),
    avatar_url: String(item.avatar_url || item.profile_picture_url || legacy.avatar_url || '').trim(),
    source_channels: asArray(item.source_channels || legacy.source_channels),
    company_name: String(item.company_name || legacy.company_name || '').trim(),
    description: String(item.description || legacy.description || '').trim(),
  };
};

const normalizeProfileDetail = (summary = {}, raw = {}) => {
  const mergedSummary = normalizeProfileSummary(summary);
  const rawProfile = asObject(raw);
  const mappings = asArray(rawProfile.mappings);
  const fingerprints = asArray(rawProfile.fingerprints);
  const mergeHistory = asArray(rawProfile.merge_history);

  return {
    ...mergedSummary,
    id: String(mergedSummary.id || rawProfile.customer_id || '').trim(),
    customer_id: String(rawProfile.customer_id || mergedSummary.customer_id || mergedSummary.id || '').trim(),
    tenant_id: String(rawProfile.tenant_id || '').trim(),
    profile_confidence: Number(rawProfile.profile_confidence || mergedSummary.profile_confidence || 0) || 0,
    created_at: rawProfile.created_at || null,
    updated_at: rawProfile.updated_at || null,
    mappings,
    fingerprints,
    merge_history: mergeHistory,
  };
};

const normalizeSuggestion = (suggestion = {}) => {
  const item = asObject(suggestion);
  // Compat contract: the backend service resolves by `resolution_id`, while the
  // UI historically addressed the same record as `suggestion_id`. We normalize
  // both to the same value so either field name stays safe to use.
  const resolutionId = String(
    item.resolution_id || item.suggestion_id || item.id || '',
  ).trim();
  const matchedFields = asArray(item.matched_fields || item.match_reasons_list)
    .map((field) => String(field || '').trim())
    .filter(Boolean);
  const sourceChannels = asArray(item.source_channels)
    .map((channel) => String(channel || '').trim())
    .filter(Boolean);
  return {
    ...item,
    id: resolutionId,
    suggestion_id: resolutionId,
    resolution_id: resolutionId,
    review_id: String(item.review_id || '').trim(),
    match_score: Number(item.match_score || item.confidence || 0) || 0,
    confidence: Number(item.confidence || item.match_score || 0) || 0,
    matched_fields: matchedFields,
    source_channels: sourceChannels,
    candidate_a: normalizeCandidateProfile(item.candidate_a, {
      customer_id: item.customer_id_a,
      name: item.name_a,
      email: item.email_a,
      phone: item.phone_a,
      avatar_url: item.avatar_a,
      source_channels: item.channels_a,
      company_name: item.company_a || item.customer_company_name_a,
    }),
    candidate_b: normalizeCandidateProfile(item.candidate_b, {
      customer_id: item.customer_id_b,
      name: item.name_b,
      email: item.email_b,
      phone: item.phone_b,
      avatar_url: item.avatar_b,
      source_channels: item.channels_b,
      company_name: item.company_b || item.customer_company_name_b,
    }),
  };
};

export const identityUnificationApi = {
  async listProfiles() {
    const response = await api.get('/identity/profiles');
    return asArray(response.data).map((item) => normalizeProfileSummary(item));
  },

  async getProfileDetail(profileId) {
    const safeProfileId = String(profileId || '').trim();
    if (!safeProfileId) throw new Error('Select a profile before loading details.');

    const [summaryResult, rawResult] = await Promise.allSettled([
      api.get(`/identity/profiles/${encodeURIComponent(safeProfileId)}`),
      api.get(`/v1/identity/${encodeURIComponent(safeProfileId)}`),
    ]);

    if (summaryResult.status === 'rejected') {
      throw summaryResult.reason;
    }

    const summary = summaryResult.value?.data || {};
    const raw = rawResult.status === 'fulfilled' ? rawResult.value?.data || {} : {};
    return normalizeProfileDetail(summary, raw);
  },

  async resolve(payload) {
    const response = await api.post('/identity/resolve', payload);
    const data = asObject(response.data);
    return {
      ...data,
      profile: normalizeProfileSummary(data.profile || {}),
    };
  },

  async unify(payload) {
    const response = await api.post('/identity/unify', payload);
    const data = asObject(response.data);
    return {
      ...data,
      profile: normalizeProfileSummary(data.profile || {}),
    };
  },

  async merge(customerIds, mergeReason = 'manual_merge') {
    const normalizedIds = asArray(customerIds)
      .map((item) => String(item || '').trim())
      .filter(Boolean);

    const response = await api.post('/identity/merge', {
      customer_ids: normalizedIds,
      merge_reason: mergeReason,
    });

    return normalizeProfileSummary(response.data || {});
  },

  async split({ profileId, customerId = '', mappingIds = [], fingerprintIds = [], splitReason = 'manual_split' }) {
    const safeProfileId = String(profileId || '').trim();
    if (!safeProfileId) throw new Error('Select a profile before splitting.');

    const payload = {
      profile_id: safeProfileId,
      customer_id: String(customerId || safeProfileId).trim(),
      mapping_ids: asArray(mappingIds)
        .map((item) => String(item || '').trim())
        .filter(Boolean),
      fingerprint_ids: asArray(fingerprintIds)
        .map((item) => String(item || '').trim())
        .filter(Boolean),
      split_reason: String(splitReason || 'manual_split').trim() || 'manual_split',
    };

    const response = await api.post('/identity/split', payload);
    return asObject(response.data);
  },

  async getByCustomer(customerId) {
    const safeCustomerId = String(customerId || '').trim();
    if (!safeCustomerId) throw new Error('Select a customer before loading identity details.');

    const response = await api.get(`/identity/customer/${encodeURIComponent(safeCustomerId)}`);
    const data = asObject(response.data);
    return {
      ...data,
      profile: data.profile ? normalizeProfileSummary(data.profile) : null,
    };
  },

  async listSuggestions() {
    const response = await api.get('/identity/suggestions');
    return asArray(response.data).map((item) => normalizeSuggestion(item));
  },

  async resolveSuggestion(suggestionId, action, notes) {
    const safeSuggestionId = String(suggestionId || '').trim();
    if (!safeSuggestionId) throw new Error('Suggestion ID is required.');

    const response = await api.post(
      `/identity/suggestions/${encodeURIComponent(safeSuggestionId)}/resolve`,
      {
        suggestion_id: safeSuggestionId,
        resolution_id: safeSuggestionId,
        action: String(action || 'accept').trim(),
        notes: notes ? String(notes) : null,
      },
    );
    return normalizeSuggestion(asObject(response.data));
  },

  async syncCustomersToIdentity() {
    try {
      const response = await api.post('/customers/batch-sync-identity', {});
      return asObject(response.data);
    } catch (_err) {
      // Sync failure is non-fatal; auto-detect still runs against whatever is already synced
      return { synced: 0, skipped: 0, failed: 0, error: String(_err?.message || '') };
    }
  },

  async autoDetect() {
    // Ensure all company customers are registered in the identity service before detection
    await identityUnificationApi.syncCustomersToIdentity();
    const response = await api.post('/identity/auto-detect', {});
    return asObject(response.data);
  },

  async submitPublic(payload) {
    const response = await api.post('/unification/public', payload || {});
    return asObject(response.data);
  },
};

export {
  isTenantConfigUnavailableError,
  normalizeProfileSummary,
  normalizeProfileDetail,
};
