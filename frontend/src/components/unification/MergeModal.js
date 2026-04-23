import { useMemo, useState } from 'react';
import { Link2, Plus, X } from 'lucide-react';

const normalizeId = (value) => String(value || '').trim();

const dedupe = (items) => {
  const unique = new Set();
  const output = [];
  items.forEach((item) => {
    const safe = normalizeId(item);
    if (!safe || unique.has(safe)) return;
    unique.add(safe);
    output.push(safe);
  });
  return output;
};

export default function MergeModal({
  isOpen,
  onClose,
  profiles,
  selectedIds,
  onToggleSelected,
  onClearSelection,
  onConfirm,
  actionInFlight = false,
}) {
  const [manualIdInput, setManualIdInput] = useState('');

  const manualIds = useMemo(() => {
    return dedupe(
      String(manualIdInput || '')
        .split(/[,\s\n\t]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    );
  }, [manualIdInput]);

  const candidateIds = useMemo(() => dedupe([...(selectedIds || []), ...manualIds]), [selectedIds, manualIds]);
  const canSubmit = candidateIds.length >= 2 && !actionInFlight;

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-2xl border border-slate-200 bg-white shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <header className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Manual Identity Merge</h2>
            <p className="mt-1 text-xs text-slate-500">
              Merge at least two unified profile IDs. This operation requires admin permissions and is audited.
            </p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
            <X size={16} />
          </button>
        </header>

        <div className="space-y-4 px-5 py-4">
          <section>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Selected Profiles</p>
              <button
                type="button"
                onClick={onClearSelection}
                className="text-[11px] font-medium text-slate-500 hover:text-slate-700"
                disabled={!selectedIds?.length}
              >
                Clear selection
              </button>
            </div>
            <div className="max-h-52 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
              {Array.isArray(profiles) && profiles.length > 0 ? (
                profiles.map((profile) => {
                  const profileId = normalizeId(profile?.id);
                  const selected = (selectedIds || []).includes(profileId);
                  return (
                    <label
                      key={profileId}
                      className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-xs ${
                        selected ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-200 bg-white text-slate-600'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => onToggleSelected?.(profileId)}
                        className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600"
                      />
                      <span className="min-w-0 flex-1 truncate font-medium">{profile?.display_name || profileId}</span>
                      <span className="font-mono text-[10px] text-slate-400">{profileId}</span>
                    </label>
                  );
                })
              ) : (
                <p className="px-2 py-5 text-center text-xs text-slate-500">No profiles loaded yet.</p>
              )}
            </div>
          </section>

          <section>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Additional IDs</p>
            <textarea
              rows={3}
              value={manualIdInput}
              onChange={(event) => setManualIdInput(event.target.value)}
              placeholder="Paste profile IDs separated by commas or new lines"
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono text-slate-700 outline-none ring-blue-300 transition focus:border-blue-300 focus:ring"
            />
            <p className="mt-1 text-[11px] text-slate-500">
              Parsed IDs: {manualIds.length > 0 ? manualIds.join(', ') : 'none'}
            </p>
          </section>

          <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-xs text-slate-600">Profiles queued for merge</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {candidateIds.length > 0 ? (
                candidateIds.map((id) => (
                  <span key={id} className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[10px] font-semibold text-slate-600">
                    {id}
                  </span>
                ))
              ) : (
                <span className="text-[11px] text-slate-500">Select or add at least two profile IDs.</span>
              )}
            </div>
          </section>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-4">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-100">
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => onConfirm?.(candidateIds)}
            className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {actionInFlight ? (
              <>
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Merging...
              </>
            ) : (
              <>
                <Plus size={12} />
                <Link2 size={12} />
                Merge Profiles
              </>
            )}
          </button>
        </footer>
      </div>
    </div>
  );
}
