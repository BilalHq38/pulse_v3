import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import { BookOpen, Search, Plus, X, Eye, Edit3, Trash2, Tag, User, Calendar, Upload, FileText, Download, Users } from 'lucide-react';

const CATEGORY_LABELS = {
  getting_started: 'Getting Started',
  integration: 'Integration',
  ai: 'AI',
  sales: 'Sales',
  general: 'General',
  troubleshooting: 'Troubleshooting',
};

function formatCategoryLabel(value) {
  const key = String(value || '').trim().toLowerCase();
  if (!key) return 'General';
  return CATEGORY_LABELS[key] || key
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export default function KnowledgeBasePage() {
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  // ── Tab state ──────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState('articles'); // 'articles' | 'onboarding'

  // ── Knowledge Base (Articles) state ────────────────────────
  const [docs, setDocs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [search, setSearch] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [form, setForm] = useState({ title: '', content: '', category: 'general', tags: '', key_points: '', ai_context_enabled: true });

  // ── Onboarding Docs state ──────────────────────────────────
  const [onboardingDocs, setOnboardingDocs] = useState([]);
  const [onboardingLoading, setOnboardingLoading] = useState(true);
  const [onboardingSearch, setOnboardingSearch] = useState('');
  const [onboardingSelected, setOnboardingSelected] = useState(null);
  const [showOnboardingForm, setShowOnboardingForm] = useState(false);
  const [onboardingEditMode, setOnboardingEditMode] = useState(false);
  const [onboardingForm, setOnboardingForm] = useState({ title: '', content: '', category: 'onboarding', tags: '' });
  const [uploading, setUploading] = useState(false);

  const loadDocs = useCallback(async () => {
    try {
      const params = {};
      if (search) params.search = search;
      if (filterCategory) params.category = filterCategory;
      const res = await api.get('/knowledge-base', { params });
      setDocs(res.data);
    } catch (err) {
      console.error(err);
    }
  }, [search, filterCategory]);

  const createDoc = async () => {
    try {
      const payload = { ...form, tags: form.tags.split(',').map(t => t.trim()).filter(Boolean) };
      if (editMode && selected) {
        await api.put(`/knowledge-base/${selected.id}`, payload);
      } else {
        await api.post('/knowledge-base', payload);
      }
      setShowForm(false);
      setEditMode(false);
      setForm({ title: '', content: '', category: 'general', tags: '', key_points: '', ai_context_enabled: true });
      loadDocs();
      showToast({
        type: 'success',
        title: editMode ? 'Article Updated' : 'Article Added',
        message: `${payload.title || 'The article'} is ready in the knowledge base.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: editMode ? 'Update Failed' : 'Create Failed',
        message: getErrorMessage(err, 'We could not save that article.'),
      });
    }
  };

  const deleteDoc = async (docId) => {
    const doc = docs.find((item) => item.id === docId);
    if (doc?.is_prebuilt) {
      showToast({
        type: 'warning',
        title: 'Prebuilt Article Protected',
        message: 'Prebuilt AI context articles cannot be deleted. Edit the article or disable AI context instead.',
      });
      return;
    }
    requestConfirmation({
      title: 'Delete Article',
      description: `Delete ${doc?.title || 'this article'} from the knowledge base. This cannot be undone.`,
      confirmLabel: 'Delete article',
      onConfirm: async () => {
        try {
          await api.delete(`/knowledge-base/${docId}`);
          if (selected?.id === docId) setSelected(null);
          setDocs((prev) => prev.filter((item) => item.id !== docId));
          await loadDocs();
          showToast({
            type: 'success',
            title: 'Article Deleted',
            message: `${doc?.title || 'The article'} was removed.`,
          });
        } catch (err) {
          console.error(err);
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that article.'),
          });
        }
      },
    });
  };

  const startEdit = (doc) => {
    setForm({ title: doc.title, content: doc.content, category: doc.category, tags: (doc.tags || []).join(', '), key_points: doc.key_points || '', ai_context_enabled: doc.ai_context_enabled !== false });
    setSelected(doc);
    setEditMode(true);
    setShowForm(true);
  };

  const toggleAiContext = async (doc) => {
    try {
      await api.put(`/knowledge-base/${doc.id}`, { ai_context_enabled: !doc.ai_context_enabled });
      if (selected?.id === doc.id) setSelected({ ...selected, ai_context_enabled: !doc.ai_context_enabled });
      loadDocs();
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: getErrorMessage(err, 'We could not update the AI context setting.'),
      });
    }
  };

  // ── Onboarding Docs CRUD ───────────────────────────────────
  const loadOnboardingDocs = useCallback(async () => {
    try {
      const params = {};
      if (onboardingSearch) params.search = onboardingSearch;
      const res = await api.get('/onboarding-docs', { params });
      setOnboardingDocs(res.data);
    } catch (err) { console.error(err); }
    finally { setOnboardingLoading(false); }
  }, [onboardingSearch]);

  useEffect(() => { loadDocs(); }, [loadDocs]);
  useEffect(() => { loadOnboardingDocs(); }, [loadOnboardingDocs]);

  const createOnboardingDoc = async () => {
    try {
      const payload = { ...onboardingForm, tags: onboardingForm.tags.split(',').map(t => t.trim()).filter(Boolean) };
      if (onboardingEditMode && onboardingSelected) {
        await api.put(`/onboarding-docs/${onboardingSelected.id}`, payload);
      } else {
        await api.post('/onboarding-docs', payload);
      }
      setShowOnboardingForm(false);
      setOnboardingEditMode(false);
      setOnboardingForm({ title: '', content: '', category: 'onboarding', tags: '' });
      loadOnboardingDocs();
      showToast({
        type: 'success',
        title: onboardingEditMode ? 'Doc Updated' : 'Doc Added',
        message: `${payload.title || 'The onboarding document'} is ready for the team.`,
      });
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: onboardingEditMode ? 'Update Failed' : 'Create Failed',
        message: getErrorMessage(err, 'We could not save that onboarding document.'),
      });
    }
  };

  const deleteOnboardingDoc = async (docId) => {
    const doc = onboardingDocs.find((item) => item.id === docId);
    requestConfirmation({
      title: 'Delete Onboarding Doc',
      description: `Delete ${doc?.title || 'this onboarding document'}. This action cannot be undone.`,
      confirmLabel: 'Delete document',
      onConfirm: async () => {
        try {
          await api.delete(`/onboarding-docs/${docId}`);
          if (onboardingSelected?.id === docId) setOnboardingSelected(null);
          loadOnboardingDocs();
          showToast({
            type: 'success',
            title: 'Doc Deleted',
            message: `${doc?.title || 'The onboarding document'} was removed.`,
          });
        } catch (err) {
          console.error(err);
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that onboarding document.'),
          });
        }
      },
    });
  };

  const startOnboardingEdit = (doc) => {
    setOnboardingForm({ title: doc.title, content: doc.content, category: doc.category || 'onboarding', tags: (doc.tags || []).join(', ') });
    setOnboardingSelected(doc);
    setOnboardingEditMode(true);
    setShowOnboardingForm(true);
  };

  const handleOnboardingFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      await api.post('/onboarding-docs/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      loadOnboardingDocs();
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Upload Failed',
        message: getErrorMessage(err, 'We could not upload that onboarding document.'),
      });
    } finally {
      setUploading(false);
    }
  };

  const downloadOnboardingDoc = async (doc) => {
    try {
      const res = await api.get(`/onboarding-docs/${doc.id}/download`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', doc.file_name || doc.title);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      showToast({
        type: 'success',
        title: 'Download Ready',
        message: `${doc.file_name || doc.title || 'The document'} downloaded successfully.`,
      });
    } catch (err) {
      showToast({
        type: 'error',
        title: 'Download Failed',
        message: getErrorMessage(err, 'We could not download that document.'),
      });
    }
  };

  const categories = ['getting_started', 'integration', 'ai', 'sales', 'general', 'troubleshooting'];
  const onboardingCategories = ['onboarding', 'policies', 'guides', 'templates', 'general'];

  const FILE_TYPE_ICONS = { pdf: '📄', doc: '📝', docx: '📝', pptx: '📊', xlsx: '📗', xls: '📗', csv: '📋', txt: '📃', md: '📑' };

  return (
    <>
    <div className="p-6 lg:p-8 space-y-6" data-testid="knowledge-base-page">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Knowledge Base</h1>
        <p className="text-slate-400 text-sm mt-1">Articles, guides & onboarding documentation</p>
      </div>

      {/* Tab Navigation */}
      <div className="flex items-center gap-1 p-1 bg-slate-100 rounded-xl w-fit">
        <button
          onClick={() => setActiveTab('articles')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'articles' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
        >
          <BookOpen size={15} /> Articles <span className="text-xs bg-slate-200 px-1.5 py-0.5 rounded-md">{docs.length}</span>
        </button>
        <button
          onClick={() => setActiveTab('onboarding')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'onboarding' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
        >
          <Users size={15} /> Onboarding Docs <span className="text-xs bg-slate-200 px-1.5 py-0.5 rounded-md">{onboardingDocs.length}</span>
        </button>
      </div>

      {/* ═══════════════════════════════════════════════════════════
          TAB: Articles (existing Knowledge Base)
          ═══════════════════════════════════════════════════════════ */}
      {activeTab === 'articles' && (
        <>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 flex-1">
              <div className="relative flex-1 max-w-md">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search articles..." className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="kb-search" />
              </div>
              <div className="flex gap-1 overflow-x-auto">
                <button onClick={() => setFilterCategory('')} className={`px-3 py-1.5 text-xs rounded-lg font-medium capitalize transition-colors whitespace-nowrap ${!filterCategory ? 'bg-blue-50 text-blue-600 border border-blue-200' : 'text-slate-400 hover:text-slate-600 border border-slate-200'}`}>All</button>
                {categories.map(c => (
                  <button key={c} onClick={() => setFilterCategory(c)} className={`px-3 py-1.5 text-xs rounded-lg font-medium transition-colors whitespace-nowrap ${filterCategory === c ? 'bg-blue-50 text-blue-600 border border-blue-200' : 'text-slate-400 hover:text-slate-600 border border-slate-200'}`}>{formatCategoryLabel(c)}</button>
                ))}
              </div>
            </div>
            <button onClick={() => { setShowForm(true); setEditMode(false); setForm({ title: '', content: '', category: 'general', tags: '', key_points: '', ai_context_enabled: true }); }} className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15 ml-3" data-testid="add-kb-doc-btn">
              <Plus size={16} /> New Article
            </button>
          </div>

          <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-4">
            <p className="text-sm font-semibold text-indigo-900 mb-2">AI Context Articles</p>
            <div className="text-xs text-indigo-800 space-y-1">
              <p>These articles are used as AI context inside the platform.</p>
              <p>Prebuilt articles can be used directly.</p>
              <p>You can modify them for company requirements.</p>
              <p>You can upgrade and customize them further anytime.</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="kb-articles-grid">
            {docs.map((doc) => (
              <div key={doc.id} className="bg-white border border-slate-100 rounded-xl p-5 hover:border-blue-200 transition-all group" data-testid={`kb-doc-${doc.id}`}>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-2 py-0.5 rounded bg-blue-50 text-blue-600">{formatCategoryLabel(doc.category)}</span>
                    {doc.is_prebuilt && <span className="text-[10px] px-2 py-0.5 rounded bg-violet-50 text-violet-700">Prebuilt</span>}
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => startEdit(doc)} className="p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-slate-600" data-testid={`edit-kb-${doc.id}`}><Edit3 size={14} /></button>
                    <button onClick={() => deleteDoc(doc.id)} className="p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-red-500" data-testid={`delete-kb-${doc.id}`}><Trash2 size={14} /></button>
                  </div>
                </div>
                <h4 className="text-sm font-semibold text-slate-700 mb-2 cursor-pointer hover:text-blue-600" onClick={() => setSelected(doc)}>{doc.title}</h4>
                <p className="text-xs text-slate-400 line-clamp-3 mb-3">{doc.content}</p>
                {doc.key_points && <p className="text-[11px] text-slate-500 line-clamp-2 mb-3">{doc.key_points}</p>}
                <div className="flex items-center justify-between">
                  <div className="flex flex-wrap gap-1">
                    {(doc.tags || []).slice(0, 3).map(tag => (
                      <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-400">{tag}</span>
                    ))}
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => toggleAiContext(doc)} className={`text-[10px] px-2 py-1 rounded-md border ${doc.ai_context_enabled ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                      {doc.ai_context_enabled ? 'AI Enabled' : 'AI Disabled'}
                    </button>
                    <div className="flex items-center gap-1 text-[10px] text-slate-300">
                      <Eye size={10} /> {doc.views || 0}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* ═══════════════════════════════════════════════════════════
          TAB: Onboarding Documentation (isolated — not used by AI/RAG)
          ═══════════════════════════════════════════════════════════ */}
      {activeTab === 'onboarding' && (
        <>
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-2">
            <Users size={16} className="text-amber-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-amber-800">Newcomer Onboarding Documents</p>
              <p className="text-xs text-amber-600 mt-0.5">These files are isolated and <strong>not</strong> used by AI, chatbot, or any other platform function. They are exclusively for onboarding new team members.</p>
            </div>
          </div>

          <div className="flex items-center justify-between">
            <div className="relative flex-1 max-w-md">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={onboardingSearch} onChange={(e) => setOnboardingSearch(e.target.value)} placeholder="Search onboarding docs..." className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" />
            </div>
            <div className="flex items-center gap-2 ml-3">
              <label className={`flex items-center gap-2 px-4 py-2.5 border border-slate-200 text-slate-700 rounded-xl text-sm font-medium hover:bg-slate-50 transition-all cursor-pointer ${uploading ? 'opacity-60 pointer-events-none' : ''}`}>
                {uploading ? (
                  <><div className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" /> Uploading...</>
                ) : (
                  <><Upload size={16} /> Upload File</>
                )}
                <input type="file" accept=".pdf,.doc,.docx,.txt,.md,.pptx,.xlsx,.xls,.csv" className="hidden" onChange={handleOnboardingFileUpload} disabled={uploading} />
              </label>
              <button
                onClick={() => { setShowOnboardingForm(true); setOnboardingEditMode(false); setOnboardingForm({ title: '', content: '', category: 'onboarding', tags: '' }); }}
                className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15"
              >
                <Plus size={16} /> Write Doc
              </button>
            </div>
          </div>

          {onboardingLoading && <p className="text-sm text-slate-400">Loading...</p>}
          {!onboardingLoading && onboardingDocs.length === 0 && (
            <div className="bg-white border border-slate-100 rounded-xl p-8 text-center">
              <FileText size={28} className="text-slate-200 mx-auto mb-3" />
              <p className="text-sm text-slate-400">No onboarding documents yet. Upload a file or write a new document.</p>
            </div>
          )}

          {!onboardingLoading && onboardingDocs.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {onboardingDocs.map(doc => (
                <div key={doc.id} className="bg-white border border-slate-100 rounded-xl p-5 hover:border-blue-200 transition-all group">
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{FILE_TYPE_ICONS[doc.file_type] || '📄'}</span>
                      {doc.file_type && <span className="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-500 uppercase font-medium">{doc.file_type}</span>}
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      {doc.file_name && <button onClick={() => downloadOnboardingDoc(doc)} className="p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-blue-600" title="Download"><Download size={14} /></button>}
                      <button onClick={() => startOnboardingEdit(doc)} className="p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-slate-600"><Edit3 size={14} /></button>
                      <button onClick={() => deleteOnboardingDoc(doc.id)} className="p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-red-500"><Trash2 size={14} /></button>
                    </div>
                  </div>
                  <h4 className="text-sm font-semibold text-slate-700 mb-2 cursor-pointer hover:text-blue-600" onClick={() => setOnboardingSelected(doc)}>{doc.title}</h4>
                  <p className="text-xs text-slate-400 line-clamp-3 mb-3">{doc.content}</p>
                  <div className="flex items-center justify-between">
                    <div className="flex flex-wrap gap-1">
                      {(doc.tags || []).slice(0, 3).map(tag => (
                        <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-400">{tag}</span>
                      ))}
                    </div>
                    {doc.file_size && <span className="text-[10px] text-slate-300">{(doc.file_size / 1024).toFixed(0)} KB</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ═══ Article Viewer Modal ══════════════════════════════════ */}
      {selected && !showForm && activeTab === 'articles' && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="kb-viewer-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-2xl max-h-[80vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-start justify-between mb-6">
                <div>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-blue-50 text-blue-600">{formatCategoryLabel(selected.category)}</span>
                  {selected.is_prebuilt && <span className="ml-2 text-[10px] px-2 py-0.5 rounded bg-violet-50 text-violet-700">Prebuilt</span>}
                  <h3 className="text-xl font-bold text-slate-900 mt-2">{selected.title}</h3>
                  <div className="flex items-center gap-3 mt-2 text-xs text-slate-400">
                    <span className="flex items-center gap-1"><User size={12} /> {selected.author_name}</span>
                    <span className="flex items-center gap-1"><Calendar size={12} /> {new Date(selected.created_at).toLocaleDateString()}</span>
                    <span className="flex items-center gap-1"><Eye size={12} /> {selected.views} views</span>
                  </div>
                </div>
                <button onClick={() => setSelected(null)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="prose prose-invert max-w-none">
                <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Content</p>
                <div className="text-sm text-slate-600 leading-relaxed whitespace-pre-wrap">{selected.content}</div>
              </div>
              {selected.key_points && (
                <div className="mt-5 bg-slate-50 border border-slate-200 rounded-xl p-4">
                  <p className="text-xs font-semibold text-slate-700 mb-2">Key Points / AI Context Notes</p>
                  <p className="text-sm text-slate-600 whitespace-pre-wrap">{selected.key_points}</p>
                </div>
              )}
              {(selected.tags || []).length > 0 && (
                <div className="mt-6">
                  <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Tags</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(selected.tags || []).map(tag => (
                      <span key={tag} className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200"><Tag size={10} className="inline mr-1" />{tag}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ═══ Onboarding Doc Viewer Modal ═══════════════════════════ */}
      {onboardingSelected && !showOnboardingForm && activeTab === 'onboarding' && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setOnboardingSelected(null)}>
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-2xl max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-start justify-between mb-6">
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg">{FILE_TYPE_ICONS[onboardingSelected.file_type] || '📄'}</span>
                    {onboardingSelected.file_type && <span className="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-500 uppercase font-medium">{onboardingSelected.file_type}</span>}
                  </div>
                  <h3 className="text-xl font-bold text-slate-900">{onboardingSelected.title}</h3>
                  <div className="flex items-center gap-3 mt-2 text-xs text-slate-400">
                    <span className="flex items-center gap-1"><User size={12} /> {onboardingSelected.author_name}</span>
                    <span className="flex items-center gap-1"><Calendar size={12} /> {new Date(onboardingSelected.created_at).toLocaleDateString()}</span>
                    {onboardingSelected.file_size && <span>{(onboardingSelected.file_size / 1024).toFixed(0)} KB</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {onboardingSelected.file_name && (
                    <button onClick={() => downloadOnboardingDoc(onboardingSelected)} className="px-3 py-1.5 bg-blue-50 border border-blue-200 text-blue-700 rounded-lg text-xs font-medium hover:bg-blue-100 inline-flex items-center gap-1">
                      <Download size={12} /> Download
                    </button>
                  )}
                  <button onClick={() => setOnboardingSelected(null)} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
                </div>
              </div>
              <div className="text-sm text-slate-600 leading-relaxed whitespace-pre-wrap bg-slate-50 rounded-xl p-4 border border-slate-100">{onboardingSelected.content}</div>
              <div className="flex flex-wrap gap-1.5 mt-4">
                {(onboardingSelected.tags || []).map(tag => (
                  <span key={tag} className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200"><Tag size={10} className="inline mr-1" />{tag}</span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Article Create/Edit Modal ═════════════════════════════ */}
      {showForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="kb-form-modal">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-2xl">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-900">{editMode ? 'Edit Article' : 'New Article'}</h3>
                <button onClick={() => { setShowForm(false); setEditMode(false); }} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Article Title <span className="text-red-400">*</span></label>
                  <input value={form.title} onChange={(e) => setForm({...form, title: e.target.value})} placeholder="e.g. How to reset your password" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="kb-form-title" required />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Category</label>
                  <select value={form.category} onChange={(e) => setForm({...form, category: e.target.value})} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="kb-form-category">
                    {categories.map(c => <option key={c} value={c}>{formatCategoryLabel(c)}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Article Content</label>
                  <textarea value={form.content} onChange={(e) => setForm({...form, content: e.target.value})} placeholder="Write the full article content here..." rows={10} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20 resize-none" data-testid="kb-form-content" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Key Points <span className="text-slate-400 font-normal">(how the AI should use this article)</span></label>
                  <textarea value={form.key_points} onChange={(e) => setForm({...form, key_points: e.target.value})} placeholder="e.g. Use this article when customers ask about account access or forgotten passwords..." rows={4} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20 resize-none" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Tags <span className="text-slate-400 font-normal">(comma separated)</span></label>
                  <input value={form.tags} onChange={(e) => setForm({...form, tags: e.target.value})} placeholder="e.g. password, account, login" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" data-testid="kb-form-tags" />
                </div>
                <label className="flex items-center justify-between px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl cursor-pointer">
                  <div>
                    <span className="text-sm font-medium text-slate-700">Use as AI context</span>
                    <p className="text-xs text-slate-400 mt-0.5">AI will reference this article when answering customer questions</p>
                  </div>
                  <input type="checkbox" checked={form.ai_context_enabled} onChange={(e) => setForm({ ...form, ai_context_enabled: e.target.checked })} className="w-4 h-4 accent-blue-600" />
                </label>
                <button onClick={createDoc} className="w-full py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15" data-testid="kb-form-submit">{editMode ? 'Update Article' : 'Create Article'}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Onboarding Doc Create/Edit Modal ══════════════════════ */}
      {showOnboardingForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-2xl">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-bold text-slate-900">{onboardingEditMode ? 'Edit Document' : 'New Onboarding Document'}</h3>
                <button onClick={() => { setShowOnboardingForm(false); setOnboardingEditMode(false); }} className="text-slate-400 hover:text-slate-600"><X size={20} /></button>
              </div>
              <div className="space-y-4">
                <input value={onboardingForm.title} onChange={(e) => setOnboardingForm({...onboardingForm, title: e.target.value})} placeholder="Document Title *" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" required />
                <select value={onboardingForm.category} onChange={(e) => setOnboardingForm({...onboardingForm, category: e.target.value})} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500/20">
                  {onboardingCategories.map(c => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
                </select>
                <textarea value={onboardingForm.content} onChange={(e) => setOnboardingForm({...onboardingForm, content: e.target.value})} placeholder="Document content..." rows={10} className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20 resize-none" />
                <input value={onboardingForm.tags} onChange={(e) => setOnboardingForm({...onboardingForm, tags: e.target.value})} placeholder="Tags (comma separated)" className="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500/20" />
                <button onClick={createOnboardingDoc} className="w-full py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-xl text-sm font-medium hover:from-blue-500 hover:to-blue-600 transition-all shadow-lg shadow-blue-600/15">{onboardingEditMode ? 'Update Document' : 'Create Document'}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
    {confirmDialog}
    </>
  );
}
