import { useMemo, useState } from 'react';
import { Clock3, Eye, GitMerge, ShieldAlert, X } from 'lucide-react';

const normalize = (value) => String(value || '').trim();

const scoreColor = (score) => {
  const numeric = Number(score || 0) || 0;
  if (numeric >= 0.85) return 'bg-emerald-100 text-emerald-700 border-emerald-200';
  if (numeric >= 0.65) return 'bg-orange-100 text-orange-700 border-orange-200';
  return 'bg-slate-100 text-slate-600 border-slate-200';
};

const scoreLabel = (score) => `${Math.round(Math.min(Math.max(Number(score || 0) || 0, 0), 1) * 100)}%`;

const SIGNAL_LABELS = {
  phone: 'Phone',
  phone_normalized: 'Phone',
  email: 'Email',
  email_normalized: 'Email',
  name: 'Name',
  name_similarity: 'Name',
  profile_picture: 'Avatar',
  profile_picture_url: 'Avatar',
  channel_identity: 'Channel Identity',
  description: 'Description',
  description_similarity: 'Description',
  company: 'Company',
  company_similarity: 'Company',
  device_fingerprint: 'Device',
  ip_subnet: 'Location',
};

const signalLabel = (signal) => {
  const key = String(signal || '').trim().toLowerCase();
  if (SIGNAL_LABELS[key]) return SIGNAL_LABELS[key];
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const asArray = (value) => (Array.isArray(value) ? value : []);

const legacyCandidate = (item, side) => ({
  customer_id: item?.[`customer_id_${side}`] || '',
  display_name: item?.[`name_${side}`] || item?.[`customer_id_${side}`] || 'Unknown profile',
  email: item?.[`email_${side}`] || '',
  phone: item?.[`phone_${side}`] || '',
  avatar_url: item?.[`avatar_${side}`] || '',
  source_channels: item?.[`channels_${side}`] || [],
  company_name: item?.[`company_${side}`] || item?.[`customer_company_name_${side}`] || '',
});

function CandidateCard({ candidate, label }) {
  const profile = candidate || {};
  const name = normalize(profile.display_name || profile.name) || 'Unknown profile';
  const id = normalize(profile.customer_id || profile.id);
  const avatarUrl = normalize(profile.avatar_url);
  const channels = asArray(profile.source_channels).filter(Boolean);
  const description = normalize(profile.description || profile.bio);
  const initials = (name.charAt(0) || '?').toUpperCase();

  return (
    <div className="min-w-0 rounded-xl border border-slate-200 bg-white p-4">
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
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-800 text-sm font-bold text-white">
            {initials}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</p>
          <p className="truncate text-sm font-semibold text-slate-900">{name}</p>
          <p className="truncate font-mono text-[10px] text-slate-400">{id || 'missing-id'}</p>
        </div>
      </div>

      <div className="mt-3 space-y-1.5 text-[11px] text-slate-600">
        <p className="truncate">
          <span className="font-semibold text-slate-500">Email:</span> {profile.email || 'not available'}
        </p>
        <p className="truncate">
          <span className="font-semibold text-slate-500">Phone:</span> {profile.phone || 'not available'}
        </p>
        {profile.company_name ? (
          <p className="truncate">
            <span className="font-semibold text-slate-500">Company:</span> {profile.company_name}
          </p>
        ) : null}
        {description ? (
          <p className="line-clamp-2">
            <span className="font-semibold text-slate-500">Bio:</span> {description}
          </p>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {channels.length > 0 ? (
          channels.map((channel) => (
            <span
              key={channel}
              className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-700"
            >
              {signalLabel(channel)}
            </span>
          ))
        ) : (
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] text-slate-500">
            Channel unknown
          </span>
        )}
      </div>
    </div>
  );
}

export default function ReviewQueuePanel({
  queue,
  loading = false,
  actionInFlight = false,
  isAdmin = false,
  onResolve,
}) {
  const [noteMap, setNoteMap] = useState({});
  const [expandedMap, setExpandedMap] = useState({});

  const pendingQueue = useMemo(() => {
    const list = Array.isArray(queue) ? queue : [];
    return [...list].sort((a, b) => Number(b?.match_score || 0) - Number(a?.match_score || 0));
  }, [queue]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <header className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Merge Candidates</h3>
          <p className="mt-1 text-xs text-slate-500">Review likely duplicate identities and confirm only safe merges.</p>
        </div>
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700">
          <Clock3 size={12} />
          {pendingQueue.length} pending
        </span>
      </header>

      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((item) => (
            <div key={item} className="h-40 animate-pulse rounded-xl border border-slate-200 bg-slate-100" />
          ))}
        </div>
      ) : pendingQueue.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-8 text-center text-xs text-slate-500">
          No pending suggestions. Run auto-detect to refresh potential matches.
        </div>
      ) : (
        <div className="max-h-[44rem] space-y-4 overflow-y-auto pr-0.5">
          {pendingQueue.map((item) => {
            const id = normalize(item?.id || item?.review_id);
            const score = Number(item?.match_score || item?.confidence || 0) || 0;
            const notes = noteMap[id] || '';
            const disabled = actionInFlight || !isAdmin;
            const matchedFields = asArray(item?.matched_fields).length
              ? asArray(item?.matched_fields)
              : normalize(item?.match_reasons)
                .split(',')
                .map((signal) => signal.trim())
                .filter(Boolean);
            const candidateA = item?.candidate_a || legacyCandidate(item, 'a');
            const candidateB = item?.candidate_b || legacyCandidate(item, 'b');
            const expanded = Boolean(expandedMap[id]);

            return (
              <article key={id} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4" data-testid={`merge-candidate-${id}`}>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-semibold text-slate-900">Possible identity match</p>
                    <p className="mt-0.5 text-[11px] text-slate-500">Detected from shared identity signals</p>
                  </div>
                  <span className={`rounded-full border px-3 py-1.5 text-xs font-bold ${scoreColor(score)}`}>
                    {scoreLabel(score)} match
                  </span>
                </div>

                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr),auto,minmax(0,1fr)]">
                  <CandidateCard candidate={candidateA} label="Profile A" />
                  <div className="hidden items-center justify-center px-1 text-[11px] font-semibold text-slate-400 md:flex">
                    vs
                  </div>
                  <CandidateCard candidate={candidateB} label="Profile B" />
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                  {matchedFields.length > 0 ? (
                    matchedFields.map((field) => (
                      <span
                        key={field}
                        className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-700"
                      >
                        {signalLabel(field)}
                      </span>
                    ))
                  ) : (
                    <span className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[10px] text-slate-500">
                      Signal-based suggestion
                    </span>
                  )}
                </div>

                {expanded ? (
                  <div className="mt-3 rounded-xl border border-slate-200 bg-white p-3 text-[11px] text-slate-600">
                    <p className="font-semibold text-slate-700">Review notes</p>
                    <p className="mt-1">{item?.match_reasons || 'No additional reason text provided.'}</p>
                  </div>
                ) : null}

                <textarea
                  value={notes}
                  onChange={(event) =>
                    setNoteMap((prev) => ({
                      ...prev,
                      [id]: event.target.value,
                    }))
                  }
                  placeholder="Optional reviewer notes"
                  className="mt-3 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[11px] text-slate-700 outline-none ring-blue-300 transition focus:border-blue-300 focus:ring"
                  rows={2}
                  disabled={!isAdmin}
                />

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                  {!isAdmin ? (
                    <span className="inline-flex items-center gap-1 text-[11px] text-amber-700">
                      <ShieldAlert size={12} />
                      Admin role required for review decisions
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400">Decisions update the identity review history.</span>
                  )}

                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setExpandedMap((prev) => ({ ...prev, [id]: !prev[id] }))}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-100"
                    >
                      <Eye size={12} />
                      Review
                    </button>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onResolve?.({ suggestionId: id, action: 'accept', notes })}
                      className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <GitMerge size={12} />
                      Merge
                    </button>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onResolve?.({ suggestionId: id, action: 'reject', notes })}
                      className="inline-flex items-center gap-1 rounded-lg bg-slate-200 px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <X size={12} />
                      Skip
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
