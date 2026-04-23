import { useMemo, useState } from 'react';
import { Check, Clock3, ShieldAlert, X } from 'lucide-react';

const normalize = (value) => String(value || '').trim();

const scoreColor = (score) => {
  const numeric = Number(score || 0) || 0;
  if (numeric >= 0.85) return 'bg-emerald-100 text-emerald-700 border-emerald-200';
  if (numeric >= 0.65) return 'bg-orange-100 text-orange-700 border-orange-200';
  return 'bg-slate-100 text-slate-600 border-slate-200';
};

export default function ReviewQueuePanel({
  queue,
  loading = false,
  actionInFlight = false,
  isAdmin = false,
  onResolve,
}) {
  const [noteMap, setNoteMap] = useState({});

  const pendingQueue = useMemo(() => {
    const list = Array.isArray(queue) ? queue : [];
    return [...list].sort((a, b) => Number(b?.match_score || 0) - Number(a?.match_score || 0));
  }, [queue]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4">
      <header className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Review Queue</h3>
          <p className="mt-1 text-xs text-slate-500">Pending cross-platform suggestions that need explicit decisions.</p>
        </div>
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700">
          <Clock3 size={12} />
          {pendingQueue.length} pending
        </span>
      </header>

      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((item) => (
            <div key={item} className="h-20 animate-pulse rounded-xl border border-slate-200 bg-slate-100" />
          ))}
        </div>
      ) : pendingQueue.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-8 text-center text-xs text-slate-500">
          No pending suggestions. Run auto-detect to refresh potential matches.
        </div>
      ) : (
        <div className="max-h-[34rem] space-y-2 overflow-y-auto pr-0.5">
          {pendingQueue.map((item) => {
            const id = normalize(item?.id || item?.review_id);
            const score = Number(item?.match_score || 0) || 0;
            const scoreLabel = `${Math.round(Math.min(Math.max(score, 0), 1) * 100)}%`;
            const notes = noteMap[id] || '';
            const disabled = actionInFlight || !isAdmin;

            return (
              <article key={id} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-xs font-semibold text-slate-800">
                        {item?.name_a || item?.customer_id_a || 'Unknown'}
                      </p>
                      <span className="text-[11px] text-slate-400">vs</span>
                      <p className="truncate text-xs font-semibold text-slate-800">
                        {item?.name_b || item?.customer_id_b || 'Unknown'}
                      </p>
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      {item?.match_reasons || 'Signal-based suggestion'}
                    </p>
                  </div>
                  <span className={`rounded-full border px-2 py-1 text-[10px] font-bold ${scoreColor(score)}`}>{scoreLabel}</span>
                </div>

                <div className="mt-2 grid grid-cols-2 gap-2 text-[10px] text-slate-500">
                  <span className="truncate">A: {item?.customer_id_a || 'n/a'}</span>
                  <span className="truncate">B: {item?.customer_id_b || 'n/a'}</span>
                </div>

                <textarea
                  value={notes}
                  onChange={(event) =>
                    setNoteMap((prev) => ({
                      ...prev,
                      [id]: event.target.value,
                    }))
                  }
                  placeholder="Optional reviewer notes"
                  className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[11px] text-slate-700 outline-none ring-blue-300 transition focus:border-blue-300 focus:ring"
                  rows={2}
                  disabled={!isAdmin}
                />

                <div className="mt-2 flex items-center justify-between gap-2">
                  {!isAdmin ? (
                    <span className="inline-flex items-center gap-1 text-[11px] text-amber-700">
                      <ShieldAlert size={12} />
                      Admin role required for review decisions
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400">Decision is written to merge review audit history.</span>
                  )}

                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onResolve?.({ suggestionId: id, action: 'accept', notes })}
                      className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Check size={12} />
                      Accept
                    </button>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onResolve?.({ suggestionId: id, action: 'reject', notes })}
                      className="inline-flex items-center gap-1 rounded-lg bg-slate-200 px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <X size={12} />
                      Reject
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
