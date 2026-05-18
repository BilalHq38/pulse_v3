import { CheckCircle2, ChevronRight, Layers, Mail, MessageSquareMore, Phone, UserRound } from 'lucide-react';
import TagBadge from '@/components/unification/TagBadge';
import { deriveTagKind } from '@/stores/useIdentityUnificationStore';

const formatCurrency = (value) => {
  const amount = Number(value || 0) || 0;
  return amount.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
};

const formatConfidence = (value) => {
  const score = Number(value || 0) || 0;
  return `${Math.round(Math.min(Math.max(score, 0), 1) * 100)}%`;
};

const channelLabel = (value) => {
  const text = String(value || '').trim();
  if (!text) return '';
  return text
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const profileChannels = (profile = {}) => {
  const values = Array.isArray(profile.source_channels) && profile.source_channels.length
    ? profile.source_channels
    : String(profile.platforms_used || '')
        .split(/[,\u2022/|]+/)
        .map((item) => item.trim());
  return [...new Set(values.map(channelLabel).filter(Boolean))];
};

export default function ProfileCard({
  profile,
  profileMeta,
  selected = false,
  onToggleSelect,
  onOpenDetail,
}) {
  const profileId = String(profile?.id || '').trim();
  const matchTag = deriveTagKind(profile, profileMeta);
  const confidence =
    Number(profileMeta?.confidenceScore || profile?.profile_confidence || profile?.confidence_score || 0) || 0;
  const reviewStatus = String(profileMeta?.reviewStatus || profile?.review_status || '').trim();
  const avatarUrl = String(profile?.avatar_url || '').trim();
  const initials = (String(profile?.display_name || '?').charAt(0) || '?').toUpperCase();
  const channels = profileChannels(profile);
  const primaryEmail = String(profile?.primary_email || profile?.email || '').trim();
  const primaryPhone = String(profile?.primary_phone || profile?.phone || '').trim();
  const linkedCount = channels.length || Number(profile?.member_count || 0) || 0;

  return (
    <article
      className={`group rounded-xl border px-3 py-2.5 transition-all duration-200 ${
        selected
          ? 'border-blue-400 bg-blue-50/80 shadow-sm'
          : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
      }`}
    >
      <div className="flex flex-wrap items-center gap-3">
        {avatarUrl ? (
          <img
            src={avatarUrl}
            alt=""
            className="h-10 w-10 shrink-0 rounded-lg border border-slate-200 object-cover"
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600 text-sm font-bold text-white">
            {initials}
          </div>
        )}

        <div className="min-w-[12rem] flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="max-w-[18rem] truncate text-sm font-semibold text-slate-900">{profile?.display_name || 'Unknown profile'}</h3>
            {channels.slice(0, 3).map((channel) => (
              <span
                key={channel}
                className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-700"
              >
                {channel}
              </span>
            ))}
            {channels.length > 3 ? (
              <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] text-slate-500">
                +{channels.length - 3}
              </span>
            ) : null}
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
            {primaryEmail ? (
              <span className="inline-flex min-w-0 items-center gap-1">
                <Mail size={11} className="shrink-0 text-slate-400" />
                <span className="truncate">{primaryEmail}</span>
              </span>
            ) : null}
            {primaryPhone ? (
              <span className="inline-flex min-w-0 items-center gap-1">
                <Phone size={11} className="shrink-0 text-slate-400" />
                <span className="truncate">{primaryPhone}</span>
              </span>
            ) : null}
            {!primaryEmail && !primaryPhone ? (
              <span className="text-slate-400">No primary contact</span>
            ) : null}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <TagBadge kind={matchTag} compact />
            {profile?.review_required ? <TagBadge kind="review_required" compact /> : null}
            {reviewStatus ? <TagBadge kind="unknown" label={`Review: ${reviewStatus}`} compact /> : null}
          </div>
        </div>

        <dl className="grid min-w-[15rem] grid-cols-3 gap-1.5 rounded-lg border border-slate-100 bg-slate-50/80 p-2">
          <div>
            <dt className="text-[9px] uppercase tracking-wide text-slate-400">Members</dt>
            <dd className="text-xs font-semibold text-slate-800">{Number(profile?.member_count || 0) || 0}</dd>
          </div>
          <div>
            <dt className="text-[9px] uppercase tracking-wide text-slate-400">Match</dt>
            <dd className="text-xs font-semibold text-slate-800">{formatConfidence(confidence)}</dd>
          </div>
          <div>
            <dt className="text-[9px] uppercase tracking-wide text-slate-400">LTV</dt>
            <dd className="text-xs font-semibold text-slate-800">{formatCurrency(profile?.lifetime_value)}</dd>
          </div>
        </dl>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-slate-500">
            <Layers size={12} className="shrink-0" />
            <span>{linkedCount} channel{linkedCount === 1 ? '' : 's'}</span>
          </div>

          {Array.isArray(profile?.all_conversations) && profile.all_conversations.length > 0 ? (
            <div className="flex items-center gap-1 text-[11px] text-slate-500">
              <MessageSquareMore size={12} className="text-indigo-500" />
              {profile.all_conversations.length}
            </div>
          ) : null}

          <button
            type="button"
            onClick={() => onToggleSelect?.(profileId)}
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold ${
              selected
                ? 'border-blue-300 bg-blue-100 text-blue-700'
                : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
            }`}
          >
            <CheckCircle2 size={12} />
            {selected ? 'Selected' : 'Select'}
          </button>
          <button
            type="button"
            onClick={() => onOpenDetail?.(profileId)}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
          >
            <UserRound size={12} />
            Details
            <ChevronRight size={12} className="opacity-70" />
          </button>
        </div>
      </div>
    </article>
  );
}
