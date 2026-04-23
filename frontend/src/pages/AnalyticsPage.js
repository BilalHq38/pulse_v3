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
  const [loading, setLoading] = useState(true);
  const [generatingDaily, setGeneratingDaily] = useState(false);
  const [expandedDay, setExpandedDay] = useState(null);

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
    ]).then(([ov, cd, ld, sd, ad, cs, ds, ais, ar]) => {
      setOverview(ov.data);
      setConvoData(cd.data);
      setLeadData(ld.data);
      setSentimentData(sd.data);
      setAgentData(ad.data);
      setCustomerSummaries(cs.data || []);
      setDailySummaries(ds.data || []);
      setAiSessions(ais.data || []);
      setAnalyticsReports(ar.data || []);
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

  return (
    <div className="p-6 lg:p-8 space-y-8" data-testid="analytics-page">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Analytics</h1>
        <p className="text-slate-400 text-sm mt-1">Performance insights and business metrics</p>
      </div>

      {/* Top KPIs */}
      {overview && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4" data-testid="analytics-kpis">
          {[
            { label: 'Conversations', value: overview.total_conversations, icon: MessageSquare, color: 'violet' },
            { label: 'AI Rate', value: `${overview.ai_resolution_rate}%`, icon: Bot, color: 'purple' },
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
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }} />
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
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }} />
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
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }} />
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
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '12px' }} />
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
            {generatingDaily ? 'Generating...' : 'Generate Today'}
          </button>
        </div>
        {dailySummaries.length === 0 ? (
          <div className="text-center py-10 text-slate-400 text-sm">
            <Sparkles size={28} className="mx-auto mb-2 opacity-30" />
            No daily summaries yet. Click "Generate Today" to create one.
          </div>
        ) : (
          <div className="space-y-3">
            {dailySummaries.map((ds) => (
              <div key={ds.id || ds.date} className="border border-slate-100 rounded-lg overflow-hidden">
                <button
                  onClick={() => setExpandedDay(expandedDay === ds.date ? null : ds.date)}
                  className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors text-left"
                >
                  <div className="flex items-center gap-3">
                    <Calendar size={14} className="text-violet-400" />
                    <span className="text-sm font-medium text-slate-700">{ds.date}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      ds.overall_sentiment === 'positive' ? 'bg-emerald-50 text-emerald-700' :
                      ds.overall_sentiment === 'negative' ? 'bg-red-50 text-red-600' :
                      ds.overall_sentiment === 'mixed' ? 'bg-amber-50 text-amber-700' :
                      'bg-slate-100 text-slate-500'
                    }`}>{ds.overall_sentiment || 'neutral'}</span>
                    <span className="text-xs text-slate-400">{ds.total_interactions} interactions</span>
                  </div>
                  <span className="text-slate-400 text-xs">{expandedDay === ds.date ? '▲' : '▼'}</span>
                </button>
                {expandedDay === ds.date && (
                  <div className="px-4 pb-4 space-y-4 border-t border-slate-100 pt-3">
                    {ds.summary_text && (
                      <p className="text-sm text-slate-600 leading-relaxed">{ds.summary_text}</p>
                    )}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      {ds.top_topics?.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide">Top Topics</p>
                          <ul className="space-y-1">
                            {ds.top_topics.map((t, i) => (
                              <li key={i} className="flex items-center gap-1.5 text-xs text-slate-600">
                                <span className="w-1.5 h-1.5 rounded-full bg-violet-400"></span>{t}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {ds.highlight_issues?.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide flex items-center gap-1"><AlertCircle size={10} className="text-red-400" /> Issues</p>
                          <ul className="space-y-1">
                            {ds.highlight_issues.map((t, i) => (
                              <li key={i} className="text-xs text-red-600">{t}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {ds.recommendations?.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide flex items-center gap-1"><CheckCircle size={10} className="text-emerald-500" /> Recommendations</p>
                          <ul className="space-y-1">
                            {ds.recommendations.map((r, i) => (
                              <li key={i} className="text-xs text-emerald-700">{r}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Customer Interaction Log */}
      <div className="bg-white border border-slate-100 rounded-xl p-6" data-testid="customer-interaction-log">
        <div className="flex items-center gap-2 mb-5">
          <MessageSquare size={16} className="text-cyan-500" />
          <h3 className="text-sm font-semibold text-slate-900">Customer Interaction Summaries</h3>
          <span className="text-xs text-slate-400 ml-1">({customerSummaries.length} recent)</span>
        </div>
        {customerSummaries.length === 0 ? (
          <div className="text-center py-10 text-slate-400 text-sm">
            <MessageSquare size={28} className="mx-auto mb-2 opacity-30" />
            Interaction summaries appear here after AI conversations.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px]">
              <thead>
                <tr className="border-b border-slate-100">
                  <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Customer</th>
                  <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Date</th>
                  <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Sentiment</th>
                  <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Status</th>
                  <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Topics</th>
                  <th className="text-left text-xs text-slate-400 font-medium py-2 px-3">Summary</th>
                </tr>
              </thead>
              <tbody>
                {customerSummaries.map((cs) => (
                  <tr key={cs.id} className="border-b border-slate-100 hover:bg-slate-50 transition-colors">
                    <td className="py-3 px-3">
                      <div className="flex items-center gap-2">
                        <div className="w-6 h-6 rounded-full bg-cyan-50 flex items-center justify-center text-xs font-bold text-cyan-600">{(cs.customer_name || '?').charAt(0).toUpperCase()}</div>
                        <span className="text-sm text-slate-700 font-medium">{cs.customer_name || 'Unknown'}</span>
                      </div>
                    </td>
                    <td className="py-3 px-3 text-xs text-slate-500">{cs.date}</td>
                    <td className="py-3 px-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        cs.sentiment_label === 'positive' ? 'bg-emerald-50 text-emerald-700' :
                        cs.sentiment_label === 'negative' ? 'bg-red-50 text-red-600' :
                        cs.sentiment_label === 'mixed' ? 'bg-amber-50 text-amber-700' :
                        'bg-slate-100 text-slate-500'
                      }`}>{cs.sentiment_label || 'neutral'}</span>
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex items-center gap-1">
                        {cs.resolution_status === 'resolved' ? <CheckCircle size={12} className="text-emerald-500" /> :
                         cs.resolution_status === 'escalated' ? <AlertCircle size={12} className="text-red-500" /> :
                         <Clock size={12} className="text-amber-500" />}
                        <span className="text-xs text-slate-600 capitalize">{cs.resolution_status?.replace('_', ' ')}</span>
                      </div>
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex flex-wrap gap-1">
                        {(cs.topics || []).slice(0, 3).map((t, i) => (
                          <span key={i} className="text-xs bg-violet-50 text-violet-700 px-1.5 py-0.5 rounded">{t}</span>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-xs text-slate-500 max-w-[260px] truncate">{cs.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
