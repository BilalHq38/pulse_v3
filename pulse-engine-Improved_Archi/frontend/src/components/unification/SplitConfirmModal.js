import { createPortal } from 'react-dom';
import { X, AlertTriangle } from 'lucide-react';

export default function SplitConfirmModal({
  isOpen,
  onClose,
  onConfirm,
  count,
  loading,
}) {
  if (!isOpen) return null;

  return createPortal(
    // Backdrop — fixed, full viewport, closes on outside click
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      {/* Modal panel — stopPropagation so clicking inside doesn't close */}
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-2xl border border-slate-200"
        onClick={(e) => e.stopPropagation()}
      >
        {/* HEADER */}
        <div className="flex justify-between items-start p-5 border-b border-slate-100">
          <div>
            <h3 className="text-lg font-semibold text-slate-900">
              Split Identity Signals
            </h3>
            <p className="text-sm text-slate-500 mt-1">
              This will create a new profile.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* BODY */}
        <div className="p-5 flex gap-3">
          <AlertTriangle className="text-amber-500 mt-0.5 shrink-0" size={18} />
          <p className="text-sm text-slate-700">
            You are about to move <b>{count}</b> selected signal{count !== 1 ? 's' : ''} into a new
            profile. This action can be reversed later by merging the profiles back together.
          </p>
        </div>

        {/* FOOTER */}
        <div className="flex justify-end gap-3 p-5 border-t border-slate-100">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 rounded-lg border border-slate-200 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className="px-4 py-2 rounded-lg bg-rose-600 text-white text-sm font-medium hover:bg-rose-500 disabled:opacity-60 transition-colors"
          >
            {loading ? 'Splitting...' : 'Split profile'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}