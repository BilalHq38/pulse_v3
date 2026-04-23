import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSocket } from '@/lib/useSocket';
import api from '@/lib/api';
import {
  Activity, AlertCircle, Bot, Clock, Facebook, HeartHandshake, Instagram,
  Mail, MessageSquare, RefreshCw, ShieldCheck, Sparkles, TrendingUp, Users, WalletCards, Zap
} from 'lucide-react';

const DEFAULT_DATA = {
  real_time_activity: {
    incoming_messages: 0,
    active_conversations: 0,
    pending_replies: 0,
    ai_active_chats: 0,
    human_active_chats: 0,
    live_feed: [],
  },
  inbox_summary: {
    total_unread: 0,
    platform_counts: { whatsapp: 0, instagram: 0, facebook: 0, email: 0, web_chat: 0 },
    urgent_chats: 0,
  },
  ai_performance: {
    ai_response_success_rate_today: 0,
    blocked_or_flagged_conversations: 0,
    avg_response_time_ai: '0m',
    avg_response_time_human: '0m',
    suggested_replies_pending: 0,
  },
  leads_quick_view: {
    new_leads_today: 0,
    converted_leads_today: 0,
    hot_leads: 0,
    recent_lead_activity: [],
  },
  alerts: [],
  system_health: { gemini_api: 'unknown', channels: [], bulk_upload_status: 'unknown', server_time: '' },
  micro_visuals: { messages_last_24h: [], sentiment_distribution: { positive: 0, neutral: 0, negative: 0 }, response_rate_gauge: 0 },
  insights: [],
};

const channelMeta = {
  whatsapp: { label: 'WhatsApp', color: 'bg-emerald-500', icon: MessageSquare },
  instagram: { label: 'Instagram', color: 'bg-pink-500', icon: Instagram },
  facebook: { label: 'Facebook', color: 'bg-blue-500', icon: Facebook },
  email: { label: 'Email', color: 'bg-sky-500', icon: Mail },
  web_chat: { label: 'Website', color: 'bg-slate-500', icon: Activity },
};

function MetricCard({ label, value, sub, icon: Icon, tone = 'text-slate-500' }) {
  const bgMap = {
    'text-blue-500': 'bg-blue-50',
    'text-cyan-500': 'bg-cyan-50',
    'text-amber-500': 'bg-amber-50',
    'text-violet-500': 'bg-violet-50',
    'text-emerald-500': 'bg-emerald-50',
  };
  return (
    <div className="bg-white border border-slate-100 rounded-2xl p-3 sm:p-4 pe-card hover:shadow-md transition-all duration-200">
      <div className="flex items-center justify-between mb-2 sm:mb-3">
        <div className={`w-8 h-8 rounded-xl ${bgMap[tone] || 'bg-slate-50'} flex items-center justify-center`}>
          <Icon size={16} className={tone} />
        </div>
      </div>
      <p className="text-xl sm:text-2xl font-bold text-slate-900 tabular-nums">{value}</p>
      <p className="text-[11px] sm:text-xs font-semibold text-slate-700 mt-1">{label}</p>
      <p className="text-[10px] sm:text-[11px] text-slate-400 mt-0.5 line-clamp-1">{sub}</p>
    </div>
  );
}

