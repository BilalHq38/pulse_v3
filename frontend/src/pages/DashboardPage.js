import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { useSocket } from '@/lib/useSocket';
import api from '@/lib/api';
import {
  Activity, AlertCircle, Bot, Clock, Facebook, HeartHandshake, Instagram,
  Mail, MessageSquare, RefreshCw, ShieldCheck, Sparkles, TrendingUp, Zap
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
  system_health: { channels: [], server_time: '' },
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

function MetricCard({ label, value, sub, icon: Icon, tone = 'text-slate-500', onClick }) {
  const bgMap = {
    'text-blue-500': 'bg-blue-50',
    'text-cyan-500': 'bg-cyan-50',
    'text-amber-500': 'bg-amber-50',
    'text-violet-500': 'bg-violet-50',
    'text-emerald-500': 'bg-emerald-50',
  };
  const content = (
    <>
      <div className="flex items-center justify-between mb-2 sm:mb-3">
        <div className={`w-8 h-8 rounded-xl ${bgMap[tone] || 'bg-slate-50'} flex items-center justify-center`}>
          <Icon size={16} className={tone} />
        </div>
      </div>
      <p className="text-xl sm:text-2xl font-bold text-slate-900 tabular-nums">{value}</p>
      <p className="text-[11px] sm:text-xs font-semibold text-slate-700 mt-1">{label}</p>
      <p className="text-[10px] sm:text-[11px] text-slate-400 mt-0.5 line-clamp-1">{sub}</p>
    </>
  );
  const className = `w-full bg-white border border-slate-100 rounded-2xl p-3 sm:p-4 pe-card transition-all duration-200 ${
    onClick ? 'hover:shadow-md hover:border-blue-200 cursor-pointer text-left focus:outline-none focus:ring-2 focus:ring-blue-500/20' : 'hover:shadow-md'
  }`;
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className} aria-label={`Open inbox filtered by ${label}`}>
        {content}
      </button>
    );
  }
  return (
    <div className={className}>
      {content}
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

// FIX: derive channel online status from `enabled` alone.
// The `configured` field returned by /dashboard/live-summary is unreliable —
// the backend may not set it (undefined → falsy) even for fully-set-up channels.
// Using `enabled` as the single source of truth matches the backend's persisted state.
// If the backend is later fixed to reliably populate `configured`, you can restore
// the conjunction: channel.enabled && channel.configured
function isChannelOnline(channel) {
  return Boolean(channel.enabled);
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [data, setData] = useState(DEFAULT_DATA);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [companyName, setCompanyName] = useState(() => localStorage.getItem('pe_company_name') || '');

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

  useEffect(() => {
    const onCompanyChanged = (event) => {
      const nextName = String(
        event.type === 'storage'
          ? localStorage.getItem('pe_company_name') || ''
          : event.detail?.company_name || event.detail || '',
      ).trim();
      setCompanyName(nextName);
    };
    window.addEventListener('pe-company-changed', onCompanyChanged);
    window.addEventListener('storage', onCompanyChanged);
    return () => {
      window.removeEventListener('pe-company-changed', onCompanyChanged);
      window.removeEventListener('storage', onCompanyChanged);
    };
  }, []);

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

  const openInboxFilter = (filter) => {
    navigate(`/inbox?inbox_filter=${encodeURIComponent(filter)}`);
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
  const dashboardTitle = companyName || user?.company_name || user?.company?.name || 'Dashboard';

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-5 sm:space-y-6 animate-fadeIn" data-testid="dashboard-page">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-slate-900 flex items-center gap-2">
            <Zap size={20} className="text-blue-500" />
            {dashboardTitle}
          </h1>
        </div>
        <button
          onClick={() => fetchLive(false)}
          disabled={refreshing}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 border border-slate-200 rounded-xl px-3 py-1.5 bg-white disabled:opacity-50"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Real-time activity metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3 sm:gap-4">
        <MetricCard label="Incoming Messages" value={data.real_time_activity.incoming_messages} sub="messages today" icon={MessageSquare} tone="text-blue-500" onClick={() => openInboxFilter('incoming_messages')} />
        <MetricCard label="Active Conversations" value={data.real_time_activity.active_conversations} sub="open conversations" icon={Activity} tone="text-cyan-500" onClick={() => openInboxFilter('active_conversations')} />
        <MetricCard label="Pending Replies" value={data.real_time_activity.pending_replies} sub="awaiting response" icon={Clock} tone="text-amber-500" onClick={() => openInboxFilter('pending_replies')} />
        <MetricCard label="AI Chat" value={data.real_time_activity.ai_active_chats} sub="handled by AI" icon={Bot} tone="text-violet-500" onClick={() => openInboxFilter('ai_chats')} />
        <MetricCard label="Human Chats" value={data.real_time_activity.human_active_chats} sub="handled by agents" icon={HeartHandshake} tone="text-emerald-500" onClick={() => openInboxFilter('human_chats')} />
        <MetricCard label="Unread" value={data.inbox_summary.total_unread} sub="in inbox" icon={AlertCircle} tone="text-blue-500" onClick={() => openInboxFilter('unread')} />
      </div>

      {/* Alerts + System Health */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-9 bg-white border border-slate-100 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <AlertCircle size={16} className="text-red-500" />
            <h2 className="text-sm font-semibold text-slate-900">Alerts</h2>
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
            {(data.system_health.channels || []).map((channel) => {
              // FIX (line 332-333): was `channel.enabled && channel.configured` which always
              // evaluates to false when backend omits `configured` (undefined is falsy).
              // Now derived via isChannelOnline() which uses `enabled` only as the source of truth.
              const online = isChannelOnline(channel);
              return (
                <div key={channel.channel} className="flex items-center justify-between">
                  <span className="text-sm text-slate-700">{channelMeta[channel.channel]?.label || channel.channel}</span>
                  <span className={`text-xs px-2 py-1 rounded-full ${online ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'}`}>
                    {online ? 'online' : 'needs setup'}
                  </span>
                </div>
              );
            })}
            {!loading && (data.system_health.channels || []).length === 0 && (
              <p className="text-sm text-slate-400">No channel health data yet.</p>
            )}
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
            <p className="text-4xl font-bold text-slate-900">{Math.min(100, data.micro_visuals.response_rate_gauge || 0)}%</p>
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
