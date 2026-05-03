import { CheckCircle2, ChevronRight, Layers, MessageSquareMore, UserRound } from 'lucide-react';
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

  return (
    <article
      className={`group rounded-2xl border p-4 transition-all duration-200 ${
        selected
          ? 'border-blue-400 bg-blue-50/70 shadow-sm'
          : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start gap-3">
        {avatarUrl ? (
          <img
            src={avatarUrl}
            alt=""
            className="h-11 w-11 shrink-0 rounded-xl border border-slate-200 object-cover"
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : (
          <div className="h-11 w-11 shrink-0 rounded-xl bg-gradient-to-br from-sky-500 to-indigo-600 text-white flex items-center justify-center text-sm font-bold">
            {initials}
          </div>
        )}

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-slate-900">{profile?.display_name || 'Unknown profile'}</h3>
              <p className="truncate text-[11px] text-slate-500">{profileId || 'missing-profile-id'}</p>
            </div>

            <button
              type="button"
              onClick={() => onToggleSelect?.(profileId)}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[10px] font-semibold ${
                selected
                  ? 'border-blue-300 bg-blue-100 text-blue-700'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
              }`}
            >
              <CheckCircle2 size={11} />
              {selected ? 'Selected' : 'Select'}
            </button>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <TagBadge kind={matchTag} compact />
            {profile?.review_required ? <TagBadge kind="review_required" compact /> : null}
            {reviewStatus ? <TagBadge kind="unknown" label={`Review: ${reviewStatus}`} compact /> : null}
          </div>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2 rounded-xl border border-slate-100 bg-slate-50/70 p-3">
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-slate-400">Members</dt>
          <dd className="mt-1 text-sm font-semibold text-slate-800">{Number(profile?.member_count || 0) || 0}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-slate-400">Confidence</dt>
          <dd className="mt-1 text-sm font-semibold text-slate-800">{formatConfidence(confidence)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-slate-400">LTV</dt>
          <dd className="mt-1 text-sm font-semibold text-slate-800">{formatCurrency(profile?.lifetime_value)}</dd>
        </div>
      </dl>

      <div className="mt-4 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-[11px] text-slate-500">
          <Layers size={12} className="shrink-0" />
          <span className="truncate">{profile?.platforms_used || 'Platform signals unavailable'}</span>
        </div>

        <button
          type="button"
          onClick={() => onOpenDetail?.(profileId)}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
        >
          <UserRound size={12} />
          Profile
          <ChevronRight size={12} className="opacity-70" />
        </button>
      </div>

      {Array.isArray(profile?.all_conversations) && profile.all_conversations.length > 0 ? (
        <div className="mt-3 flex items-center gap-1 text-[11px] text-slate-500">
          <MessageSquareMore size={12} className="text-indigo-500" />
          {profile.all_conversations.length} linked conversations
        </div>
      ) : null}
    </article>
  );
}
