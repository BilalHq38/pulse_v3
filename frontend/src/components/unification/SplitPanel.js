import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Fingerprint, RefreshCw, Scissors, Unplug } from 'lucide-react';

const normalizeId = (value) => String(value || '').trim();

const formatDate = (value) => {
  if (!value) return 'n/a';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'n/a';
  return date.toLocaleString();
};

export default function SplitPanel({
  profileDetail,
  loading = false,
  actionInFlight = false,
  isAdmin = false,
  onRefresh,
  onSubmitSplit,
}) {
  const profileId = normalizeId(profileDetail?.id || profileDetail?.customer_id);
  const mappings = Array.isArray(profileDetail?.mappings) ? profileDetail.mappings : [];
  const fingerprints = Array.isArray(profileDetail?.fingerprints) ? profileDetail.fingerprints : [];

  const [mappingSelection, setMappingSelection] = useState([]);
  const [fingerprintSelection, setFingerprintSelection] = useState([]);

  useEffect(() => {
    setMappingSelection([]);
    setFingerprintSelection([]);
  }, [profileId]);

  const selectedCount = mappingSelection.length + fingerprintSelection.length;

  const canSubmit = isAdmin && selectedCount > 0 && !actionInFlight && !!profileId;

  const selectionHint = useMemo(() => {
    if (selectedCount === 0) return 'Select at least one mapping or fingerprint to split.';
    if (!isAdmin) return 'Only admins can execute split operations.';
    return `${selectedCount} identity signals selected.`;
  }, [selectedCount, isAdmin]);

  const toggleSelection = (value, current, setter) => {
    const safe = normalizeId(value);
    if (!safe) return;
    setter(current.includes(safe) ? current.filter((item) => item !== safe) : [...current, safe]);
  };

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4">
      <header className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Split Profile Signals</h3>
          <p className="mt-1 text-xs text-slate-500">Detach identity mappings or fingerprints from the selected profile.</p>
        </div>
        <button
          type="button"
          onClick={() => profileId && onRefresh?.(profileId)}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
          disabled={!profileId || loading || actionInFlight}
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </header>

      {!profileId ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-8 text-center text-xs text-slate-500">
          Select a profile card to inspect mappings and split signals.
        </div>
      ) : (
        <>
          <div className="mb-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-[11px] font-semibold text-slate-700">Profile: {profileDetail?.display_name || profileId}</p>
            <p className="mt-1 break-all font-mono text-[10px] text-slate-500">{profileId}</p>
          </div>

          <div className="space-y-3">
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Identity Mappings</p>
              <div className="max-h-48 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
                {mappings.length > 0 ? (
                  mappings.map((item) => {
                    const mappingId = normalizeId(item.mapping_id);
                    const selected = mappingSelection.includes(mappingId);
                    return (
                      <label
                        key={mappingId}
                        className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 ${
                          selected ? 'border-rose-300 bg-rose-50' : 'border-slate-200 bg-white'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!isAdmin}
                          onChange={() => toggleSelection(mappingId, mappingSelection, setMappingSelection)}
                          className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 text-rose-600"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-semibold text-slate-700">{item.platform || 'unknown'} / {item.platform_user_id || 'n/a'}</p>
                          <p className="truncate font-mono text-[10px] text-slate-500">{mappingId}</p>
                          <p className="mt-1 text-[10px] text-slate-500">Confidence: {Math.round((Number(item.confidence || 0) || 0) * 100)}%</p>
                        </div>
                      </label>
                    );
                  })
                ) : (
                  <p className="px-2 py-5 text-center text-xs text-slate-500">No mappings available.</p>
                )}
              </div>
            </div>

            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Device Fingerprints</p>
              <div className="max-h-48 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
                {fingerprints.length > 0 ? (
                  fingerprints.map((item) => {
                    const fingerprintId = normalizeId(item.fingerprint_id);
                    const selected = fingerprintSelection.includes(fingerprintId);
                    return (
                      <label
                        key={fingerprintId}
                        className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 ${
                          selected ? 'border-rose-300 bg-rose-50' : 'border-slate-200 bg-white'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!isAdmin}
                          onChange={() => toggleSelection(fingerprintId, fingerprintSelection, setFingerprintSelection)}
                          className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 text-rose-600"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-semibold text-slate-700">{item.fingerprint_hash || 'fingerprint'}</p>
                          <p className="truncate font-mono text-[10px] text-slate-500">{fingerprintId}</p>
                          <p className="mt-1 text-[10px] text-slate-500">Seen: {formatDate(item.last_seen)}</p>
                        </div>
                        <Fingerprint size={14} className="mt-0.5 text-slate-400" />
                      </label>
                    );
                  })
                ) : (
                  <p className="px-2 py-5 text-center text-xs text-slate-500">No fingerprints available.</p>
                )}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle size={14} className="mt-0.5 text-amber-500" />
              <div>
                <p className="text-xs font-medium text-slate-700">{selectionHint}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  Selected mapping IDs: {mappingSelection.length} | Selected fingerprints: {fingerprintSelection.length}
                </p>
              </div>
            </div>

            <button
              type="button"
              disabled={!canSubmit}
              onClick={() =>
                onSubmitSplit?.({
                  profileId,
                  mappingIds: mappingSelection,
                  fingerprintIds: fingerprintSelection,
                })
              }
              className="mt-3 inline-flex items-center gap-1 rounded-lg bg-rose-600 px-3 py-2 text-xs font-semibold text-white hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {actionInFlight ? (
                <>
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  Splitting...
                </>
              ) : (
                <>
                  <Scissors size={12} />
                  <Unplug size={12} />
                  Split Selected Signals
                </>
              )}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
