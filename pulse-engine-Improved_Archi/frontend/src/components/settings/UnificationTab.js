import { useCallback } from 'react';
import api from '@/lib/api';
import {
  Search, AlertTriangle, Check, X, ChevronRight, MessageSquare, Users,
} from 'lucide-react';
import { showToast } from '@/hooks/use-toast';
import { useSocket } from '@/lib/useSocket';

export default function UnificationTab({
  isAdmin,
  unificationLoading, runAutoDetect,
  unifiedProfiles, mergeSuggestions,
  acceptSuggestion, rejectSuggestion,
  selectedUnifiedProfile, setSelectedUnifiedProfile,
  suggestionDetail, setSuggestionDetail,
  splitFromProfile,
  onIdentityEvent,
}) {
  const handleSocketEvent = useCallback((eventName, data) => {
    if (['identity_merged', 'identity_split', 'identity_resolved'].includes(eventName)) {
      onIdentityEvent?.(eventName, data);
    }
  }, [onIdentityEvent]);

  useSocket(handleSocketEvent);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Cross-Platform Unification</h2>
          <p className="text-xs text-slate-400 mt-1">Merge customer profiles across WhatsApp, Facebook, Instagram, Email, and Web Chat into unified identities.</p>
        </div>
        <button onClick={runAutoDetect} disabled={unificationLoading}
          className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-medium hover:bg-indigo-500 disabled:opacity-50 transition-all shadow-sm">
          {unificationLoading ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Search size={14} />}
          Scan for Matches
        </button>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Unified Profiles</p>
          <p className="text-2xl font-bold text-slate-900 mt-1">{unifiedProfiles.length}</p>
        </div>
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Pending Suggestions</p>
          <p className="text-2xl font-bold text-amber-600 mt-1">{mergeSuggestions.length}</p>
        </div>
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Total Linked</p>
          <p className="text-2xl font-bold text-emerald-600 mt-1">{unifiedProfiles.reduce((sum, p) => sum + (p.member_count || 0), 0)}</p>
        </div>
      </div>

      {/* Merge Suggestions */}
      {mergeSuggestions.length > 0 && (
        <div className="bg-white border border-amber-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 flex items-center gap-2">
            <AlertTriangle size={14} className="text-amber-600" />
            <h3 className="text-sm font-semibold text-amber-800">Merge Suggestions ({mergeSuggestions.length})</h3>
            <span className="text-[10px] text-amber-600 ml-auto">Review and confirm cross-platform matches</span>
          </div>
          <div className="divide-y divide-slate-100">
            {mergeSuggestions.map((s) => {
              const score = parseFloat(s.match_score) || 0;
              const scoreColor = score >= 0.85 ? 'text-emerald-600 bg-emerald-50' : score >= 0.7 ? 'text-amber-600 bg-amber-50' : 'text-slate-500 bg-slate-50';
              return (
                <div key={s.id} className="px-4 py-3 hover:bg-slate-50 transition-colors">
                  <div className="flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-600 flex-shrink-0">
                          {(s.name_a || '?').charAt(0)}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-800 truncate">{s.name_a || 'Unknown'}</p>
                          <p className="text-[10px] text-slate-400 truncate">{s.email_a || s.phone_a || ''}</p>
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col items-center gap-1 flex-shrink-0">
                      <span className={`text-xs font-bold px-2 py-1 rounded-lg ${scoreColor}`}>{(score * 100).toFixed(0)}%</span>
                      <span className="text-[9px] text-slate-400 max-w-[120px] text-center truncate">{s.match_reasons}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center text-xs font-bold text-purple-600 flex-shrink-0">
                          {(s.name_b || '?').charAt(0)}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-800 truncate">{s.name_b || 'Unknown'}</p>
                          <p className="text-[10px] text-slate-400 truncate">{s.email_b || s.phone_b || ''}</p>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button
                        onClick={() => acceptSuggestion(s.id)}
                        disabled={unificationLoading}
                        className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-500 transition-colors disabled:opacity-50"
                      >
                        {unificationLoading ? <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Check size={13} />}
                        {unificationLoading ? 'Working...' : 'Accept'}
                      </button>
                      <button
                        onClick={() => rejectSuggestion(s.id)}
                        disabled={unificationLoading}
                        className="flex items-center gap-1.5 px-3 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs font-medium hover:bg-slate-200 transition-colors disabled:opacity-50"
                      >
                        {unificationLoading ? <div className="w-3.5 h-3.5 border-2 border-slate-500 border-t-transparent rounded-full animate-spin" /> : <X size={13} />}
                        {unificationLoading ? 'Working...' : 'Reject'}
                      </button>
                      <button onClick={() => setSuggestionDetail(s)} className="p-2 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600">
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
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-slate-900 mb-3">Manual Merge</h3>
          <p className="text-xs text-slate-400 mb-3">Use the dedicated Unification page to select readable profiles before merging.</p>
          <a
            href="/unification"
            className="inline-flex px-4 py-2 bg-indigo-600 text-white rounded-lg text-xs font-medium hover:bg-indigo-500"
          >
            Open Unification
          </a>
        </div>
      )}

      {/* Unified Profiles List */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-900">Unified Profiles ({unifiedProfiles.length})</h3>
        </div>
        {unifiedProfiles.length === 0 ? (
          <div className="p-8 text-center">
            <Users size={32} className="text-slate-300 mx-auto mb-3" />
            <p className="text-sm text-slate-500">No unified profiles yet</p>
            <p className="text-xs text-slate-400 mt-1">Run "Scan for Matches" to find cross-platform customers</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {unifiedProfiles.map((p) => (
              <button key={p.id} onClick={async () => {
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
              }} className="w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
                    {(p.display_name || '?').charAt(0)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-slate-800">{p.display_name || 'Unknown'}</p>
                    <p className="text-[10px] text-slate-400 truncate">{p.primary_email || p.primary_phone || ''} • {p.platforms_used || 'No platforms'}</p>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <div className="text-right">
                      <p className="text-xs font-semibold text-slate-700">{p.member_count} profiles</p>
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
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setSelectedUnifiedProfile(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="bg-gradient-to-r from-indigo-600 to-purple-600 px-6 py-4 flex items-center justify-between">
              <div>
                <h3 className="text-white font-bold text-lg">{selectedUnifiedProfile.display_name}</h3>
                <p className="text-indigo-200 text-xs">{selectedUnifiedProfile.platforms_used} • {selectedUnifiedProfile.total_interactions} interactions</p>
              </div>
              <button onClick={() => setSelectedUnifiedProfile(null)} className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white"><X size={16} /></button>
            </div>
            <div className="p-6 overflow-y-auto max-h-[60vh] space-y-6">
              <div>
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Linked Profiles ({selectedUnifiedProfile.members?.length || 0})</h4>
                <div className="space-y-2">
                  {(selectedUnifiedProfile.members || []).map((m) => (
                    <div key={m.customer_id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl">
                      <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-600">{(m.name || '?').charAt(0)}</div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-800">{m.name || 'Unknown'}</p>
                        <p className="text-[10px] text-slate-400">{m.email || m.phone || ''}</p>
                      </div>
                      {m.is_primary && <span className="text-[9px] font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">PRIMARY</span>}
                      <span className="text-[9px] text-slate-400">{m.match_method}</span>
                      {isAdmin && (
                        <button
                          onClick={() => splitFromProfile(selectedUnifiedProfile.id, m.mapping_id || m.customer_id)}
                          disabled={unificationLoading}
                          className="text-[10px] text-red-500 hover:text-red-700 disabled:opacity-50"
                        >
                          {unificationLoading ? 'Removing...' : 'Remove'}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Cross-Platform Conversations</h4>
                <div className="space-y-2">
                  {(selectedUnifiedProfile.all_conversations || []).slice(0, 10).map((c) => (
                    <div key={c.id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl">
                      <MessageSquare size={14} className="text-slate-400 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-slate-700 truncate">{c.last_message || 'No messages'}</p>
                        <p className="text-[10px] text-slate-400">{c.channel} • {c.source_customer}</p>
                      </div>
                      <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${c.status === 'open' ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>{c.status}</span>
                    </div>
                  ))}
                  {(selectedUnifiedProfile.all_conversations || []).length === 0 && (
                    <p className="text-xs text-slate-400 text-center py-4">No conversations found</p>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Suggestion Detail Modal */}
      {suggestionDetail && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setSuggestionDetail(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-bold text-slate-900">Match Details</h3>
              <button onClick={() => setSuggestionDetail(null)} className="p-1.5 rounded-lg hover:bg-slate-100"><X size={16} className="text-slate-400" /></button>
            </div>
            <div className="p-6 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="p-4 bg-blue-50 rounded-xl">
                  <p className="text-[10px] text-blue-600 font-semibold uppercase tracking-wider mb-2">Customer A</p>
                  <p className="text-sm font-bold text-slate-800">{suggestionDetail.name_a}</p>
                  <p className="text-xs text-slate-500 mt-1">Email: {suggestionDetail.email_a || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Phone: {suggestionDetail.phone_a || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Company: {suggestionDetail.company_a || 'N/A'}</p>
                </div>
                <div className="p-4 bg-purple-50 rounded-xl">
                  <p className="text-[10px] text-purple-600 font-semibold uppercase tracking-wider mb-2">Customer B</p>
                  <p className="text-sm font-bold text-slate-800">{suggestionDetail.name_b}</p>
                  <p className="text-xs text-slate-500 mt-1">Email: {suggestionDetail.email_b || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Phone: {suggestionDetail.phone_b || 'N/A'}</p>
                  <p className="text-xs text-slate-500">Company: {suggestionDetail.company_b || 'N/A'}</p>
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-xl">
                <p className="text-[10px] text-slate-400 font-semibold uppercase tracking-wider mb-1">Match Score &amp; Reasons</p>
                <div className="flex items-center gap-2">
                  <span className="text-lg font-bold text-indigo-600">{((parseFloat(suggestionDetail.match_score) || 0) * 100).toFixed(0)}%</span>
                  <span className="text-xs text-slate-500">{suggestionDetail.match_reasons}</span>
                </div>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => { acceptSuggestion(suggestionDetail.id); setSuggestionDetail(null); }}
                  disabled={unificationLoading}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-emerald-600 text-white rounded-xl text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
                >
                  {unificationLoading ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <Check size={16} />}
                  {unificationLoading ? 'Working...' : 'Confirm - Same Person'}
                </button>
                <button
                  onClick={() => { rejectSuggestion(suggestionDetail.id); setSuggestionDetail(null); }}
                  disabled={unificationLoading}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-slate-100 text-slate-600 rounded-xl text-sm font-semibold hover:bg-slate-200 disabled:opacity-50"
                >
                  {unificationLoading ? <div className="w-4 h-4 border-2 border-slate-500 border-t-transparent rounded-full animate-spin" /> : <X size={16} />}
                  {unificationLoading ? 'Working...' : 'Reject - Different People'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
