import { useCallback, useState } from 'react';
import api from '@/lib/api';
import {
  Search, AlertTriangle, Check, X, ChevronRight, MessageSquare, Users,
  GitMerge, Split, Info,
} from 'lucide-react';
import { showToast } from '@/hooks/use-toast';
import { useSocket } from '@/lib/useSocket';

export default function UnificationTab({
  isAdmin,
  unificationLoading, runAutoDetect,
  unifiedProfiles, mergeSuggestions,
  acceptSuggestion, rejectSuggestion,
  manualMergeIds, setManualMergeIds, manualMerge,
  selectedUnifiedProfile, setSelectedUnifiedProfile,
  suggestionDetail, setSuggestionDetail,
  splitFromProfile,
  onIdentityEvent,
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  const handleSocketEvent = useCallback((eventName, data) => {
    if (['identity_merged', 'identity_split', 'identity_resolved'].includes(eventName)) {
      onIdentityEvent?.(eventName, data);
    }
  }, [onIdentityEvent]);

  useSocket(handleSocketEvent);

  const searchCustomers = useCallback(async (q) => {
    if (!q || q.trim().length < 2) { setSearchResults([]); return; }
    setSearching(true);
    try {
      const res = await api.get(`/customers/search?q=${encodeURIComponent(q.trim())}&limit=10`);
      setSearchResults(Array.isArray(res.data) ? res.data : []);
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }, []);

  const selectForMerge = (customerId) => {
    if (!manualMergeIds || !setManualMergeIds) return;
    if (manualMergeIds.includes(customerId)) return;
    const empty = manualMergeIds.findIndex((id) => !id);
    if (empty !== -1) {
      const next = [...manualMergeIds];
      next[empty] = customerId;
      setManualMergeIds(next);
    } else {
      setManualMergeIds([...manualMergeIds, customerId]);
    }
    setSearchQuery('');
    setSearchResults([]);
  };

  const removeFromMerge = (idx) => {
    if (!setManualMergeIds) return;
    const next = [...(manualMergeIds || [])];
    next.splice(idx, 1);
    if (next.length < 2) next.push('');
    setManualMergeIds(next.slice(0, Math.max(2, next.filter(Boolean).length + 1)));
  };

  const selectedCount = (manualMergeIds || []).filter(Boolean).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Cross-Platform Identity Unification</h2>
          <p className="text-xs text-slate-400 mt-1">
            Detect and merge customer profiles scattered across WhatsApp, Facebook, Instagram, Email, and Web Chat into unified identities.
          </p>
        </div>
        <button
          onClick={runAutoDetect}
          disabled={unificationLoading}
          className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-medium hover:bg-indigo-500 disabled:opacity-50 transition-all shadow-sm"
        >
          {unificationLoading
            ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            : <Search size={14} />}
          Scan for Matches
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Unified Profiles</p>
          <p className="text-2xl font-bold text-slate-900 mt-1">{unifiedProfiles.length}</p>
          <p className="text-[10px] text-slate-400 mt-1">merged identities</p>
        </div>
        <div className="bg-white border border-amber-200 rounded-xl p-4">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Pending Suggestions</p>
          <p className="text-2xl font-bold text-amber-600 mt-1">{mergeSuggestions.length}</p>
          <p className="text-[10px] text-slate-400 mt-1">awaiting review</p>
        </div>
        <div className="bg-white border border-emerald-200 rounded-xl p-4">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Total Linked</p>
          <p className="text-2xl font-bold text-emerald-600 mt-1">
            {unifiedProfiles.reduce((sum, p) => sum + (p.member_count || 0), 0)}
          </p>
          <p className="text-[10px] text-slate-400 mt-1">customer profiles</p>
        </div>
      </div>

      {/* Merge Suggestions */}
      {mergeSuggestions.length > 0 && (
        <div className="bg-white border border-amber-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 flex items-center gap-2">
            <AlertTriangle size={14} className="text-amber-600" />
            <h3 className="text-sm font-semibold text-amber-800">
              Merge Suggestions ({mergeSuggestions.length})
            </h3>
            <span className="text-[10px] text-amber-600 ml-auto">Review cross-platform matches</span>
          </div>
          <div className="divide-y divide-slate-100">
            {mergeSuggestions.map((s) => {
              const score = parseFloat(s.match_score) || 0;
              const scoreColor =
                score >= 0.85
                  ? 'text-emerald-600 bg-emerald-50 border-emerald-200'
                  : score >= 0.7
                  ? 'text-amber-600 bg-amber-50 border-amber-200'
                  : 'text-slate-500 bg-slate-50 border-slate-200';
              return (
                <div key={s.id} className="px-4 py-3 hover:bg-slate-50 transition-colors">
                  <div className="flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-600 flex-shrink-0">
                          {(s.name_a || '?').charAt(0).toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-800 truncate">{s.name_a || 'Unknown'}</p>
                          <p className="text-[10px] text-slate-400 truncate">{s.email_a || s.phone_a || 'No contact'}</p>
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col items-center gap-1 flex-shrink-0">
                      <span className={`text-xs font-bold px-2 py-1 rounded-lg border ${scoreColor}`}>
                        {(score * 100).toFixed(0)}%
                      </span>
                      <span className="text-[9px] text-slate-400 max-w-[100px] text-center truncate">
                        {s.match_reasons}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center text-xs font-bold text-purple-600 flex-shrink-0">
                          {(s.name_b || '?').charAt(0).toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-800 truncate">{s.name_b || 'Unknown'}</p>
                          <p className="text-[10px] text-slate-400 truncate">{s.email_b || s.phone_b || 'No contact'}</p>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button
                        onClick={() => acceptSuggestion(s.id)}
                        disabled={unificationLoading}
                        className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-500 transition-colors disabled:opacity-50"
                      >
                        {unificationLoading
                          ? <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                          : <Check size={13} />}
                        {unificationLoading ? 'Working...' : 'Accept'}
                      </button>
                      <button
                        onClick={() => rejectSuggestion(s.id)}
                        disabled={unificationLoading}
                        className="flex items-center gap-1.5 px-3 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs font-medium hover:bg-slate-200 transition-colors disabled:opacity-50"
                      >
                        {unificationLoading
                          ? <div className="w-3.5 h-3.5 border-2 border-slate-500 border-t-transparent rounded-full animate-spin" />
                          : <X size={13} />}
                        {unificationLoading ? 'Working...' : 'Reject'}
                      </button>
                      <button
                        onClick={() => setSuggestionDetail(s)}
                        className="p-2 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600"
                        title="View details"
                      >
                        <ChevronRight size={14} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Manual Merge */}
      {isAdmin && (
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
            <GitMerge size={15} className="text-indigo-500" />
            <h3 className="text-sm font-semibold text-slate-900">Manual Merge</h3>
            <span className="ml-auto text-[10px] text-slate-400">Search and select 2+ customers to merge</span>
          </div>
          <div className="p-4 space-y-4">
            {/* Search input */}
            <div className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  searchCustomers(e.target.value);
                }}
                placeholder="Search customers by name, email, or phone…"
                className="w-full pl-8 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-200"
              />
              {searching && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
              )}
              {/* Dropdown */}
              {searchResults.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-lg z-10 overflow-hidden max-h-52 overflow-y-auto">
                  {searchResults.map((c) => {
                    const alreadySelected = (manualMergeIds || []).includes(c.id);
                    return (
                      <button
                        key={c.id}
                        onClick={() => selectForMerge(c.id)}
                        disabled={alreadySelected}
                        className={`w-full text-left px-3 py-2.5 text-sm hover:bg-slate-50 transition-colors flex items-center gap-3 ${alreadySelected ? 'opacity-40 cursor-not-allowed' : ''}`}
                      >
                        <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-xs font-bold text-indigo-600 flex-shrink-0">
                          {(c.name || '?').charAt(0).toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium text-slate-800 truncate">{c.name || 'Unknown'}</p>
                          <p className="text-[10px] text-slate-400 truncate">{c.email || c.phone || ''}</p>
                        </div>
                        {alreadySelected && <span className="ml-auto text-[9px] text-indigo-500 font-semibold">Added</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Selected customers chips */}
            {(manualMergeIds || []).filter(Boolean).length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs text-slate-500 font-medium">Selected for merge ({selectedCount}):</p>
                <div className="flex flex-wrap gap-2">
                  {(manualMergeIds || []).filter(Boolean).map((id, idx) => (
                    <div key={id} className="flex items-center gap-2 px-2.5 py-1.5 bg-indigo-50 border border-indigo-200 rounded-lg text-xs">
                      <div className="w-4 h-4 rounded-full bg-indigo-500 flex items-center justify-center text-white text-[8px] font-bold flex-shrink-0">
                        {idx + 1}
                      </div>
                      <span className="font-mono text-indigo-700 max-w-[120px] truncate">{id}</span>
                      <button
                        onClick={() => removeFromMerge(idx)}
                        className="text-indigo-400 hover:text-indigo-700 ml-1"
                      >
                        <X size={11} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2 p-3 bg-slate-50 rounded-lg border border-dashed border-slate-200">
                <Info size={13} className="text-slate-400 flex-shrink-0" />
                <p className="text-xs text-slate-400">Search and select at least 2 customers above to merge their profiles.</p>
              </div>
            )}

            <button
              onClick={manualMerge}
              disabled={unificationLoading || selectedCount < 2}
              className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-medium hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
            >
              {unificationLoading
                ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                : <GitMerge size={15} />}
              {unificationLoading ? 'Merging…' : `Merge ${selectedCount >= 2 ? selectedCount : ''} Profiles`}
            </button>
          </div>
        </div>
      )}

      {/* Unified Profiles List */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-900">
            Unified Profiles ({unifiedProfiles.length})
          </h3>
          {unifiedProfiles.length > 0 && (
            <span className="text-[10px] text-slate-400">Click a profile to view linked accounts</span>
          )}
        </div>
        {unifiedProfiles.length === 0 ? (
          <div className="p-10 text-center">
            <Users size={36} className="text-slate-200 mx-auto mb-3" />
            <p className="text-sm font-medium text-slate-500">No unified profiles yet</p>
            <p className="text-xs text-slate-400 mt-1">
              Click <span className="font-medium text-indigo-600">Scan for Matches</span> to detect customers with the same identity across channels.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {unifiedProfiles.map((p) => (
              <button
                key={p.id}
                onClick={async () => {
                  try {
                    const r = await api.get(`/identity/profiles/${p.id}`);
                    setSelectedUnifiedProfile(r.data);
                  } catch {
                    showToast({
                      type: 'error',
                      title: 'Profile Unavailable',
                      message: 'That unified profile could not be loaded right now.',
                    });
                  }
                }}
                className="w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
                    {(p.display_name || '?').charAt(0).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-slate-800">{p.display_name || 'Unknown'}</p>
                    <p className="text-[10px] text-slate-400 truncate">
                      {p.primary_email || p.primary_phone || '—'}
                      {p.platforms_used ? ` · ${p.platforms_used}` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <div className="text-right">
                      <p className="text-xs font-semibold text-slate-700">{p.member_count} linked</p>
                      <p className="text-[10px] text-slate-400">${parseFloat(p.lifetime_value || 0).toFixed(2)} LTV</p>
                    </div>
                    <ChevronRight size={14} className="text-slate-400" />
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Unified Profile Detail Modal */}
      {selectedUnifiedProfile && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          onClick={() => setSelectedUnifiedProfile(null)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="bg-gradient-to-r from-indigo-600 to-purple-600 px-6 py-4 flex items-center justify-between flex-shrink-0">
              <div>
                <h3 className="text-white font-bold text-lg">{selectedUnifiedProfile.display_name}</h3>
                <p className="text-indigo-200 text-xs">
                  {selectedUnifiedProfile.platforms_used}
                  {selectedUnifiedProfile.total_interactions != null
                    ? ` · ${selectedUnifiedProfile.total_interactions} interactions`
                    : ''}
                </p>
              </div>
              <button
                onClick={() => setSelectedUnifiedProfile(null)}
                className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white"
              >
                <X size={16} />
              </button>
            </div>

            <div className="p-6 overflow-y-auto flex-1 space-y-6">
              {/* Linked profiles */}
              <div>
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
                  Linked Profiles ({selectedUnifiedProfile.members?.length || 0})
                </h4>
                <div className="space-y-2">
                  {(selectedUnifiedProfile.members || []).map((m) => (
                    <div key={m.customer_id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl border border-slate-100">
                      <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-600 flex-shrink-0">
                        {(m.name || '?').charAt(0).toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-800">{m.name || 'Unknown'}</p>
                        <p className="text-[10px] text-slate-400">{m.email || m.phone || '—'}</p>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {m.is_primary && (
                          <span className="text-[9px] font-bold text-indigo-600 bg-indigo-50 border border-indigo-100 px-2 py-0.5 rounded-full">
                            PRIMARY
                          </span>
                        )}
                        {m.match_method && (
                          <span className="text-[9px] text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">
                            {m.match_method}
                          </span>
                        )}
                        {isAdmin && (
                          <button
                            onClick={() => splitFromProfile(selectedUnifiedProfile.id, m.mapping_id || m.customer_id)}
                            disabled={unificationLoading}
                            className="flex items-center gap-1 text-[10px] text-red-500 hover:text-red-700 disabled:opacity-50 px-2 py-1 rounded hover:bg-red-50"
                            title="Remove from unified profile"
                          >
                            <Split size={11} />
                            {unificationLoading ? 'Removing…' : 'Remove'}
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Conversations */}
              <div>
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
                  Cross-Platform Conversations
                </h4>
                <div className="space-y-2">
                  {(selectedUnifiedProfile.all_conversations || []).slice(0, 10).map((c) => (
                    <div key={c.id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl border border-slate-100">
                      <MessageSquare size={14} className="text-slate-400 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-slate-700 truncate">{c.last_message || 'No messages'}</p>
                        <p className="text-[10px] text-slate-400">{c.channel} · {c.source_customer}</p>
                      </div>
                      <span
                        className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${
                          c.status === 'open' ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'
                        }`}
                      >
                        {c.status}
                      </span>
                    </div>
                  ))}
                  {(selectedUnifiedProfile.all_conversations || []).length === 0 && (
                    <p className="text-xs text-slate-400 text-center py-6">No conversations linked to this unified profile.</p>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Suggestion Detail Modal */}
      {suggestionDetail && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          onClick={() => setSuggestionDetail(null)}
        >
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-bold text-slate-900">Match Details</h3>
              <button onClick={() => setSuggestionDetail(null)} className="p-1.5 rounded-lg hover:bg-slate-100">
                <X size={16} className="text-slate-400" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="p-4 bg-blue-50 rounded-xl border border-blue-100">
                  <p className="text-[10px] text-blue-600 font-semibold uppercase tracking-wider mb-2">Customer A</p>
                  <p className="text-sm font-bold text-slate-800">{suggestionDetail.name_a || '—'}</p>
                  <p className="text-xs text-slate-500 mt-1">Email: {suggestionDetail.email_a || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Phone: {suggestionDetail.phone_a || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Company: {suggestionDetail.company_a || 'N/A'}</p>
                </div>
                <div className="p-4 bg-purple-50 rounded-xl border border-purple-100">
                  <p className="text-[10px] text-purple-600 font-semibold uppercase tracking-wider mb-2">Customer B</p>
                  <p className="text-sm font-bold text-slate-800">{suggestionDetail.name_b || '—'}</p>
                  <p className="text-xs text-slate-500 mt-1">Email: {suggestionDetail.email_b || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Phone: {suggestionDetail.phone_b || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Company: {suggestionDetail.company_b || 'N/A'}</p>
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
                <p className="text-[10px] text-slate-400 font-semibold uppercase tracking-wider mb-2">Match Score &amp; Reasons</p>
                <div className="flex items-center gap-3">
                  <span className="text-2xl font-bold text-indigo-600">
                    {((parseFloat(suggestionDetail.match_score) || 0) * 100).toFixed(0)}%
                  </span>
                  <span className="text-xs text-slate-500">{suggestionDetail.match_reasons}</span>
                </div>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => { acceptSuggestion(suggestionDetail.id); setSuggestionDetail(null); }}
                  disabled={unificationLoading}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-emerald-600 text-white rounded-xl text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
                >
                  {unificationLoading
                    ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    : <Check size={16} />}
                  {unificationLoading ? 'Working…' : 'Confirm — Same Person'}
                </button>
                <button
                  onClick={() => { rejectSuggestion(suggestionDetail.id); setSuggestionDetail(null); }}
                  disabled={unificationLoading}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-slate-100 text-slate-600 rounded-xl text-sm font-semibold hover:bg-slate-200 disabled:opacity-50"
                >
                  {unificationLoading
                    ? <div className="w-4 h-4 border-2 border-slate-500 border-t-transparent rounded-full animate-spin" />
                    : <X size={16} />}
                  {unificationLoading ? 'Working…' : 'Reject — Different People'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
