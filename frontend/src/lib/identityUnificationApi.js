import api from '@/lib/api';

const normalizeRole = (value) => String(value || '').trim().toLowerCase();

const normalizeTenantContext = (tenantContext = {}) => {
  const tenantId = String(tenantContext.tenantId || '').trim();
  const apiKey = String(tenantContext.apiKey || '').trim();
  const userRole = normalizeRole(tenantContext.userRole) || 'company_agent';

  if (!tenantId) {
    throw new Error('Tenant ID is required for identity unification requests.');
  }
  if (!apiKey) {
    throw new Error('API key is required for identity unification requests.');
  }

  return { tenantId, apiKey, userRole };
};

const identityHeaders = (tenantContext = {}) => {
  const { tenantId, apiKey, userRole } = normalizeTenantContext(tenantContext);
  return {
    'X-Tenant-ID': tenantId,
    'X-API-Key': apiKey,
    'X-User-Role': userRole,
  };
};

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
    platforms_used: String(item.platforms_used || '').trim(),
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

export const identityUnificationApi = {
  async listProfiles(tenantContext) {
    const response = await api.get('/identity/profiles', {
      headers: identityHeaders(tenantContext),
    });
    return asArray(response.data).map((item) => normalizeProfileSummary(item));
  },

  async getProfileDetail(profileId, tenantContext) {
    const safeProfileId = String(profileId || '').trim();
    if (!safeProfileId) throw new Error('Profile ID is required.');

    const headers = identityHeaders(tenantContext);
    const [summaryResult, rawResult] = await Promise.allSettled([
      api.get(`/identity/profiles/${encodeURIComponent(safeProfileId)}`, { headers }),
      api.get(`/v1/identity/${encodeURIComponent(safeProfileId)}`, { headers }),
    ]);

    if (summaryResult.status === 'rejected') {
      throw summaryResult.reason;
    }

    const summary = summaryResult.value?.data || {};
    const raw = rawResult.status === 'fulfilled' ? rawResult.value?.data || {} : {};
    return normalizeProfileDetail(summary, raw);
  },

  async resolve(payload, tenantContext) {
    const response = await api.post('/identity/resolve', payload, {
      headers: identityHeaders(tenantContext),
    });
    const data = asObject(response.data);
    return {
      ...data,
      profile: normalizeProfileSummary(data.profile || {}),
    };
  },

  async unify(payload, tenantContext) {
    const response = await api.post('/identity/unify', payload, {
      headers: identityHeaders(tenantContext),
    });
    const data = asObject(response.data);
    return {
      ...data,
      profile: normalizeProfileSummary(data.profile || {}),
    };
  },

  async merge(customerIds, tenantContext, mergeReason = 'manual_merge') {
    const normalizedIds = asArray(customerIds)
      .map((item) => String(item || '').trim())
      .filter(Boolean);

    const response = await api.post(
      '/identity/merge',
      {
        customer_ids: normalizedIds,
        merge_reason: mergeReason,
      },
      {
        headers: identityHeaders(tenantContext),
      },
    );

    return normalizeProfileSummary(response.data || {});
  },

  async split({ profileId, mappingIds = [], fingerprintIds = [], splitReason = 'manual_split' }, tenantContext) {
    const safeProfileId = String(profileId || '').trim();
    if (!safeProfileId) throw new Error('Profile ID is required for split.');

    const payload = {
      profile_id: safeProfileId,
      customer_id: safeProfileId,
      mapping_ids: asArray(mappingIds)
        .map((item) => String(item || '').trim())
        .filter(Boolean),
      fingerprint_ids: asArray(fingerprintIds)
        .map((item) => String(item || '').trim())
        .filter(Boolean),
      split_reason: String(splitReason || 'manual_split').trim() || 'manual_split',
    };

    const response = await api.post('/identity/split', payload, {
      headers: identityHeaders(tenantContext),
    });

    return asObject(response.data);
  },

  async getByCustomer(customerId, tenantContext) {
    const safeCustomerId = String(customerId || '').trim();
    if (!safeCustomerId) throw new Error('Customer ID is required.');

    const response = await api.get(`/identity/customer/${encodeURIComponent(safeCustomerId)}`, {
      headers: identityHeaders(tenantContext),
    });
    const data = asObject(response.data);
    return {
      ...data,
      profile: data.profile ? normalizeProfileSummary(data.profile) : null,
    };
  },

  async listSuggestions(tenantContext) {
    const response = await api.get('/identity/suggestions', {
      headers: identityHeaders(tenantContext),
    });
    return asArray(response.data);
  },

  async resolveSuggestion(suggestionId, action, notes, tenantContext) {
    const safeSuggestionId = String(suggestionId || '').trim();
    if (!safeSuggestionId) throw new Error('Suggestion ID is required.');

    const response = await api.post(
      `/identity/suggestions/${encodeURIComponent(safeSuggestionId)}/resolve`,
      {
        action: String(action || 'accept').trim(),
        notes: notes ? String(notes) : null,
      },
      {
        headers: identityHeaders(tenantContext),
      },
    );
    return asObject(response.data);
  },

  async autoDetect(tenantContext) {
    const response = await api.post('/identity/auto-detect', {}, {
      headers: identityHeaders(tenantContext),
    });
    return asObject(response.data);
  },

  async submitPublic(payload) {
    const response = await api.post('/unification/public', payload || {});
    return asObject(response.data);
  },
};

export {
  identityHeaders,
  isTenantConfigUnavailableError,
  normalizeProfileSummary,
  normalizeProfileDetail,
};
