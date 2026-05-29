import { useState, useEffect } from 'react';
import api from '@/lib/api';
import { BarChart3, TrendingUp, Bot, Users, Target, MessageSquare, Sparkles, Calendar, RefreshCw, AlertCircle, CheckCircle, Clock, Package } from 'lucide-react';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';

const COLORS = ['#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899'];

function summaryEntityType(item) {
  const entityType = String(item?.entity_type || '').trim().toLowerCase();
  if (entityType) return entityType;
  const sourceType = String(item?.source_type || '').trim().toLowerCase();
  if (sourceType.includes('customer')) return 'customer';
  if (sourceType.includes('lead')) return 'lead';
  const legacyType = String(item?.type || '').trim().toLowerCase();
  return legacyType === 'customer' || legacyType === 'lead' ? legacyType : '';
}

function normalizeSummaryKeyPart(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function interactionSummaryKey(item) {
  const entityType = summaryEntityType(item) || 'unknown';
  const entityId = item?.customer_id || item?.lead_id || item?.entity_id || item?.name || item?.id || '';
  const date = item?.date || item?.summary_date || '';
  if (entityType === 'customer') {
    return [
      entityType,
      entityId,
      item?.conversation_id || '',
      date,
    ].map(normalizeSummaryKeyPart).join('|');
  }
  return [
    entityType,
    entityId,
    date,
    item?.activity_type || item?.type || '',
    item?.stage || item?.status || '',
    item?.content || item?.summary_text || '',
  ].map(normalizeSummaryKeyPart).join('|');
}

function dedupeInteractionSummaries(items) {
  const seen = new Set();
  const unique = [];
  (Array.isArray(items) ? items : []).forEach((item) => {
    const key = interactionSummaryKey(item);
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(item);
  });
  return unique;
}

export default function AnalyticsPage() {
  const [overview, setOverview] = useState(null);
  const [convoData, setConvoData] = useState([]);
  const [leadData, setLeadData] = useState(null);
  const [sentimentData, setSentimentData] = useState([]);
  const [agentData, setAgentData] = useState([]);
  const [customerSummaries, setCustomerSummaries] = useState([]);
  const [dailySummaries, setDailySummaries] = useState([]);
  const [aiSessions, setAiSessions] = useState([]);
  const [analyticsReports, setAnalyticsReports] = useState([]);
  const [aiScore, setAiScore] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generatingDaily, setGeneratingDaily] = useState(false);
  const [expandedDay, setExpandedDay] = useState(null);

  // Filter to toggle interaction summaries between customer, lead or all.
  // 'all' shows both customer and lead summaries, 'customer' shows only customers,
  // 'lead' shows only leads. Default is 'all'.
  const [interactionFilter, setInteractionFilter] = useState('all');

  const fetchAll = () => {
    Promise.all([
      api.get('/analytics/overview'),
      api.get('/analytics/conversations'),
      api.get('/analytics/leads'),
      api.get('/analytics/sentiment'),
      api.get('/analytics/agents'),
      api.get('/analytics/customer-summaries'),
      api.get('/analytics/daily-summaries'),
      api.get('/ai/sessions', { params: { limit: 20 } }).catch(() => ({ data: [] })),
      api.get('/analytics/reports', { params: { limit: 10 } }).catch(() => ({ data: [] })),
      api.get('/analytics/ai-score').catch(() => ({ data: null })),
    ]).then(([ov, cd, ld, sd, ad, cs, ds, ais, ar, asc]) => {
      setOverview(ov.data);
      setConvoData(cd.data);
      setLeadData(ld.data);
      setSentimentData(sd.data);
      setAgentData(ad.data);
      setCustomerSummaries(cs.data || []);
      setDailySummaries(ds.data || []);
      setAiSessions(ais.data || []);
      setAnalyticsReports(ar.data || []);
      setAiScore(asc.data || null);
    }).catch(console.error).finally(() => setLoading(false));
  };

  useEffect(() => { fetchAll(); }, []);

  const handleGenerateDaily = async () => {
    setGeneratingDaily(true);
    try {
      await api.post('/analytics/daily-summary/generate');
      const ds = await api.get('/analytics/daily-summaries');
      setDailySummaries(ds.data || []);
    } catch (e) { console.error(e); }
    finally { setGeneratingDaily(false); }
  };

  if (loading) return <div className="flex items-center justify-center h-full"><div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin"></div></div>;

  const leadsBySource = leadData ? Object.entries(leadData.by_source).map(([name, value]) => ({ name: name.replace('_', ' '), value })) : [];
  const leadsByGrade = leadData ? Object.entries(leadData.by_grade).map(([name, value]) => ({ name, value })) : [];
  const interactionRows = dedupeInteractionSummaries(customerSummaries);
  const customerInteractionRows = interactionRows.filter((item) => summaryEntityType(item) === 'customer');
  const leadInteractionRows = interactionRows.filter((item) => summaryEntityType(item) === 'lead');
  // Determine which rows are visible based on the current interaction filter.
  const filteredInteractionRows =
    interactionFilter === 'customer'
      ? customerInteractionRows
      : interactionFilter === 'lead'
      ? leadInteractionRows
      : [...customerInteractionRows, ...leadInteractionRows];

  // Show the count of visible interactions in the header based on the current filter.
  const visibleInteractionCount = filteredInteractionRows.length;

  return (
    <div className="p-6 lg:p-8 space-y-8" data-testid="analytics-page">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Analytics</h1>
        <p className="text-slate-400 text-sm mt-1">Performance insights and business metrics</p>
      </div>

      {/* Top KPIs */}
      {overview && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4" data-testid="analytics-kpis">
          {[
            { label: 'Conversations', value: overview.total_conversations, icon: MessageSquare, color: 'violet' },
            { label: 'AI Rate', value: `${overview.ai_resolution_rate}%`, icon: Bot, color: 'purple' },
            { label: 'AI Score', value: aiScore ? `${aiScore.ai_score}%` : '—', icon: Sparkles, color: 'purple' },
            { label: 'CSAT', value: overview.csat_score, icon: TrendingUp, color: 'emerald' },
            { label: 'NPS', value: overview.nps_score, icon: BarChart3, color: 'cyan' },
            { label: 'Leads', value: overview.total_leads, icon: Target, color: 'amber' },
            { label: 'Customers', value: overview.total_customers, icon: Users, color: 'fuchsia' },
            { label: 'Products', value: overview.total_products, icon: Package, color: 'blue' },
          ].map(k => (
            <div key={k.label} className="bg-white border border-slate-100 rounded-xl p-4">
              <k.icon size={16} className="text-slate-400 mb-2" />
              <p className="text-2xl font-bold text-slate-900">{k.value}</p>
              <p className="text-xs text-slate-400 mt-0.5">{k.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Conversation Volume */}
        <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="chart-conversation-volume">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Conversation Volume (30d)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={convoData.slice(-15)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="date" stroke="#cbd5e1" tick={{ fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
              <YAxis stroke="#cbd5e1" tick={{ fontSize: 10 }} />
              <Tooltip
                contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }}
                labelStyle={{ color: '#f9fafb' }}
                itemStyle={{ color: '#f9fafb' }}
                cursor={{ fill: '#f8fafc' }}
              />
              <Bar dataKey="ai_handled" fill="#8b5cf6" name="AI" radius={[2, 2, 0, 0]} />
              <Bar dataKey="human_handled" fill="#06b6d4" name="Human" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Sentiment Trend */}
        <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="chart-sentiment-trend">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Sentiment Trend (30d)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={sentimentData.slice(-15)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="date" stroke="#cbd5e1" tick={{ fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
              <YAxis stroke="#cbd5e1" tick={{ fontSize: 10 }} domain={[-1, 1]} />
              <Tooltip
                contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }}
                labelStyle={{ color: '#f9fafb' }}
                itemStyle={{ color: '#f9fafb' }}
                cursor={{ stroke: '#f8fafc', fill: '#f8fafc', strokeWidth: 1 }}
              />
              <Line type="monotone" dataKey="avg_sentiment" stroke="#10b981" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Leads by Source */}
        <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="chart-leads-source">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Leads by Source</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie data={leadsBySource.length ? leadsBySource : [{name: 'No data', value: 1}]} cx="50%" cy="50%" outerRadius={80} innerRadius={40} dataKey="value" paddingAngle={3}>
                {(leadsBySource.length ? leadsBySource : [{name: 'No data'}]).map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              </Pie>
              <Tooltip
                contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }}
                labelStyle={{ color: '#f9fafb' }}
                itemStyle={{ color: '#f9fafb' }}
              />
              <Legend wrapperStyle={{ fontSize: '12px' }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Lead Grade Distribution */}
        <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="chart-leads-grade">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Lead Grade Distribution</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={leadsByGrade} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis type="number" stroke="#cbd5e1" tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="name" stroke="#cbd5e1" tick={{ fontSize: 11 }} width={60} />
              <Tooltip
                contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }}
                labelStyle={{ color: '#f9fafb' }}
                itemStyle={{ color: '#f9fafb' }}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {leadsByGrade.map((entry, i) => <Cell key={i} fill={entry.name === 'hot' ? '#ef4444' : entry.name === 'warm' ? '#f59e0b' : '#3b82f6'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Agent Performance */}
      <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="agent-performance">
        <h3 className="text-sm font-semibold text-slate-900 mb-4">Agent Performance</h3>
        <table className="w-full">
          <thead>
            <tr className="border-b border-slate-100">
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">Agent</th>
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">Role</th>
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">Conversations</th>
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">Resolved</th>
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">Resolution Rate</th>
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">Avg Response</th>
              <th className="text-left text-xs text-slate-400 font-medium py-3 px-4">CSAT</th>
            </tr>
          </thead>
          <tbody>
            {agentData.map((agent) => (
              <tr key={agent.id} className="border-b border-slate-100 hover:bg-slate-50 transition-colors">
                <td className="py-3 px-4">
                  <div className="flex items-center gap-2">
                    <div className="w-7 h-7 rounded-full bg-blue-50 flex items-center justify-center text-xs font-bold text-blue-600">{agent.name?.charAt(0)}</div>
                    <span className="text-sm text-slate-700">{agent.name}</span>
                  </div>
                </td>
                <td className="py-3 px-4 text-xs text-slate-500 capitalize">{agent.role}</td>
                <td className="py-3 px-4 text-sm text-slate-600 font-medium">{agent.conversations_handled}</td>
                <td className="py-3 px-4 text-sm text-slate-600">{agent.resolved}</td>
                <td className="py-3 px-4">
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${agent.resolution_rate}%` }}></div>
                    </div>
                    <span className="text-xs text-slate-500">{agent.resolution_rate}%</span>
                  </div>
                </td>
                <td className="py-3 px-4 text-sm text-slate-500">{agent.avg_response_time}</td>
                <td className="py-3 px-4 text-sm text-amber-600 font-medium">{agent.csat}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* AI Score & Nature Breakdown */}
      {aiScore && (
        <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="ai-score-panel">
          <div className="flex items-center gap-2 mb-5">
            <Sparkles size={16} className="text-purple-500" />
            <h3 className="text-sm font-semibold text-slate-900">AI Score &amp; Nature Breakdown</h3>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-5">
            {[
              { label: 'AI Score', value: `${aiScore.ai_score}%`, sub: 'composite', color: 'text-purple-700', bg: 'bg-purple-50' },
              { label: 'Avg Confidence', value: `${aiScore.avg_confidence_pct}%`, sub: 'per conversation', color: 'text-blue-700', bg: 'bg-blue-50' },
              { label: 'Resolution Rate', value: `${aiScore.resolution_rate}%`, sub: 'AI-handled', color: 'text-emerald-700', bg: 'bg-emerald-50' },
              { label: 'Escalations', value: aiScore.ai_escalated, sub: 'AI-handled', color: 'text-red-700', bg: 'bg-red-50' },
            ].map((stat) => (
              <div key={stat.label} className={`${stat.bg} rounded-lg p-3 text-center`}>
                <p className={`text-xl font-bold ${stat.color}`}>{stat.value}</p>
                <p className="text-[11px] font-medium text-slate-700 mt-0.5">{stat.label}</p>
                <p className="text-[10px] text-slate-400">{stat.sub}</p>
              </div>
            ))}
          </div>
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Nature Distribution</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: 'Excellent', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
                { label: 'Good', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
                { label: 'Moderate', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
                { label: 'Low', cls: 'bg-red-50 text-red-700 border-red-200' },
              ].map(({ label, cls }) => (
                <div key={label} className={`rounded-lg border p-3 text-center ${cls}`}>
                  <p className="text-xl font-bold">{(aiScore.nature_breakdown || {})[label] ?? 0}</p>
                  <p className="text-[11px] font-semibold mt-0.5">{label}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Daily AI Summaries */}
      <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="daily-summaries">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-500" />
            <h3 className="text-sm font-semibold text-slate-900">Daily AI Summaries</h3>
          </div>
          <button
            onClick={handleGenerateDaily}
            disabled={generatingDaily}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 text-white text-xs font-medium rounded-lg hover:bg-violet-700 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={generatingDaily ? 'animate-spin' : ''} />
            {generatingDaily ? 'Generating...' : "Generate Today's Summary"}
          </button>
        </div>
        {dailySummaries.length === 0 ? (
          <div className="text-center py-10 text-slate-400 text-sm">
            <Sparkles size={28} className="mx-auto mb-2 opacity-30" />
            No daily summaries yet. Click "Generate Today's Summary" to create one.
          </div>
        ) : (
          <div className="space-y-3">
            {dailySummaries.map((ds) => (
              <div key={ds.id || ds.date} className="border border-slate-100 rounded-lg overflow-hidden">
                {(() => {
                  // Use a unique key for the expanded state; prefer the summary id if available, otherwise fall back to the date.
                  const dayKey = ds.id || ds.date;
                  return (
                    <>
                      <button
                        onClick={() => setExpandedDay(expandedDay === dayKey ? null : dayKey)}
                        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors text-left"
                      >
                        <div className="flex items-center gap-3">
                          <Calendar size={14} className="text-violet-400" />
                          {/* Date column */}
                          <span className="text-xs font-mono text-slate-500 bg-slate-100 px-2 py-0.5 rounded">{ds.summary_date || ds.date}</span>
                          <span className="text-sm font-medium text-slate-700">{ds.date}</span>
                          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                            ds.overall_sentiment === 'positive'
                              ? 'bg-emerald-50 text-emerald-700'
                              : ds.overall_sentiment === 'negative'
                              ? 'bg-red-50 text-red-600'
                              : ds.overall_sentiment === 'mixed'
                              ? 'bg-amber-50 text-amber-700'
                              : 'bg-slate-100 text-slate-500'
                          }`}>{ds.overall_sentiment || 'neutral'}</span>
                          <span className="text-xs text-slate-400">{ds.total_interactions} interactions</span>
                        </div>
                        <span className="text-slate-400 text-xs">{expandedDay === dayKey ? '▲' : '▼'}</span>
                      </button>
                      {expandedDay === dayKey && (
                        <div className="px-4 pb-4 space-y-4 border-t border-slate-100 pt-3">
                          {ds.summary_text && <p className="text-sm text-slate-600 leading-relaxed">{ds.summary_text}</p>}
                          {/* Comprehensive stats cards */}
                          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
                            {[
                              { label: 'Total Interactions', value: ds.total_interactions ?? '—', color: 'text-violet-700', bg: 'bg-violet-50' },
                              { label: 'Total Messages', value: ds.total_messages ?? '—', color: 'text-blue-700', bg: 'bg-blue-50' },
                              { label: 'Avg Sentiment', value: ds.avg_sentiment != null ? Number(ds.avg_sentiment).toFixed(2) : '—', color: 'text-emerald-700', bg: 'bg-emerald-50' },
                              { label: 'Escalations', value: ds.escalations ?? '—', color: 'text-red-700', bg: 'bg-red-50' },
                              { label: 'AI Handled', value: ds.ai_handled_count ?? '—', color: 'text-cyan-700', bg: 'bg-cyan-50' },
                              { label: 'Date', value: ds.summary_date || ds.date || '—', color: 'text-slate-700', bg: 'bg-slate-100' },
                            ].map((stat, i) => (
                              <div key={i} className={`${stat.bg} rounded-lg p-3 text-center`}>
                                <p className={`text-base font-bold ${stat.color}`}>{stat.value}</p>
                                <p className="text-[10px] text-slate-500 mt-0.5">{stat.label}</p>
                              </div>
                            ))}
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {ds.top_topics?.length > 0 && (
                              <div>
                                <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide">Top Topics</p>
                                <ul className="space-y-1">
                                  {ds.top_topics.map((t, i) => (
                                    <li key={i} className="flex items-center gap-1.5 text-xs text-slate-600">
                                      <span className="w-1.5 h-1.5 rounded-full bg-violet-400"></span>
                                      {t}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {ds.highlight_issues?.length > 0 && (
                              <div>
                                <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide flex items-center gap-1">
                                  <AlertCircle size={10} className="text-red-400" /> Issues
                                </p>
                                <ul className="space-y-1">
                                  {ds.highlight_issues.map((t, i) => (
                                    <li key={i} className="text-xs text-red-600">
                                      {t}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {ds.recommendations?.length > 0 && (
                              <div>
                                <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide flex items-center gap-1">
                                  <CheckCircle size={10} className="text-emerald-500" /> Recommendations
                                </p>
                                <ul className="space-y-1">
                                  {ds.recommendations.map((r, i) => (
                                    <li key={i} className="text-xs text-emerald-700">
                                      {r}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Interaction Log */}
      <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="customer-interaction-log">
        <div className="flex items-center gap-2 mb-5">
          <MessageSquare size={16} className="text-cyan-500" />
          <h3 className="text-sm font-semibold text-slate-900">Lead + Customer Interaction Summary</h3>
          <span className="text-xs text-slate-400 ml-1">({visibleInteractionCount} recent)</span>
        </div>
        {interactionRows.length === 0 ? (
          <div className="text-center py-10 text-slate-400 text-sm">
            <MessageSquare size={28} className="mx-auto mb-2 opacity-30" />
            Interaction summaries appear here after AI conversations.
          </div>
        ) : (
          <>
            {/* Filter buttons for customer/lead/all */}
            <div className="flex items-center gap-2 mb-4">
              <button
                onClick={() => setInteractionFilter('customer')}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg border ${
                  interactionFilter === 'customer'
                    ? 'bg-cyan-500 text-white border-cyan-500'
                    : 'bg-white text-cyan-600 border-cyan-200 hover:bg-cyan-50'
                }`}
              >
                Customers
              </button>
              <button
                onClick={() => setInteractionFilter('lead')}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg border ${
                  interactionFilter === 'lead'
                    ? 'bg-amber-500 text-white border-amber-500'
                    : 'bg-white text-amber-600 border-amber-200 hover:bg-amber-50'
                }`}
              >
                Leads
              </button>
              <button
                onClick={() => setInteractionFilter('all')}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg border ${
                  interactionFilter === 'all'
                    ? 'bg-violet-500 text-white border-violet-500'
                    : 'bg-white text-violet-600 border-violet-200 hover:bg-violet-50'
                }`}
              >
                All
              </button>
            </div>
            {filteredInteractionRows.length === 0 ? (
              <div className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-sm text-slate-400">
                {interactionFilter === 'customer'
                  ? 'No customer interaction summaries yet.'
                  : interactionFilter === 'lead'
                  ? 'No lead-only interaction summaries yet.'
                  : 'No interaction summaries yet.'}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[700px]">
                  <thead>
                    <tr className="border-b border-slate-100">
                      <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Type</th>
                      <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Name</th>
                      <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Date</th>
                      <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Sentiment</th>
                      <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Status</th>
                      <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Topics</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredInteractionRows.map((cs) => {
                      const isCustomer = summaryEntityType(cs) === 'customer';
                      const typeLabel = cs.type || (isCustomer ? 'Customer' : 'Lead');
                      const name = cs.name || cs.customer_name || cs.lead_name || 'Unknown';
                      const initial = name.charAt(0).toUpperCase();
                      const sentiment = cs.sentiment_label || 'neutral';
                      return (
                        <tr key={interactionSummaryKey(cs)} className="border-b border-slate-100 hover:bg-slate-50 transition-colors">
                          <td className="py-3 px-3">
                            <span
                              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                isCustomer
                                  ? 'bg-cyan-50 text-cyan-700'
                                  : 'bg-amber-50 text-amber-700'
                              }`}
                            >
                              {typeLabel}
                            </span>
                          </td>
                          <td className="py-3 px-3">
                            <div className="flex items-center gap-2">
                              <div
                                className={`${isCustomer ? 'bg-cyan-50 text-cyan-600' : 'bg-amber-50 text-amber-700'} w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold`}
                              >
                                {initial}
                              </div>
                              <span className="text-sm text-slate-700 font-medium">{name}</span>
                            </div>
                          </td>
                          <td className="py-3 px-3 text-xs text-slate-500">{cs.date}</td>
                          <td className="py-3 px-3">
                            <span
                              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                sentiment === 'positive'
                                  ? 'bg-emerald-50 text-emerald-700'
                                  : sentiment === 'negative'
                                  ? 'bg-red-50 text-red-600'
                                  : sentiment === 'mixed'
                                  ? 'bg-amber-50 text-amber-700'
                                  : 'bg-slate-100 text-slate-500'
                              }`}
                            >
                              {sentiment}
                            </span>
                          </td>
                          <td className="py-3 px-3">
                            <div className="flex items-center gap-1">
                              {cs.resolution_status === 'resolved' ? (
                                <CheckCircle size={12} className="text-emerald-500" />
                              ) : cs.resolution_status === 'escalated' ? (
                                <AlertCircle size={12} className="text-red-500" />
                              ) : (
                                <Clock size={12} className="text-amber-500" />
                              )}
                              <span className="text-xs text-slate-600 capitalize">{cs.resolution_status?.replace('_', ' ')}</span>
                            </div>
                          </td>
                          <td className="py-3 px-3">
                            <div className="flex flex-wrap gap-1">
                              {(cs.topics || []).slice(0, 3).map((t, i) => (
                                <span key={i} className="text-xs bg-violet-50 text-violet-700 px-1.5 py-0.5 rounded">
                                  {t}
                                </span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      {/* AI Sessions */}
      <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="ai-sessions-log">
        <div className="flex items-center gap-2 mb-5">
          <Bot size={16} className="text-violet-500" />
          <h3 className="text-sm font-semibold text-slate-900">Recent AI Sessions</h3>
          <span className="text-xs text-slate-400 ml-1">({aiSessions.length})</span>
        </div>
        {aiSessions.length === 0 ? (
          <div className="text-center py-8 text-slate-400 text-sm">
            <Bot size={28} className="mx-auto mb-2 opacity-30" />
            AI session records appear here as conversations are processed.
          </div>
        ) : (
          <div className="space-y-2 max-h-[300px] overflow-y-auto">
            {aiSessions.map(s => (
              <div key={s.id} className="flex items-start gap-3 p-3 bg-slate-50 rounded-lg">
                <Sparkles size={14} className="text-violet-400 mt-0.5 flex-shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-slate-400 mb-0.5">{new Date(s.created_at).toLocaleString()}</p>
                  <p className="text-xs text-slate-600 truncate"><span className="font-medium">Prompt:</span> {s.prompt?.substring(0, 120)}</p>
                  <p className="text-xs text-slate-500 truncate"><span className="font-medium">Response:</span> {s.response?.substring(0, 120)}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Analytics Reports */}
      {analyticsReports.length > 0 && (
        <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="analytics-reports">
          <div className="flex items-center gap-2 mb-5">
            <BarChart3 size={16} className="text-blue-500" />
            <h3 className="text-sm font-semibold text-slate-900">Generated Reports</h3>
            <span className="text-xs text-slate-400 ml-1">({analyticsReports.length})</span>
          </div>
          <div className="space-y-2">
            {analyticsReports.map(r => (
              <div key={r.id} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                <div>
                  <p className="text-sm font-medium text-slate-700 capitalize">{(r.report_type || '').replace(/_/g, ' ')}</p>
                  <p className="text-[10px] text-slate-400">Period: {r.period} | Generated: {new Date(r.generated_at).toLocaleDateString()}</p>
                </div>
                <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-600 rounded">{r.report_type}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
