import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, FileSpreadsheet, Upload, X } from 'lucide-react';

function escapeCsvValue(value) {
  const text = String(value ?? '');
  if (!/[",\n]/.test(text)) return text;
  return `"${text.replace(/"/g, '""')}"`;
}

export default function BulkUploadModal({
  isOpen,
  onClose,
  title,
  subtitle,
  entityLabel,
  uploading,
  onUpload,
  templateHeaders,
  templateSample,
  guideRows,
}) {
  const [selectedFile, setSelectedFile] = useState(null);

  useEffect(() => {
    if (!isOpen) setSelectedFile(null);
  }, [isOpen]);

  // Prevent body scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const templateCsv = useMemo(() => {
    const headerLine = templateHeaders.map(escapeCsvValue).join(',');
    const sampleLine = templateSample.map(escapeCsvValue).join(',');
    return `${headerLine}\n${sampleLine}\n`;
  }, [templateHeaders, templateSample]);

  if (!isOpen) return null;

  const downloadTemplate = () => {
    const blob = new Blob([templateCsv], { type: 'text/csv;charset=utf-8' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${entityLabel.toLowerCase()}-bulk-upload-template.csv`;
    link.click();
    window.URL.revokeObjectURL(url);
  };

  const submitUpload = async () => {
    if (!selectedFile || uploading) return;
    await onUpload(selectedFile);
  };

  const handleBackdropClick = () => {
    if (!uploading) onClose();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 backdrop-blur-sm p-4"
      onClick={handleBackdropClick}
    >
      <div
        className="relative w-full max-w-4xl max-h-[92vh] rounded-xl border border-slate-200 bg-white shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* HEADER — fixed, never scrolls */}
        <div className="flex-none flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-blue-700">
              <FileSpreadsheet size={12} /> Bulk Upload
            </p>
            <h3 className="mt-3 text-xl font-bold text-slate-900">{title}</h3>
            <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={uploading}
            className="flex-none rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* SCROLLABLE CONTENT */}
        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
          <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">

            {/* LEFT — Spreadsheet guide */}
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-5 hover:border-blue-200 transition-colors">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h4 className="text-sm font-semibold text-slate-900">Spreadsheet Guide</h4>
                    <p className="mt-1 text-xs text-slate-500">
                      Upload .xlsx, .xls, or .csv files. One record per row.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={downloadTemplate}
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:border-blue-200 hover:text-blue-700 transition-colors"
                  >
                    <Download size={14} /> Download Template
                  </button>
                </div>

                <div className="mt-4 rounded-xl border border-slate-200 bg-white overflow-hidden">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 border-b border-slate-200">
                      <tr>
                        <th className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Column</th>
                        <th className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">What to enter</th>
                      </tr>
                    </thead>
                    <tbody>
                      {guideRows.map((row) => (
                        <tr key={row.column} className="border-t border-slate-100 align-top">
                          <td className="px-4 py-3 font-medium text-slate-800 whitespace-nowrap">{row.column}</td>
                          <td className="px-4 py-3 text-slate-500">{row.help}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-5">
                <h4 className="text-sm font-semibold text-emerald-900">What happens on upload</h4>
                <p className="mt-2 text-sm text-emerald-800">
                  Existing {entityLabel.toLowerCase()}s are matched by phone first, then email.
                  Matching rows update instead of creating duplicates.
                </p>
              </div>
            </div>

            {/* RIGHT — File picker */}
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-200 bg-white p-5 hover:border-blue-200 transition-colors">
                <h4 className="text-sm font-semibold text-slate-900">Choose File</h4>

                <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 bg-slate-50 px-5 py-10 text-center hover:border-blue-300 hover:bg-blue-50/40 transition-colors">
                  <Upload size={28} className="text-slate-400" />
                  <span className="mt-3 text-sm font-medium text-slate-700">
                    {selectedFile ? selectedFile.name : 'Select an Excel or CSV file'}
                  </span>
                  <span className="mt-1 text-xs text-slate-500">
                    Supported formats: .xlsx, .xls, .csv
                  </span>
                  <input
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    className="hidden"
                    onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                  />
                </label>

                <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-xs text-slate-500">
                  Required minimum: name, phone, or email.
                </div>

                <div className="mt-5 flex gap-3">
                  <button
                    type="button"
                    onClick={onClose}
                    disabled={uploading}
                    className="flex-1 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:border-blue-200 hover:text-blue-700 disabled:opacity-50 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={submitUpload}
                    disabled={!selectedFile || uploading}
                    className="flex-1 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60 transition-colors"
                  >
                    {uploading ? 'Uploading...' : `Upload ${entityLabel}s`}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}