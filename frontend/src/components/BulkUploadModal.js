import { useEffect, useMemo, useState } from 'react';
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
    if (!isOpen) {
      setSelectedFile(null);
    }
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-3xl rounded-2xl border border-slate-200 bg-white shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-blue-700">
              <FileSpreadsheet size={12} /> Bulk Upload
            </p>
            <h3 className="mt-3 text-xl font-bold text-slate-900">{title}</h3>
            <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>

        <div className="grid gap-6 px-6 py-6 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="space-y-5">
            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-slate-900">Spreadsheet Guide</h4>
                  <p className="mt-1 text-xs text-slate-500">Upload `.xlsx`, `.xls`, or `.csv` files. One record per row.</p>
                </div>
                <button
                  type="button"
                  onClick={downloadTemplate}
                  className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
                >
                  <Download size={14} /> Download Template
                </button>
              </div>

              <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Column</th>
                      <th className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">What to enter</th>
                    </tr>
                  </thead>
                  <tbody>
                    {guideRows.map((row) => (
                      <tr key={row.column} className="border-t border-slate-100 align-top">
                        <td className="px-4 py-3 font-medium text-slate-800">{row.column}</td>
                        <td className="px-4 py-3 text-slate-500">{row.help}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="rounded-2xl border border-emerald-100 bg-emerald-50/60 p-5">
              <h4 className="text-sm font-semibold text-emerald-900">What happens on upload</h4>
              <p className="mt-2 text-sm leading-6 text-emerald-800">
                Existing {entityLabel.toLowerCase()}s are matched by phone first, then email. Matching rows update the current record instead of creating a duplicate.
              </p>
            </div>
          </div>

          <div className="space-y-5">
            <div className="rounded-2xl border border-slate-200 bg-white p-5">
              <h4 className="text-sm font-semibold text-slate-900">Choose File</h4>
              <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 px-5 py-10 text-center transition-colors hover:border-blue-300 hover:bg-blue-50/40">
                <Upload size={28} className="text-slate-400" />
                <span className="mt-3 text-sm font-medium text-slate-700">
                  {selectedFile ? selectedFile.name : 'Select an Excel or CSV file'}
                </span>
                <span className="mt-1 text-xs text-slate-500">Supported formats: `.xlsx`, `.xls`, `.csv`</span>
                <input
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  className="hidden"
                  onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
                />
              </label>

              <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
                Required minimum: name, phone, or email for each {entityLabel.toLowerCase()} row.
              </div>

              <div className="mt-5 flex gap-3">
                <button
                  type="button"
                  onClick={onClose}
                  className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={submitUpload}
                  disabled={!selectedFile || uploading}
                  className="flex-1 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {uploading ? 'Uploading...' : `Upload ${entityLabel}s`}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