function TinyBars({ items, color = '#2563eb' }) {
  const max = Math.max(...items.map((item) => item.value || 0), 1);
  return (
    <div className="flex items-end gap-1 h-20">
      {items.map((item) => (
        <div key={item.label} className="flex-1 flex flex-col items-center justify-end gap-1">
          <div className="w-full bg-slate-100 rounded-t-md overflow-hidden" style={{ height: 56 }}>
            <div
              className="w-full rounded-t-md"
              style={{ height: `${Math.max(8, Math.round(((item.value || 0) / max) * 56))}px`, backgroundColor: color }}
            />
          </div>
          <span className="text-[9px] text-slate-400">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const [data, setData] = useState(DEFAULT_DATA);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchLive = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const res = await api.get('/dashboard/live-summary');
      setData(res.data || DEFAULT_DATA);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchLive(true); }, [fetchLive]);

  useSocket((event) => {
    if (['conversation_updated', 'new_message', 'notification'].includes(event)) {
      fetchLive(true);
    }
  });

  const handleAction = (action) => {
    if (!action?.url) return;
    if (/^https?:\/\//.test(action.url)) {
      window.open(action.url, '_blank', 'noopener,noreferrer');
      return;
    }
    navigate(action.url);
  };

  const messageBars = useMemo(
    () => (data.micro_visuals.messages_last_24h || []).slice(-12).map((item) => ({ label: item.bucket, value: Number(item.count || 0) })),
    [data.micro_visuals.messages_last_24h],
  );

  const sentimentBars = useMemo(() => {
    const dist = data.micro_visuals.sentiment_distribution || {};
    return [
      { label: 'Pos', value: Number(dist.positive || 0), color: '#10b981' },
      { label: 'Neu', value: Number(dist.neutral || 0), color: '#94a3b8' },
      { label: 'Neg', value: Number(dist.negative || 0), color: '#ef4444' },
    ];
  }, [data.micro_visuals.sentiment_distribution]);

  const totalSentiment = sentimentBars.reduce((sum, item) => sum + item.value, 0) || 1;

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-5 sm:space-y-6 animate-fadeIn" data-testid="dashboard-page">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-slate-900 flex items-center gap-2">
            <Zap size={20} className="text-blue-500" />
            Live Control Center
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">Monitor, prioritize, and act in real time.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="pe-status-dot pe-status-online" />
          <span className="text-[11px] text-slate-500">Live</span>
          <button
            onClick={() => fetchLive()}
            className="inline-flex items-center gap-2 px-3 sm:px-4 py-2 sm:py-2.5 rounded-xl border border-slate-200 bg-white text-xs sm:text-sm font-medium text-slate-700 hover:bg-slate-50 transition-all hover:shadow-sm"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 sm:gap-4">
        <MetricCard label="Incoming Messages" value={data.real_time_activity.incoming_messages} sub="today across all channels" icon={MessageSquare} tone="text-blue-500" />
        <MetricCard label="Active Conversations" value={data.real_time_activity.active_conversations} sub="open right now" icon={Users} tone="text-cyan-500" />
        <MetricCard label="Pending Replies" value={data.real_time_activity.pending_replies} sub="waiting for response" icon={Clock} tone="text-amber-500" />
        <MetricCard label="AI Active Chats" value={data.real_time_activity.ai_active_chats} sub="automation live" icon={Bot} tone="text-violet-500" />
        <MetricCard label="Human Active Chats" value={data.real_time_activity.human_active_chats} sub="manual handling" icon={HeartHandshake} tone="text-emerald-500" />
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 sm:gap-6">
        <div className="xl:col-span-5 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Activity size={16} className="text-blue-500" />
            <h2 className="text-sm font-semibold text-slate-900">Real-Time Activity</h2>
          </div>
          <div className="space-y-2">
            {(data.real_time_activity.live_feed || []).map((item) => (
              <button
                key={item.id}
                onClick={() => navigate(`/inbox?conversation=${encodeURIComponent(item.id)}`)}
                className="w-full text-left rounded-xl border border-slate-100 p-3 hover:border-blue-200 hover:bg-blue-50/30 transition-colors"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-slate-800 truncate">{item.customer_name || 'Conversation'}</p>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 capitalize">{item.channel?.replace('_', ' ')}</span>
                </div>
                <p className="text-xs text-slate-500 truncate mt-1">{item.last_message || 'No recent message'}</p>
              </button>
            ))}
            {!loading && (data.real_time_activity.live_feed || []).length === 0 && <p className="text-sm text-slate-400">No live conversation activity yet.</p>}
          </div>
        </div>

        <div className="xl:col-span-3 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <WalletCards size={16} className="text-cyan-500" />
            <h2 className="text-sm font-semibold text-slate-900">Inbox Summary</h2>
          </div>
          <div className="space-y-3">
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">Total Unread</p>
              <p className="text-2xl font-bold text-slate-900">{data.inbox_summary.total_unread}</p>
            </div>
            {Object.entries(data.inbox_summary.platform_counts || {}).map(([key, value]) => {
              const meta = channelMeta[key] || channelMeta.web_chat;
              const Icon = meta.icon;
              return (
                <div key={key} className="flex items-center justify-between">
                  <span className="inline-flex items-center gap-2 text-sm text-slate-700"><Icon size={14} /> {meta.label}</span>
                  <span className="text-sm font-semibold text-slate-900">{value}</span>
                </div>
              );
            })}
            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <span className="text-sm text-red-600 font-medium">Urgent chats</span>
              <span className="text-sm font-bold text-red-600">{data.inbox_summary.urgent_chats}</span>
            </div>
          </div>
        </div>

        <div className="xl:col-span-4 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Bot size={16} className="text-violet-500" />
            <h2 className="text-sm font-semibold text-slate-900">AI Performance Snapshot</h2>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">AI success today</p>
              <p className="text-xl font-bold text-slate-900">{data.ai_performance.ai_response_success_rate_today}%</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">Blocked / flagged</p>
              <p className="text-xl font-bold text-slate-900">{data.ai_performance.blocked_or_flagged_conversations}</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">AI avg response</p>
              <p className="text-xl font-bold text-slate-900">{data.ai_performance.avg_response_time_ai}</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">Human avg response</p>
              <p className="text-xl font-bold text-slate-900">{data.ai_performance.avg_response_time_human}</p>
            </div>
          </div>
          <div className="mt-4">
            <p className="text-xs text-slate-500 mb-2">Suggested replies pending approval</p>
            <p className="text-lg font-semibold text-slate-800">{data.ai_performance.suggested_replies_pending}</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-4 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Zap size={16} className="text-amber-500" />
            <h2 className="text-sm font-semibold text-slate-900">Leads Quick View</h2>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">New</p>
              <p className="text-xl font-bold text-slate-900">{data.leads_quick_view.new_leads_today}</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">Converted</p>
              <p className="text-xl font-bold text-slate-900">{data.leads_quick_view.converted_leads_today}</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] text-slate-400">Hot</p>
              <p className="text-xl font-bold text-slate-900">{data.leads_quick_view.hot_leads}</p>
            </div>
          </div>
          <div className="space-y-2">
            {(data.leads_quick_view.recent_lead_activity || []).map((lead) => (
              <button
                key={lead.id}
                onClick={() => navigate('/leads')}
                className="w-full text-left rounded-xl border border-slate-100 p-3 hover:border-amber-200 hover:bg-amber-50/30 transition-colors"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-slate-800 truncate">{lead.name}</p>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 uppercase">{lead.grade}</span>
                </div>
                <p className="text-xs text-slate-500 mt-1 truncate">{lead.company || lead.status || 'Lead activity'}</p>
              </button>
            ))}
          </div>
        </div>

        <div className="xl:col-span-5 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <AlertCircle size={16} className="text-red-500" />
            <h2 className="text-sm font-semibold text-slate-900">Alerts & Actions</h2>
          </div>
          <div className="space-y-3">
            {(data.alerts || []).map((alert) => (
              <div key={alert.id} className={`rounded-2xl border p-4 ${alert.severity === 'error' ? 'border-red-200 bg-red-50/70' : 'border-amber-200 bg-amber-50/70'}`}>
                <p className="text-sm font-semibold text-slate-900">{alert.title}</p>
                <p className="text-xs text-slate-600 mt-1">{alert.detail || 'Needs attention.'}</p>
                <div className="flex items-center gap-2 mt-3 flex-wrap">
                  {(alert.actions || []).map((action) => (
                    <button
                      key={`${alert.id}-${action.label}`}
                      onClick={() => handleAction(action)}
                      className="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {!loading && (data.alerts || []).length === 0 && <p className="text-sm text-slate-400">No critical alerts right now.</p>}
          </div>
        </div>

        <div className="xl:col-span-3 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <ShieldCheck size={16} className="text-emerald-500" />
            <h2 className="text-sm font-semibold text-slate-900">System Health</h2>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-700">Gemini API</span>
              <span className={`text-xs px-2 py-1 rounded-full ${data.system_health.gemini_api === 'online' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{data.system_health.gemini_api}</span>
            </div>
            {(data.system_health.channels || []).map((channel) => (
              <div key={channel.channel} className="flex items-center justify-between">
                <span className="text-sm text-slate-700">{channelMeta[channel.channel]?.label || channel.channel}</span>
                <span className={`text-xs px-2 py-1 rounded-full ${channel.enabled && channel.configured ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'}`}>
                  {channel.enabled && channel.configured ? 'online' : 'needs setup'}
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between pt-2 border-t border-slate-100">
              <span className="text-sm text-slate-700">Bulk upload</span>
              <span className={`text-xs px-2 py-1 rounded-full ${data.system_health.bulk_upload_status === 'failed' ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-700'}`}>{data.system_health.bulk_upload_status}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-4 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={16} className="text-blue-500" />
            <h2 className="text-sm font-semibold text-slate-900">Messages Last 24 Hours</h2>
          </div>
          <TinyBars items={messageBars.length ? messageBars : [{ label: 'Now', value: 0 }]} color="#2563eb" />
        </div>

        <div className="xl:col-span-4 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Sparkles size={16} className="text-emerald-500" />
            <h2 className="text-sm font-semibold text-slate-900">Sentiment Distribution</h2>
          </div>
          <div className="space-y-3">
            {sentimentBars.map((item) => {
              const pct = Math.round((item.value / totalSentiment) * 100);
              return (
                <div key={item.label}>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-slate-600">{item.label}</span>
                    <span className="font-medium text-slate-800">{item.value} ({pct}%)</span>
                  </div>
                  <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: item.color }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="xl:col-span-4 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Bot size={16} className="text-violet-500" />
            <h2 className="text-sm font-semibold text-slate-900">Response Rate Gauge</h2>
          </div>
          <div className="rounded-2xl bg-slate-50 p-5">
            <p className="text-4xl font-bold text-slate-900">{data.micro_visuals.response_rate_gauge}%</p>
            <p className="text-xs text-slate-500 mt-1">AI response rate today</p>
            <div className="mt-4 h-3 rounded-full bg-slate-200 overflow-hidden">
              <div className="h-full rounded-full bg-violet-500" style={{ width: `${Math.min(100, data.micro_visuals.response_rate_gauge || 0)}%` }} />
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white border border-slate-100 rounded-2xl p-5">
        <div className="flex items-center gap-2 mb-4">
          <Sparkles size={16} className="text-amber-500" />
          <h2 className="text-sm font-semibold text-slate-900">AI Insights</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {(data.insights || []).map((insight, idx) => (
            <div key={`${insight}-${idx}`} className="rounded-2xl bg-amber-50 border border-amber-200 p-4">
              <p className="text-sm font-medium text-amber-900">{insight}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
