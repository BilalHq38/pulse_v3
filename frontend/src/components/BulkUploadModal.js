import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Download, FileSpreadsheet, Upload, X } from 'lucide-react';

function escapeCsvValue(value) {
  const text = String(value ?? '');
  if (!/[",\n]/.test(text)) return text;
  return `"${text.replace(/"/g, '""')}"`;
}

function formatFileSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const VALID_EXTENSIONS = ['.xlsx', '.xls', '.csv'];

function validateSpreadsheetFile(file) {
  if (!file) return 'No file selected.';
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!VALID_EXTENSIONS.includes(ext)) {
    return `Invalid file type "${ext}". Please choose an .xlsx, .xls, or .csv file.`;
  }
  return '';
}

const CheckIcon = () => (
  <svg className="w-5 h-5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
  </svg>
);

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
  requirementsText = 'Required minimum: name, phone, or email.',
}) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadSuccess, setUploadSuccess] = useState(false);
  const [fileError, setFileError] = useState('');
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (!isOpen) {
      setSelectedFile(null);
      setUploadSuccess(false);
      setFileError('');
    }
  }, [isOpen]);

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

  const handleFileChange = (e) => {
    const file = e.target.files?.[0] || null;
    if (!file) return;
    const error = validateSpreadsheetFile(file);
    if (error) {
      setFileError(error);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }
    setFileError('');
    setSelectedFile(file);
    setUploadSuccess(false);
  };

  const removeFile = () => {
    setSelectedFile(null);
    setFileError('');
    setUploadSuccess(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

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
    setUploadSuccess(true);
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
        className="relative w-full max-w-4xl max-h-[92vh] rounded-xl border border-slate-100 bg-white shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {/* Header */}
        <div className="flex-none flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-blue-700">
              <FileSpreadsheet size={12} aria-hidden="true" /> Bulk Upload
            </p>
            <h3 className="mt-3 text-xl font-bold text-slate-900">{title}</h3>
            <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={uploading}
            className="flex-none rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
            aria-label="Close dialog"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
          <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
            {/* Left column — spreadsheet guide */}
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-5 transition-colors hover:border-blue-200">
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
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-100 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:border-blue-200 hover:text-blue-700"
                  >
                    <Download size={14} aria-hidden="true" /> Download Template
                  </button>
                </div>

                <div className="mt-4 rounded-xl border border-slate-100 bg-white overflow-hidden">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 border-b border-slate-100">
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
                  Existing {entityLabel.toLowerCase()}s are matched by phone first, then email. Matching rows update the current record instead of creating a duplicate.
                </p>
              </div>
            </div>

            {/* Right column — file picker */}
            <div className="space-y-5">
              <div className="rounded-xl border border-slate-100 bg-white p-5 transition-colors hover:border-blue-200">
                <h4 className="text-sm font-semibold text-slate-900">Choose File</h4>

                {/* Always-mounted hidden input so the ref stays valid across state transitions */}
                <input
                  ref={fileInputRef}
                  id="bulk-upload-file-input"
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  className="sr-only"
                  onChange={handleFileChange}
                  aria-label="Choose a spreadsheet file"
                  tabIndex={-1}
                />

                {uploadSuccess ? (
                  /* ── State 3: Upload Successful ── */
                  <div
                    className="mt-4 flex flex-col items-center justify-center rounded-xl border-2 border-emerald-300 bg-emerald-50 px-5 py-10 text-center"
                    role="status"
                    aria-label="Upload successful"
                  >
                    <div className="w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center mb-3">
                      <CheckIcon />
                    </div>
                    <span className="text-sm font-semibold text-emerald-700">Upload Successful</span>
                    <span className="mt-1 text-xs text-emerald-600">{selectedFile?.name}</span>
                  </div>
                ) : selectedFile ? (
                  /* ── State 2: File Selected — immediate green success state ── */
                  <div
                    className="mt-4 rounded-xl border-2 border-emerald-300 bg-emerald-50 px-4 py-4 transition-all"
                    role="status"
                    aria-label={`File selected: ${selectedFile.name}`}
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex-none w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center">
                        <CheckIcon />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-emerald-700">File selected</p>
                        <p className="text-xs text-emerald-600 truncate" title={selectedFile.name}>
                          {selectedFile.name}
                        </p>
                        {selectedFile.size > 0 && (
                          <p className="text-[11px] text-emerald-500 mt-0.5">
                            {formatFileSize(selectedFile.size)} · ready to upload
                          </p>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={removeFile}
                        disabled={uploading}
                        className="flex-none rounded-lg p-1.5 text-emerald-400 hover:bg-emerald-100 hover:text-emerald-700 transition-colors disabled:pointer-events-none disabled:opacity-40"
                        aria-label="Remove selected file"
                        title="Remove file"
                      >
                        <X size={14} />
                      </button>
                    </div>
                    {!uploading && (
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="mt-3 text-[11px] font-medium text-emerald-600 hover:text-emerald-800 hover:underline focus:outline-none focus-visible:underline"
                      >
                        Change file
                      </button>
                    )}
                  </div>
                ) : (
                  /* ── State 1: Default — no file chosen ── */
                  <label
                    htmlFor="bulk-upload-file-input"
                    className={`mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-5 py-10 text-center transition-colors focus-within:ring-2 focus-within:ring-blue-300 ${
                      fileError
                        ? 'border-red-300 bg-red-50/50 hover:border-red-400 hover:bg-red-50'
                        : 'border-slate-200 bg-slate-50 hover:border-blue-200 hover:bg-blue-50/40'
                    }`}
                  >
                    {fileError ? (
                      <AlertTriangle size={28} className="text-red-400" aria-hidden="true" />
                    ) : (
                      <Upload size={28} className="text-slate-400" aria-hidden="true" />
                    )}
                    <span className={`mt-3 text-sm font-medium ${fileError ? 'text-red-600' : 'text-slate-700'}`}>
                      {fileError || 'Select an Excel or CSV file'}
                    </span>
                    <span className={`mt-1 text-xs ${fileError ? 'text-red-400' : 'text-slate-500'}`}>
                      {fileError ? 'Click to try again with a valid file' : 'Supported formats: .xlsx, .xls, .csv'}
                    </span>
                  </label>
                )}

                <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-xs text-slate-500">
                  {requirementsText}
                </div>

                <div className="mt-5 flex gap-3">
                  <button
                    type="button"
                    onClick={onClose}
                    disabled={uploading}
                    className="flex-1 rounded-lg border border-slate-100 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:border-blue-200 hover:text-blue-700 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={submitUpload}
                    disabled={!selectedFile || uploading}
                    className="flex-1 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
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
