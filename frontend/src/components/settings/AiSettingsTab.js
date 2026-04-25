import { useState, useEffect, useCallback, useMemo } from 'react';
import api from '@/lib/api';
import {
  Save, Plus, Trash2, Edit, Bot, Wand2, RefreshCw, Activity,
} from 'lucide-react';

const LLM_MODELS = {
  gemini: ['gemini-3-pro-preview', 'gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-pro', 'gemini-1.5-flash'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo', 'o1', 'o1-mini', 'o3-mini'],
  anthropic: ['claude-3-7-sonnet-20250219', 'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229', 'claude-3-haiku-20240307'],
  custom: [],
};

const MODEL_MAX_TOKENS = {
  'gemini-3-pro-preview': 65536,
  'gemini-2.5-pro': 65536, 'gemini-2.5-flash': 65536, 'gemini-2.5-flash-lite': 65536,
  'gemini-2.0-flash': 8192, 'gemini-2.0-flash-lite': 8192,
  'gemini-1.5-pro': 8192, 'gemini-1.5-flash': 8192,
  'gpt-4o': 16384, 'gpt-4o-mini': 16384,
  'gpt-4-turbo': 4096, 'gpt-4': 8192, 'gpt-3.5-turbo': 4096,
  'o1': 32768, 'o1-mini': 65536, 'o3-mini': 100000,
  'claude-3-7-sonnet-20250219': 8192, 'claude-3-5-sonnet-20241022': 8192,
  'claude-3-5-haiku-20241022': 8192, 'claude-3-opus-20240229': 4096, 'claude-3-haiku-20240307': 4096,
};

const AGENT_RUNTIME_PROFILES = {
  support: {
    title: 'Support responder',
    route: 'Handles complaints, support questions, refunds, and escalation-prone messages.',
    behavior: 'Replies in the inbox and connected channels, then hands off if confidence or safety drops.',
  },
  sales: {
    title: 'Sales assistant',
    route: 'Handles product, pricing, availability, and purchase-intent conversations.',
    behavior: 'Qualifies the customer, recommends relevant products, and moves toward a clear next step.',
  },
  onboarding: {
    title: 'Onboarding guide',
    route: 'Handles setup, activation, implementation, and first-run questions.',
    behavior: 'Breaks work into simple steps and keeps the user moving without waiting for a human.',
  },
  generic: {
    title: 'General assistant',
    route: 'Covers messages that do not match a specialized active agent.',
    behavior: 'Responds with a flexible assistant style and uses the selected live model.',
  },
};

function agentRuntimeProfile(type) {
  const key = String(type || 'generic').toLowerCase();
  return AGENT_RUNTIME_PROFILES[key] || AGENT_RUNTIME_PROFILES.generic;
}

export default function AiSettingsTab({
  company, setCompany, saveCompany,
  saving, setSaving,
  llmEngines, selectedLlmEngine, refreshAiConfig,
  aiAgents, setAiAgents,
  editingLlmId, setEditingLlmId, llmDraft, setLlmDraft,
  showAddLlmForm, setShowAddLlmForm, addLlmForm, setAddLlmForm,
  editingAgentId, setEditingAgentId, agentDraft, setAgentDraft,
  showAddAgentForm, setShowAddAgentForm, addAgentForm, setAddAgentForm,
  isAdmin,
}) {
  const [orchExecutions, setOrchExecutions] = useState([]);
  const [orchLoading, setOrchLoading] = useState(true);
  const [orchErr, setOrchErr] = useState('');
  const [mcpList, setMcpList] = useState([]);
  const [aiSessions, setAiSessions] = useState([]);

  const loadExecutions = useCallback(async () => {
    try {
      setOrchErr('');
      const res = await api.get('/orchestrator/executions?limit=30');
      setOrchExecutions(res.data?.executions || []);
    } catch (e) {
      const d = e.response?.data;
      const msg = typeof d?.detail === 'string' ? d.detail : (d?.error || e.message || 'Failed to load');
      setOrchErr(msg);
    } finally {
      setOrchLoading(false);
    }
  }, []);

  const loadAiSessions = useCallback(async () => {
    try {
      const res = await api.get('/ai/sessions?limit=50');
      setAiSessions(Array.isArray(res.data) ? res.data : []);
    } catch {
      setAiSessions([]);
    }
  }, []);

  useEffect(() => {
    loadExecutions();
    loadAiSessions();
    const id = setInterval(() => {
      loadExecutions();
      loadAiSessions();
    }, 10000);
    return () => clearInterval(id);
  }, [loadAiSessions, loadExecutions]);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.get('/mcp/servers');
        setMcpList(Array.isArray(r.data) ? r.data : []);
      } catch {
        setMcpList([]);
      }
    })();
  }, []);

  const mcpSelectOptions = useMemo(
    () => mcpList.map((s) => ({ id: s.id, label: `${(s.endpoint || s.id).slice(0, 64)}${s.status && s.status !== 'active' ? ` (${s.status})` : ''}` })),
    [mcpList],
  );
  const activeAgents = useMemo(() => aiAgents.filter((agent) => agent.is_active), [aiAgents]);
  const latestSessionByAgent = useMemo(() => {
    const byAgent = {};
    for (const session of aiSessions) {
      const agentId = String(session.agent_id || '');
      if (!agentId || byAgent[agentId]) continue;
      byAgent[agentId] = session;
    }
    return byAgent;
  }, [aiSessions]);

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-slate-900">AI Configuration</h2>

      {/* Core AI Settings */}
      <div className="bg-white border border-slate-100 rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-slate-900">Enable AI Responses</h3>
            <p className="text-xs text-slate-400">Allow AI to automatically respond to customer messages</p>
          </div>
          <button
            onClick={() => { const enabling = !company.ai_enabled; setCompany({ ...company, ai_enabled: enabling, auto_assign: !enabling }); }}
            className={`relative w-11 h-6 rounded-full transition-colors ${company.ai_enabled ? 'bg-blue-600' : 'bg-gray-300'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow ${company.ai_enabled ? 'translate-x-5' : ''}`} />
          </button>
        </div>

        <div>
          <label className="text-xs text-slate-400 mb-2 block">AI Confidence Threshold: {((company.ai_confidence_threshold || 0.7) * 100).toFixed(0)}%</label>
          <input type="range" min="0.1" max="1" step="0.05" value={company.ai_confidence_threshold || 0.7}
            onChange={(e) => setCompany({ ...company, ai_confidence_threshold: parseFloat(e.target.value) })}
            className="w-full accent-violet-600" />
          <div className="flex justify-between text-[10px] text-slate-300"><span>More AI responses</span><span>More human handoff</span></div>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-slate-900">Auto-assign Conversations</h3>
            <p className="text-xs text-slate-400">Automatically assign escalated conversations to available agents</p>
          </div>
          <button
            onClick={() => setCompany({ ...company, auto_assign: !company.auto_assign })}
            className={`relative w-11 h-6 rounded-full transition-colors ${company.auto_assign ? 'bg-blue-600' : 'bg-gray-300'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow ${company.auto_assign ? 'translate-x-5' : ''}`} />
          </button>
        </div>

        {!company.ai_enabled && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
            <span className="text-amber-500 mt-0.5 flex-shrink-0 text-sm">⚠</span>
            <p className="text-xs text-amber-700"><b>AI Engine is off</b> — Auto-assign is enabled. Incoming conversations will be routed to available team agents.</p>
          </div>
        )}

        <button onClick={saveCompany} disabled={saving === 'company'}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50">
          <Save size={14} /> Save
        </button>
      </div>

      {/* System Status Log */}
      <div className="bg-slate-900 rounded-xl p-4 font-mono text-xs space-y-1.5">
        <p className="text-slate-500 mb-2 text-[10px] uppercase tracking-widest">System Status</p>
        <p><span className="text-slate-500">ai_engine </span><span className={company.ai_enabled ? 'text-emerald-400' : 'text-red-400'}>{company.ai_enabled ? '● enabled' : '○ disabled'}</span></p>
        <p><span className="text-slate-500">auto_assign </span><span className={company.auto_assign ? 'text-emerald-400' : 'text-slate-400'}>{company.auto_assign ? '● on' : '○ off'}</span></p>
        <p><span className="text-slate-500">confidence  </span><span className="text-violet-300">{((company.ai_confidence_threshold || 0.7) * 100).toFixed(0)}%</span></p>
        <p>
          <span className="text-slate-500">llm_engines </span>
          <span className="text-blue-300">{llmEngines.length} total</span>
          <span className="text-slate-600"> / </span>
          <span className="text-emerald-400">{selectedLlmEngine ? '1 selected live' : '0 selected live'}</span>
        </p>
        {selectedLlmEngine ? (
          <p className="pl-4">
            <span className="text-slate-600">└ </span>
            <span className="text-amber-300">{selectedLlmEngine.provider ? selectedLlmEngine.provider.charAt(0).toUpperCase() + selectedLlmEngine.provider.slice(1) : '?'} — {selectedLlmEngine.model_name || 'unknown'}</span>
            <span className="text-slate-600"> (temp: {selectedLlmEngine.temperature ?? 0.7}, max_tokens: {selectedLlmEngine.max_tokens ?? 2048})</span>
          </p>
        ) : (
          <p className="pl-4 text-slate-500">No live engine selected yet.</p>
        )}
        <p>
          <span className="text-slate-500">ai_agents   </span>
          <span className="text-blue-300">{aiAgents.length} total</span>
          <span className="text-slate-600"> / </span>
          <span className="text-emerald-400">{aiAgents.filter(a => a.is_active).length} active</span>
        </p>
        {aiAgents.filter(a => a.is_active).map(a => (
          <p key={a.id} className="pl-4">
            <span className="text-slate-600">└ </span>
            <span className="text-cyan-300 capitalize">{a.agent_type || 'support'}</span>
            <span className="text-slate-600"> ({a.provider || '?'} / {a.model_name || 'default'})</span>
          </p>
        ))}
      </div>

      <div className="bg-white border border-slate-100 rounded-xl p-6">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
              <Bot size={14} className="text-emerald-500" /> Live agent behavior
            </h3>
            <p className="text-[10px] text-slate-400 mt-0.5">
              Active agents are used for real-time replies in inbox and channel webhooks. This panel refreshes with the latest AI response sessions.
            </p>
          </div>
          <button
            type="button"
            onClick={loadAiSessions}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs text-slate-600 bg-slate-100 rounded-lg hover:bg-slate-200"
          >
            <RefreshCw size={12} /> Refresh replies
          </button>
        </div>
        {!company.ai_enabled && activeAgents.length > 0 && (
          <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            Active agents are configured. Enabling an agent turns AI responses on server-side, but save or refresh this page if this switch still appears off locally.
          </div>
        )}
        {activeAgents.length === 0 ? (
          <p className="text-xs text-slate-400 py-3">No active AI agents yet. Turn one on and incoming customer messages will route to it automatically.</p>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {activeAgents.map((agent) => {
              const profile = agentRuntimeProfile(agent.agent_type);
              const session = latestSessionByAgent[agent.id];
              return (
                <div key={agent.id} className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 capitalize">{agent.agent_type || 'generic'} Agent</p>
                      <p className="text-[11px] text-emerald-700 font-medium mt-0.5">{profile.title}</p>
                    </div>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium">Live</span>
                  </div>
                  <p className="text-xs text-slate-500 mt-3">{profile.route}</p>
                  <p className="text-xs text-slate-500 mt-1">{profile.behavior}</p>
                  <div className="mt-3 rounded-lg bg-white border border-slate-100 p-3">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Latest live response</p>
                    {session ? (
                      <>
                        <p className="text-xs text-slate-700 mt-1 line-clamp-3">{session.response || 'Response saved without body preview.'}</p>
                        <p className="text-[10px] text-slate-400 mt-1">
                          {session.source || 'ai'} | {session.confidence != null ? `${Math.round(Number(session.confidence) * 100)}% confidence` : 'confidence n/a'} | {(session.created_at || '').replace('T', ' ').slice(0, 19)}
                        </p>
                      </>
                    ) : (
                      <p className="text-xs text-slate-400 mt-1">Waiting for the next incoming message to show this agent responding live.</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Orchestrator: rule-based agent / workflow activity */}
      <div className="bg-white border border-slate-100 rounded-xl p-6">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
              <Activity size={14} className="text-cyan-600" /> Recent agent / workflow activity
            </h3>
            <p className="text-[10px] text-slate-400 mt-0.5">Steps from the rule-based orchestrator (Capture, Qualification, Support, …). Refreshes every 20s.</p>
          </div>
          <button
            type="button"
            onClick={() => { setOrchLoading(true); loadExecutions(); }}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs text-slate-600 bg-slate-100 rounded-lg hover:bg-slate-200"
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>
        {orchErr && <p className="text-xs text-amber-700 mb-2">{orchErr}</p>}
        {orchLoading && orchExecutions.length === 0 ? (
          <p className="text-xs text-slate-400">Loading…</p>
        ) : orchExecutions.length === 0 ? (
          <p className="text-xs text-slate-400">No workflow steps yet. They appear when messages or leads run through the orchestrator.</p>
        ) : (
          <div className="overflow-x-auto border border-slate-100 rounded-lg">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-100">
                  <th className="px-2 py-2 font-medium text-slate-500">Time</th>
                  <th className="px-2 py-2 font-medium text-slate-500">Agent</th>
                  <th className="px-2 py-2 font-medium text-slate-500">Status</th>
                  <th className="px-2 py-2 font-medium text-slate-500">Kind</th>
                  <th className="px-2 py-2 font-medium text-slate-500">ms</th>
                </tr>
              </thead>
              <tbody>
                {orchExecutions.map((row) => (
                  <tr key={row.id} className="border-b border-slate-50 last:border-0">
                    <td className="px-2 py-1.5 text-slate-600 whitespace-nowrap">{(row.started_at || '').replace('T', ' ').slice(0, 19)}</td>
                    <td className="px-2 py-1.5 font-medium text-slate-800 capitalize">{row.agent_name || '—'}</td>
                    <td className="px-2 py-1.5 text-slate-600">{row.status || '—'}</td>
                    <td className="px-2 py-1.5 text-slate-500">{row.workflow_kind || '—'}</td>
                    <td className="px-2 py-1.5 text-slate-500">{row.duration_ms != null ? Math.round(row.duration_ms) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <details className="px-2 py-2 bg-slate-50/80 border-t border-slate-100 text-[10px] text-slate-500">
              <summary className="cursor-pointer select-none">Debug: routing + output (first row)</summary>
              {orchExecutions[0] && (
                <pre className="mt-2 p-2 overflow-x-auto text-[10px] text-slate-600 whitespace-pre-wrap break-all">
{JSON.stringify({ routing_decision: orchExecutions[0].routing_decision, output_summary: orchExecutions[0].output_summary, error: orchExecutions[0].error }, null, 0)}
                </pre>
              )}
            </details>
          </div>
        )}
      </div>

      {/* LLM Engines */}
      <div className="bg-white border border-slate-100 rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2"><Wand2 size={14} className="text-violet-500" /> LLM Engines ({llmEngines.length})</h3>
          {isAdmin && (
            <button onClick={() => { const m = LLM_MODELS.gemini[0]; setShowAddLlmForm(true); setAddLlmForm({ model_name: m, provider: 'gemini', temperature: 0.7, max_tokens: MODEL_MAX_TOKENS[m] || 2048 }); }}
              className="flex items-center gap-1 px-2.5 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-500">
              <Plus size={12} /> Add Engine
            </button>
          )}
        </div>

        {llmEngines.length === 0 ? (
          <p className="text-xs text-slate-400 py-3">No LLM engines configured yet. They are auto-provisioned on first AI interaction.</p>
        ) : (
          <div className="space-y-3">
            {llmEngines.map(e => (
              <div key={e.id} className="border border-slate-200 rounded-lg overflow-hidden">
                <div className="flex items-center justify-between p-3 bg-slate-50">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-700">{e.provider ? e.provider.charAt(0).toUpperCase() + e.provider.slice(1) : '—'} — {e.model_name || 'No model'}</p>
                    <p className="text-[10px] text-slate-400">Temp: {e.temperature ?? 0.7} | Max tokens: {e.max_tokens ?? 2048}</p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${e.is_selected ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{e.is_selected ? 'Live now' : 'Catalog only'}</span>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${e.provider_ready ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'}`}>{e.provider_ready ? 'Provider ready' : 'Provider missing key'}</span>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${e.supports_vision ? 'bg-violet-50 text-violet-700' : 'bg-slate-100 text-slate-500'}`}>{e.supports_vision ? 'Vision capable' : 'Text only'}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                    {isAdmin && (
                      <button
                        onClick={async () => { setSaving(`llm-select-${e.id}`); const r = await api.post(`/ai/llm-engines/${e.id}/select`).catch(err => err); setSaving(''); if (r?.data?.ok) { await refreshAiConfig(); return; } alert(r?.response?.data?.detail || 'Failed to select live engine'); }}
                        disabled={saving === `llm-select-${e.id}` || e.is_selected}
                        className={`px-3 py-1.5 rounded-lg text-[11px] font-medium transition-colors ${e.is_selected ? 'bg-emerald-100 text-emerald-700 cursor-default' : 'bg-violet-600 text-white hover:bg-violet-500 disabled:opacity-50'}`}
                      >
                        {e.is_selected ? 'Selected' : saving === `llm-select-${e.id}` ? 'Switching…' : 'Use Live'}
                      </button>
                    )}
                    {isAdmin && (
                      <button onClick={() => { setEditingLlmId(editingLlmId === e.id ? null : e.id); setLlmDraft({ model_name: e.model_name || '', provider: e.provider || 'gemini', temperature: e.temperature ?? 0.7, max_tokens: e.max_tokens ?? 2048 }); }}
                        className="p-1.5 hover:bg-slate-200 rounded text-slate-500"><Edit size={13} /></button>
                    )}
                    {isAdmin && (
                      <button onClick={async () => { if (!window.confirm('Delete this LLM engine?')) return; await api.delete(`/ai/llm-engines/${e.id}`).catch(() => null); await refreshAiConfig(); setEditingLlmId(null); }}
                        className="p-1.5 hover:bg-red-50 rounded text-slate-400 hover:text-red-500"><Trash2 size={13} /></button>
                    )}
                  </div>
                </div>
                {editingLlmId === e.id && (
                  <div className="p-4 bg-white border-t border-slate-100 space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Provider</label>
                        <select value={llmDraft.provider}
                          onChange={ev => { const first = (LLM_MODELS[ev.target.value] || [])[0] || ''; setLlmDraft(p => ({ ...p, provider: ev.target.value, model_name: first, max_tokens: MODEL_MAX_TOKENS[first] || 2048 })); }}
                          className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm">
                          <option value="gemini">Gemini</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="custom">Custom</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Model</label>
                        {llmDraft.provider === 'custom'
                          ? <input value={llmDraft.model_name} onChange={ev => setLlmDraft(p => ({ ...p, model_name: ev.target.value }))} placeholder="custom-model-name" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" />
                          : <select value={llmDraft.model_name} onChange={ev => setLlmDraft(p => ({ ...p, model_name: ev.target.value, max_tokens: MODEL_MAX_TOKENS[ev.target.value] || p.max_tokens }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm">{(LLM_MODELS[llmDraft.provider] || []).map(m => <option key={m} value={m}>{m}</option>)}</select>}
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Temperature ({llmDraft.temperature})</label>
                        <input type="range" min="0" max="1" step="0.05" value={llmDraft.temperature} onChange={ev => setLlmDraft(p => ({ ...p, temperature: parseFloat(ev.target.value) }))} className="w-full accent-violet-600" />
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Max Tokens</label>
                        <input type="number" value={llmDraft.max_tokens} onChange={ev => setLlmDraft(p => ({ ...p, max_tokens: parseInt(ev.target.value) || 2048 }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm" />
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={async () => { const r = await api.put(`/ai/llm-engines/${e.id}`, llmDraft).catch(() => null); if (r) { await refreshAiConfig(); setEditingLlmId(null); } }}
                        disabled={saving === 'llm' + e.id}
                        className="px-4 py-2 bg-violet-600 text-white rounded-lg text-xs font-medium hover:bg-violet-500 disabled:opacity-50">
                        {saving === 'llm' + e.id ? 'Saving…' : 'Save Changes'}
                      </button>
                      <button onClick={() => setEditingLlmId(null)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs font-medium hover:bg-slate-200">Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {showAddLlmForm && (
          <div className="mt-4 border border-violet-200 bg-violet-50/40 rounded-xl p-4 space-y-3">
            <p className="text-xs font-semibold text-violet-700">New LLM Engine</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] text-slate-400 font-medium mb-1 block">Provider *</label>
                <select value={addLlmForm.provider}
                  onChange={ev => { const first = (LLM_MODELS[ev.target.value] || [])[0] || ''; setAddLlmForm(p => ({ ...p, provider: ev.target.value, model_name: first, max_tokens: MODEL_MAX_TOKENS[first] || 2048 })); }}
                  className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm">
                  <option value="gemini">Gemini</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="custom">Custom</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-400 font-medium mb-1 block">Model *</label>
                {addLlmForm.provider === 'custom'
                  ? <input value={addLlmForm.model_name} onChange={ev => setAddLlmForm(p => ({ ...p, model_name: ev.target.value }))} placeholder="custom-model-name" className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm" />
                  : <select value={addLlmForm.model_name} onChange={ev => setAddLlmForm(p => ({ ...p, model_name: ev.target.value, max_tokens: MODEL_MAX_TOKENS[ev.target.value] || p.max_tokens }))} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm">{(LLM_MODELS[addLlmForm.provider] || []).map(m => <option key={m} value={m}>{m}</option>)}</select>}
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={async () => { if (!addLlmForm.model_name.trim()) return; const r = await api.post('/ai/llm-engines', addLlmForm).catch(() => null); if (r?.data) { await refreshAiConfig(); setShowAddLlmForm(false); } }}
                className="px-4 py-2 bg-violet-600 text-white rounded-lg text-xs font-medium hover:bg-violet-500">Add Engine</button>
              <button onClick={() => setShowAddLlmForm(false)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs">Cancel</button>
            </div>
          </div>
        )}
      </div>

      {/* AI Agents */}
      <div className="bg-white border border-slate-100 rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2"><Bot size={14} className="text-blue-500" /> AI Agents ({aiAgents.length})</h3>
          {isAdmin && (
            <button onClick={() => { setShowAddAgentForm(true); setAddAgentForm({ agent_type: 'support', provider: 'gemini', model_name: LLM_MODELS.gemini[0], is_active: true, mcp_server_id: '' }); }}
              className="flex items-center gap-1 px-2.5 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-500">
              <Plus size={12} /> Add Agent
            </button>
          )}
        </div>

        {aiAgents.length === 0 ? (
          <p className="text-xs text-slate-400 py-3">No AI agents found. The system AI agent is created automatically when a user signs in.</p>
        ) : (
          <div className="space-y-3">
            {aiAgents.map(a => (
              <div key={a.id} className="border border-slate-200 rounded-lg overflow-hidden">
                <div className="flex items-center justify-between p-3 bg-slate-50">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-700 capitalize">{a.agent_type || 'support'} Agent</p>
                    <p className="text-[10px] text-slate-400">Provider: {a.provider || '—'} | Model: {a.model_name || 'default'} | LLM: {a.llm_id?.substring(0, 8) || 'default'}…</p>
                  </div>
                  <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                    <button onClick={async () => { const nextActive = !a.is_active; const r = await api.put(`/ai/agents/${a.id}`, { is_active: nextActive }).catch(() => null); if (r) { setAiAgents(prev => prev.map(x => x.id === a.id ? { ...x, is_active: nextActive } : x)); if (nextActive) setCompany(prev => ({ ...prev, ai_enabled: true })); } }}
                      className={`relative w-9 h-5 rounded-full transition-colors ${a.is_active ? 'bg-green-500' : 'bg-gray-300'}`}>
                      <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow ${a.is_active ? 'translate-x-4' : ''}`} />
                    </button>
                    {isAdmin && (
                      <button onClick={() => { setEditingAgentId(editingAgentId === a.id ? null : a.id); setAgentDraft({ agent_type: a.agent_type || 'support', provider: a.provider || 'gemini', model_name: a.model_name || '', mcp_server_id: a.mcp_server_id || '' }); }}
                        className="p-1.5 hover:bg-slate-200 rounded text-slate-500"><Edit size={13} /></button>
                    )}
                    {isAdmin && (
                      <button onClick={async () => { if (!window.confirm('Delete this AI agent?')) return; await api.delete(`/ai/agents/${a.id}`).catch(() => null); setAiAgents(prev => prev.filter(x => x.id !== a.id)); setEditingAgentId(null); }}
                        className="p-1.5 hover:bg-red-50 rounded text-slate-400 hover:text-red-500"><Trash2 size={13} /></button>
                    )}
                  </div>
                </div>
                {editingAgentId === a.id && (
                  <div className="p-4 bg-white border-t border-slate-100 space-y-3">
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Agent Type</label>
                        <select value={agentDraft.agent_type} onChange={ev => setAgentDraft(p => ({ ...p, agent_type: ev.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm">
                          <option value="support">Support</option><option value="sales">Sales</option><option value="onboarding">Onboarding</option><option value="generic">Generic</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Provider</label>
                        <select value={agentDraft.provider} onChange={ev => setAgentDraft(p => ({ ...p, provider: ev.target.value, model_name: (LLM_MODELS[ev.target.value] || [])[0] || '' }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm">
                          <option value="gemini">Gemini</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">Model</label>
                        <select value={agentDraft.model_name} onChange={ev => setAgentDraft(p => ({ ...p, model_name: ev.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm">
                          {(LLM_MODELS[agentDraft.provider] || []).map(m => <option key={m} value={m}>{m}</option>)}
                        </select>
                      </div>
                      <div className="col-span-3">
                        <label className="text-[10px] text-slate-400 font-medium mb-1 block">MCP server (optional)</label>
                        <select value={agentDraft.mcp_server_id || ''} onChange={ev => setAgentDraft(p => ({ ...p, mcp_server_id: ev.target.value }))} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm">
                          <option value="">None</option>
                          {mcpSelectOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                        </select>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={async () => { const r = await api.put(`/ai/agents/${a.id}`, agentDraft).catch(() => null); if (r) { setAiAgents(prev => prev.map(x => x.id === a.id ? { ...x, ...r.data } : x)); if (r.data?.is_active) setCompany(prev => ({ ...prev, ai_enabled: true })); setEditingAgentId(null); } }}
                        className="px-4 py-2 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-500">Save Changes</button>
                      <button onClick={() => setEditingAgentId(null)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs">Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {showAddAgentForm && (
          <div className="mt-4 border border-blue-200 bg-blue-50/40 rounded-xl p-4 space-y-3">
            <p className="text-xs font-semibold text-blue-700">New AI Agent</p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-[10px] text-slate-400 font-medium mb-1 block">Agent Type</label>
                <select value={addAgentForm.agent_type} onChange={ev => setAddAgentForm(p => ({ ...p, agent_type: ev.target.value }))} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm">
                  <option value="support">Support</option><option value="sales">Sales</option><option value="onboarding">Onboarding</option><option value="generic">Generic</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-400 font-medium mb-1 block">Provider</label>
                <select value={addAgentForm.provider} onChange={ev => setAddAgentForm(p => ({ ...p, provider: ev.target.value, model_name: (LLM_MODELS[ev.target.value] || [])[0] || '' }))} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm">
                  <option value="gemini">Gemini</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-400 font-medium mb-1 block">Model</label>
                <select value={addAgentForm.model_name} onChange={ev => setAddAgentForm(p => ({ ...p, model_name: ev.target.value }))} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm">
                  {(LLM_MODELS[addAgentForm.provider] || []).map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div className="col-span-3">
                <label className="text-[10px] text-slate-400 font-medium mb-1 block">MCP server (optional)</label>
                <select value={addAgentForm.mcp_server_id || ''} onChange={ev => setAddAgentForm(p => ({ ...p, mcp_server_id: ev.target.value }))} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm">
                  <option value="">None</option>
                  {mcpSelectOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={async () => { const r = await api.post('/ai/agents', addAgentForm).catch(() => null); if (r?.data) { setAiAgents(prev => [r.data, ...prev]); if (r.data.is_active) setCompany(prev => ({ ...prev, ai_enabled: true })); setShowAddAgentForm(false); } }}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-500">Add AI Agent</button>
              <button onClick={() => setShowAddAgentForm(false)} className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs">Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
