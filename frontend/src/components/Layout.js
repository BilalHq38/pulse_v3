import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { normalizeAvatarUrl, displayNameInitial } from '@/lib/avatar';
import { useSocket } from '@/lib/useSocket';
import PlatformLogo from '@/components/PlatformLogo';
import {
  LayoutDashboard, MessageSquare, Users, Target, BarChart3,
  Ticket, BookOpen, Settings, LogOut, X, Bell, Search, Package,
  Link2, Mail,
  ChevronDown, UserCircle, Filter, Save, Check, Plus
} from 'lucide-react';

const navItems = [
  { path: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, roles: ['admin', 'company_agent'] },
  {
    path: '/inbox',
    label: 'Inbox',
    icon: MessageSquare,
    roles: ['admin', 'company_agent'],
    children: [
      { to: '/inbox?platform=whatsapp', label: 'WhatsApp', channelKey: 'whatsapp' },
      { to: '/inbox?platform=facebook', label: 'Facebook', channelKey: 'facebook' },
      { to: '/inbox?platform=instagram', label: 'Instagram', channelKey: 'instagram' },
      { to: '/inbox?platform=email', label: 'Email', channelKey: 'email' },
      { to: '/inbox?platform=web_chat', label: 'Website', channelKey: 'web_chat' },
    ],
  },
  {
    path: '/leads',
    label: 'Leads',
    icon: Target,
    roles: ['admin', 'company_agent'],
  },
  {
    path: '/campaigns',
    label: 'Campaigns',
    icon: Mail,
    roles: ['admin', 'company_agent'],
  },
  {
    path: '/customers',
    label: 'Customers',
    icon: Users,
    roles: ['admin', 'company_agent'],
  },
  {
    path: '/tickets',
    label: 'Tickets',
    icon: Ticket,
    roles: ['admin', 'company_agent'],
  },
  { path: '/products', label: 'Products', icon: Package, roles: ['admin', 'company_agent'] },
  { path: '/analytics', label: 'Analytics', icon: BarChart3, roles: ['admin', 'company_agent'] },
  {
    path: '/knowledge-base',
    label: 'Knowledge Base',
    icon: BookOpen,
    roles: ['admin', 'company_agent'],
  },
  {
    path: '/unification',
    label: 'Unification',
    icon: Link2,
    roles: ['admin'],
  },
  {
    path: '/settings',
    label: 'Settings',
    icon: Settings,
    roles: ['admin'],
  },
];

const superAdminNavItems = [
  { path: '/super-admin', label: 'Platform User Management', icon: Users },
];

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchFocused, setSearchFocused] = useState(false);
  const [searchFilter, setSearchFilter] = useState('all');
  const [filterOpen, setFilterOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [selectedNotif, setSelectedNotif] = useState(null);
  const [profileView, setProfileView] = useState(null);
  const [profileEditForm, setProfileEditForm] = useState({ name: '', email: '', mobile_number: '' });
  const [profileImageUrl, setProfileImageUrl] = useState('');
  const [userAvatar, setUserAvatar] = useState(() => normalizeAvatarUrl(localStorage.getItem('pe_avatar') || ''));
  const saveUserAvatar = useCallback((rawUrl) => {
    const avatar = normalizeAvatarUrl(rawUrl || '');
    setUserAvatar(avatar);
    if (avatar) localStorage.setItem('pe_avatar', avatar);
    else localStorage.removeItem('pe_avatar');
    return avatar;
  }, []);

  // React to avatar changes made in SettingsPage without a full re-mount
  useEffect(() => {
    const onAvatarChanged = (e) => { saveUserAvatar(e.detail || ''); };
    window.addEventListener('pe-avatar-changed', onAvatarChanged);
    return () => window.removeEventListener('pe-avatar-changed', onAvatarChanged);
  }, [saveUserAvatar]);
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileSaved, setProfileSaved] = useState(false);
  const [modalCompanyName, setModalCompanyName] = useState('');
  const avatarInputRef = useRef(null);
  const [companyName, setCompanyName] = useState(() => localStorage.getItem('pe_company_name') || '');
  const saveCompanyName = (name) => { setCompanyName(name); if (name) localStorage.setItem('pe_company_name', name); };
  const [openMenus, setOpenMenus] = useState({});
  const profileRef = useRef(null);
  const notifRef = useRef(null);
  const filterRef = useRef(null);
  const visibleNavItems = user?.role === 'super_admin' ? superAdminNavItems : navItems;

  // Load company name + avatar once on mount from authoritative endpoints
  useEffect(() => {
    api.get('/settings/company')
      .then(res => { if (res.data?.company_name) saveCompanyName(res.data.company_name); })
      .catch(() => {});
    api.get('/settings/personal')
      .then(res => { if (res.data?.avatar) saveUserAvatar(res.data.avatar); })
      .catch(() => {});
  }, [saveUserAvatar]);

  useEffect(() => {
    setSidebarOpen(false);
    setProfileOpen(false);
    setNotifOpen(false);
    setSelectedNotif(null);
    setSearchResults(null);
    setSearchFocused(false);
    setFilterOpen(false);
    setOpenMenus((prev) => {
      const next = { ...prev };
      visibleNavItems.forEach((item) => {
        if (!item.children) return;
        if (location.pathname.startsWith(item.path)) next[item.label] = true;
      });
      return next;
    });
  }, [location.pathname, visibleNavItems]);

  useEffect(() => {
    function handleClick(e) {
      if (profileRef.current && !profileRef.current.contains(e.target)) setProfileOpen(false);
      if (notifRef.current && !notifRef.current.contains(e.target)) { setNotifOpen(false); setSelectedNotif(null); }
      if (filterRef.current && !filterRef.current.contains(e.target)) setFilterOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  useEffect(() => {
    const timer = setTimeout(async () => {
      if (!searchTerm.trim()) {
        setSearchResults(null);
        return;
      }
      try {
        const res = await api.get('/search/global', { params: { q: searchTerm } });
        setSearchResults(res.data.results || null);
      } catch {
        setSearchResults(null);
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const loadNotifications = useCallback(async () => {
    try {
      const res = await api.get('/notifications');
      setNotifications(res.data.notifications || []);
      setUnreadCount(res.data.unread_count || 0);
    } catch {}
  }, []);

  useSocket(useCallback((event) => {
    if (event === 'notification') {
      loadNotifications();
    }
  }, [loadNotifications]));

  useEffect(() => {
    loadNotifications();
  }, [loadNotifications]);

  const markNotificationRead = async (id) => {
    try {
      await api.put(`/notifications/${id}/read`);
      loadNotifications();
    } catch {}
  };

  const markAllRead = async () => {
    try {
      await api.put('/notifications/read-all');
      loadNotifications();
    } catch {}
  };

  const openProfileView = async () => {
    try {
      const [personalRes, companyRes] = await Promise.all([
        api.get('/settings/personal'),
        api.get('/settings/company').catch(() => ({ data: {} })),
      ]);
      const data = personalRes.data;
      setProfileView({ name: data.name, email: data.email, role: data.role });
      setProfileEditForm({ name: data.name || '', email: data.email || '', mobile_number: data.mobile_number || '' });
      const av = normalizeAvatarUrl(data.avatar || userAvatar);
      setProfileImageUrl(av);
      if (data.avatar) saveUserAvatar(data.avatar);
      const cn = data.company_name || companyRes.data?.company_name || companyName || '';
      setModalCompanyName(cn);
      if (cn) saveCompanyName(cn);
    } catch {
      setProfileView(user);
      setProfileEditForm({ name: user?.name || '', email: user?.email || '', mobile_number: '' });
      setProfileImageUrl(normalizeAvatarUrl(userAvatar));
    }
    setProfileSaved(false);
    setProfileOpen(false);
  };

  const handleAvatarChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const dataUrl = normalizeAvatarUrl(ev.target.result || '');
      setProfileImageUrl(dataUrl);
      saveUserAvatar(dataUrl);
      try {
        await api.put('/settings/personal', { avatar: dataUrl });
      } catch {
        try { await api.put(`/users/${user?.id}`, { avatar: dataUrl }); } catch {}
      }
    };
    reader.readAsDataURL(file);
  };

  const saveProfileFromModal = async () => {
    setProfileSaving(true);
    try {
      const payload = { name: profileEditForm.name, email: profileEditForm.email, mobile_number: profileEditForm.mobile_number };
      if (profileImageUrl) payload.avatar = profileImageUrl;
      try {
        const res = await api.put('/settings/personal', payload);
        setProfileView(prev => ({ ...prev, name: res.data.name || profileEditForm.name, email: res.data.email || profileEditForm.email }));
      } catch {
        await api.put(`/users/${user?.id}`, { name: profileEditForm.name, email: profileEditForm.email, phone: profileEditForm.mobile_number, avatar: profileImageUrl || undefined });
        setProfileView(prev => ({ ...prev, name: profileEditForm.name, email: profileEditForm.email }));
      }
      if (profileImageUrl) saveUserAvatar(profileImageUrl);
      setProfileSaved(true);
      setTimeout(() => setProfileSaved(false), 2500);
    } catch (err) { console.error(err); }
    finally { setProfileSaving(false); }
  };

  useEffect(() => {
    api.get('/settings/personal')
      .then(res => {
        if (res.data?.company_name) saveCompanyName(res.data.company_name);
        if (res.data?.avatar) saveUserAvatar(res.data.avatar);
      })
      .catch(() => {});
  }, [location.pathname, saveUserAvatar]);

  const handleLogout = async () => {
    await logout();
    navigate('/signin');
  };
  const searchGroups = searchFilter === 'all' ? ['leads', 'customers', 'tickets'] : [searchFilter];
  const filterLabel = searchFilter === 'all' ? 'All' : searchFilter.charAt(0).toUpperCase() + searchFilter.slice(1);
  const roleLabel = user?.role ? user.role.charAt(0).toUpperCase() + user.role.slice(1) : '';
  // Super admins are platform-level, not tenant-scoped.
  const companyLabel = user?.role === 'super_admin' ? 'Pulse Engine' : (companyName || '');
  const roleAtCompanyLabel = roleLabel && companyLabel ? `${roleLabel} at ${companyLabel}` : roleLabel;

  return (
    <div className="flex h-screen bg-transparent text-slate-900 overflow-hidden" data-testid="app-layout">
      {sidebarOpen && <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setSidebarOpen(false)} />}

      <aside
        id="mobile-sidebar"
        className={`fixed lg:relative inset-y-0 left-0 z-50 w-64 lg:w-60 flex-shrink-0 pe-glass pe-elevated border-r border-slate-100/80 flex flex-col transform transition-transform duration-300 ease-in-out ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
        data-testid="sidebar"
      >
        <div className="h-14 flex items-center justify-between px-4 border-b border-slate-100">
          <PlatformLogo imageWidth={34} fontSize={15} gap={8} />
          <button onClick={() => setSidebarOpen(false)} className="p-1.5 rounded-md hover:bg-slate-100 text-slate-400 lg:hidden"><X size={18} /></button>
        </div>
        <nav className="flex-1 py-3 px-3 space-y-0.5 overflow-y-auto">
          {visibleNavItems.filter(item => !item.roles || item.roles.includes(user?.role || 'company_agent')).map((item) => {
            const { path, label, icon: Icon, children: subItems } = item;
            const active = location.pathname.startsWith(path);
            if (!subItems) {
              return (
                <Link key={path} to={path} data-testid={`nav-${label.toLowerCase().replace(/\s+/g, '-')}`}
                  className={`group flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-all duration-200 hover:translate-x-1 ${
                    active ? 'bg-blue-50 text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-900 hover:bg-slate-50 hover:shadow-sm'
                  }`}>
                  <Icon size={18} className={`transition-transform duration-200 group-hover:scale-110 ${active ? 'text-blue-600' : ''}`} />
                  <span>{label}</span>
                  {active && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-blue-500" />}
                </Link>
              );
            }
            const isOpen = openMenus[label] || false;
            return (
              <div key={path} className="space-y-0.5">
                <div className={`group flex items-center rounded-lg text-[13px] font-medium transition-all duration-200 hover:translate-x-1 ${
                  active ? 'bg-blue-50 text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-900 hover:bg-slate-50 hover:shadow-sm'
                }`}>
                  <Link to={path} className="flex items-center gap-3 flex-1 px-3 py-2.5 min-w-0">
                    <Icon size={18} className={`transition-transform duration-200 group-hover:scale-110 ${active ? 'text-blue-600' : ''}`} />
                    <span className="flex-1 text-left">{label}</span>
                  </Link>
                  <button
                    onClick={() => setOpenMenus((prev) => ({ ...prev, [label]: !isOpen }))}
                    className="px-2 py-2.5 shrink-0 hover:text-blue-500 transition-colors duration-150"
                    aria-label={`Toggle ${label} submenu`}
                  >
                    <ChevronDown size={14} className={`transition-transform duration-300 ${isOpen ? 'rotate-180' : ''}`} />
                  </button>
                </div>
                <div className={`ml-7 overflow-hidden transition-all duration-300 ease-in-out ${
                  isOpen ? 'max-h-48 opacity-100 mt-1' : 'max-h-0 opacity-0'
                }`}>
                  <div className="border-l-2 border-blue-100 pl-3 space-y-0.5 py-1">
                    {subItems.map((sub) => (
                      <Link
                        key={sub.to}
                        to={sub.to}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-md text-[12px] text-slate-500 hover:text-blue-600 hover:bg-blue-50 transition-all duration-150 hover:translate-x-1"
                      >
                        {sub.channelKey === 'whatsapp' && (
                          <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:14,height:14,borderRadius:3,background:'#25D366',flexShrink:0}}>
                            <svg viewBox="0 0 24 24" width="10" height="10" fill="white"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
                          </span>
                        )}
                        {sub.channelKey === 'facebook' && (
                          <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:14,height:14,borderRadius:3,background:'#1877F2',flexShrink:0}}>
                            <svg viewBox="0 0 24 24" width="10" height="10" fill="white"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
                          </span>
                        )}
                        {sub.channelKey === 'instagram' && (
                          <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:14,height:14,borderRadius:3,background:'radial-gradient(circle at 30% 107%, #fdf497 0%, #fdf497 5%, #fd5949 45%, #d6249f 60%, #285AEB 90%)',flexShrink:0}}>
                            <svg viewBox="0 0 24 24" width="10" height="10" fill="white"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/></svg>
                          </span>
                        )}
                        {sub.channelKey === 'email' && (
                          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 14, height: 14, borderRadius: 3, background: '#0ea5e9', flexShrink: 0 }}>
                            <Mail size={10} className="text-white" strokeWidth={2.5} />
                          </span>
                        )}
                        {sub.channelKey === 'web_chat' && (
                          <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:14,height:14,borderRadius:3,background:'#475569',flexShrink:0}}>
                            <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 010 20M12 2a15.3 15.3 0 000 20"/></svg>
                          </span>
                        )}
                        {sub.label}
                      </Link>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </nav>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-14 flex items-center justify-between px-3 sm:px-5 border-b border-slate-100/80 pe-glass z-10 pe-elevated">
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="relative hidden sm:flex items-center">
              <div className="relative flex items-center bg-white border border-slate-200 rounded-xl px-3 py-2 w-56 md:w-[26rem] shadow-sm">
                <input
                  value={searchTerm}
                  onChange={(e) => { setSearchTerm(e.target.value); setSearchFocused(true); }}
                  onFocus={() => setSearchFocused(true)}
                  type="text"
                  placeholder="Search leads, customers, tickets..."
                  className="flex-1 bg-transparent text-sm text-slate-700 placeholder-slate-400 focus:outline-none"
                  data-testid="global-search-input"
                />
                <button
                  onClick={() => setSearchFocused(true)}
                  className="ml-2 p-1.5 rounded-lg border border-slate-200 bg-slate-50 text-slate-500 hover:text-slate-700 hover:bg-slate-100 hover:scale-[1.04] hover:-translate-y-0.5 transition-all duration-200"
                  data-testid="search-icon-btn"
                >
                  <Search size={14} />
                </button>
              </div>
              <div className="relative ml-2" ref={filterRef}>
                <button
                  onClick={() => setFilterOpen(!filterOpen)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-600 text-sm font-medium hover:bg-slate-50 hover:scale-[1.03] hover:-translate-y-0.5 transition-all duration-200"
                  data-testid="search-filter-btn"
                >
                  <Filter size={14} /> {filterLabel}
                </button>
                {filterOpen && (
                  <div className="absolute top-11 right-0 w-44 bg-white border border-slate-200 rounded-xl shadow-xl py-1.5 z-50" data-testid="search-filter-dropdown">
                    {['all', 'leads', 'customers', 'tickets'].map((option) => (
                      <button
                        key={option}
                        onClick={() => { setSearchFilter(option); setFilterOpen(false); }}
                        className={`w-full text-left px-3 py-2 text-sm capitalize hover:bg-slate-50 ${searchFilter === option ? 'text-blue-600 font-medium' : 'text-slate-600'}`}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1 sm:gap-2">
            <button className="p-2 rounded-md hover:bg-slate-100 text-slate-400 sm:hidden"><Search size={18} /></button>
            <div className="relative" ref={notifRef}>
              <style>{`
                @keyframes bellRing {
                  0%,100%{transform:rotate(0deg)}
                  8%{transform:rotate(20deg)}
                  18%{transform:rotate(-18deg)}
                  28%{transform:rotate(14deg)}
                  38%{transform:rotate(-10deg)}
                  48%{transform:rotate(6deg)}
                  58%{transform:rotate(-3deg)}
                  68%{transform:rotate(1deg)}
                  78%,100%{transform:rotate(0deg)}
                }
                @keyframes badgeBounce {
                  0%,100%{transform:scale(1)}
                  35%{transform:scale(1.4)}
                  55%{transform:scale(0.88)}
                  75%{transform:scale(1.12)}
                }
                @keyframes notifSlideIn {
                  from{opacity:0;transform:translateY(-10px) scale(0.96)}
                  to{opacity:1;transform:translateY(0) scale(1)}
                }
                @keyframes notifItemIn {
                  from{opacity:0;transform:translateX(-8px)}
                  to{opacity:1;transform:translateX(0)}
                }
                .bell-btn .bell-icon{transform-origin:50% 0%;transition:color 0.2s}
                .bell-btn:hover .bell-icon{animation:bellRing 0.7s cubic-bezier(0.36,0.07,0.19,0.97)}
                .bell-badge{animation:badgeBounce 2.8s ease-in-out infinite}
                .notif-dropdown{animation:notifSlideIn 0.22s cubic-bezier(0.16,1,0.3,1)}
                .notif-item{animation:notifItemIn 0.18s ease-out both}
                .notif-item .notif-hint{opacity:0;transition:opacity 0.15s}
                .notif-item:hover .notif-hint{opacity:1}
              `}</style>
              <button
                onClick={() => { setNotifOpen(!notifOpen); if (!notifOpen) loadNotifications(); }}
                className="bell-btn relative p-2 rounded-xl hover:bg-blue-50 text-slate-400 hover:text-blue-500 transition-all duration-200"
                data-testid="notifications-btn"
              >
                <Bell size={18} className="bell-icon" />
                {unreadCount > 0 && (
                  <span className="bell-badge absolute -top-0.5 -right-0.5 min-w-[17px] h-[17px] text-[10px] bg-gradient-to-br from-blue-500 to-indigo-600 text-white rounded-full px-1 flex items-center justify-center font-bold shadow-sm">
                    {unreadCount > 9 ? '9+' : unreadCount}
                  </span>
                )}
              </button>
              {notifOpen && (
                <div className="notif-dropdown absolute right-0 top-12 w-96 bg-white border border-slate-200/80 rounded-2xl shadow-2xl overflow-hidden z-50" data-testid="notifications-dropdown">
                  {selectedNotif ? (() => {
                    const typeMap = {
                      error:   { dot: 'bg-red-500',     border: 'border-l-4 border-red-400',     icon: '✕', iconCls: 'bg-red-100 text-red-500' },
                      warning: { dot: 'bg-amber-400',   border: 'border-l-4 border-amber-400',   icon: '!', iconCls: 'bg-amber-100 text-amber-600' },
                      success: { dot: 'bg-emerald-500', border: 'border-l-4 border-emerald-400', icon: '✓', iconCls: 'bg-emerald-100 text-emerald-600' },
                      info:    { dot: 'bg-blue-500',    border: 'border-l-4 border-blue-400',    icon: 'i', iconCls: 'bg-blue-100 text-blue-600' },
                    };
                    const t = typeMap[selectedNotif.type] || typeMap.info;
                    const actionUrl = selectedNotif.action_url || (() => {
                      const title = (selectedNotif.title || '').toLowerCase();
                      if (title.includes('agent') || title.includes('team') || title.includes('user')) return '/settings?tab=users';
                      if (title.includes('settings') || title.includes('complete') || title.includes('profile')) return '/settings?tab=personal';
                      if (title.includes('ticket')) return '/tickets';
                      if (title.includes('lead')) return '/leads';
                      if (title.includes('customer')) return '/customers';
                      if (title.includes('message') || title.includes('inbox')) return '/inbox';
                      if (title.includes('product') || title.includes('invoice')) return '/products';
                      return null;
                    })();
                    const relTime = (() => {
                      if (!selectedNotif.created_at) return '';
                      const diff = Math.floor((Date.now() - new Date(selectedNotif.created_at)) / 1000);
                      if (diff < 60) return `${diff}s ago`;
                      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
                      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
                      return `${Math.floor(diff / 86400)}d ago`;
                    })();
                    return (
                      <>
                        {/* Detail header */}
                        <div className="bg-gradient-to-r from-slate-50 to-white px-4 py-3 border-b border-slate-100 flex items-center gap-2">
                          <button
                            onClick={() => setSelectedNotif(null)}
                            className="flex items-center gap-1 text-xs text-slate-400 hover:text-blue-600 font-medium transition-colors px-2 py-1 rounded-lg hover:bg-blue-50"
                          >
                            ← Back
                          </button>
                          <div className="w-px h-3 bg-slate-200" />
                          <Bell size={14} className="text-blue-500" />
                          <p className="text-sm font-bold text-slate-800">Notification Detail</p>
                        </div>
                        {/* Detail body */}
                        <div className="p-5 space-y-4">
                          <div className={`rounded-xl p-4 ${t.border} bg-slate-50/80`}>
                            <div className="flex items-start gap-3 mb-3">
                              <div className={`w-8 h-8 rounded-xl flex items-center justify-center text-xs font-bold flex-shrink-0 ${t.iconCls}`}>{t.icon}</div>
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-bold text-slate-800 leading-snug">{selectedNotif.title}</p>
                                <p className="text-[11px] text-slate-400 mt-0.5">{relTime}</p>
                              </div>
                              {!selectedNotif.is_read && <span className={`w-2 h-2 rounded-full ${t.dot} flex-shrink-0 mt-1.5`} />}
                            </div>
                            {selectedNotif.body && (
                              <p className="text-sm text-slate-600 leading-relaxed">{selectedNotif.body}</p>
                            )}
                          </div>
                          {actionUrl && (
                            <div className="space-y-1.5">
                              <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Quick Action</p>
                              <button
                                onClick={() => { navigate(actionUrl); setNotifOpen(false); setSelectedNotif(null); }}
                                className="w-full flex items-center justify-between gap-2 px-4 py-3 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-sm font-semibold transition-colors group"
                              >
                                <span>Open page</span>
                                <span className="text-[11px] bg-white/20 group-hover:bg-white/30 px-2.5 py-1 rounded-lg truncate max-w-[180px] font-normal transition-colors">{actionUrl}</span>
                              </button>
                            </div>
                          )}
                        </div>
                      </>
                    );
                  })() : (
                    <>
                      {/* Header */}
                      <div className="bg-gradient-to-r from-slate-50 to-white px-4 py-3 border-b border-slate-100 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Bell size={14} className="text-blue-500" />
                          <p className="text-sm font-bold text-slate-800">Notifications</p>
                          {unreadCount > 0 && <span className="text-[10px] font-semibold bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded-full">{unreadCount} new</span>}
                        </div>
                        <button onClick={markAllRead} className="text-xs font-medium text-blue-600 hover:text-blue-700 px-2 py-1 rounded-lg hover:bg-blue-50 transition-colors">
                          Mark all read
                        </button>
                      </div>
                      {/* List */}
                      <div className="max-h-[420px] overflow-y-auto divide-y divide-slate-50">
                        {notifications.length === 0 ? (
                          <div className="flex flex-col items-center py-10 px-4 text-center">
                            <div className="w-12 h-12 rounded-2xl bg-slate-100 flex items-center justify-center mb-3">
                              <Bell size={22} className="text-slate-300" />
                            </div>
                            <p className="text-sm font-medium text-slate-500">All caught up!</p>
                            <p className="text-xs text-slate-400 mt-1">No notifications yet</p>
                          </div>
                        ) : notifications.map((n, i) => {
                          const typeMap = {
                            error:   { dot: 'bg-red-500',     border: 'border-l-[3px] border-red-400',     hover: 'hover:bg-red-50/60',     icon: '✕', iconCls: 'bg-red-100 text-red-500' },
                            warning: { dot: 'bg-amber-400',   border: 'border-l-[3px] border-amber-400',   hover: 'hover:bg-amber-50/60',   icon: '!', iconCls: 'bg-amber-100 text-amber-600' },
                            success: { dot: 'bg-emerald-500', border: 'border-l-[3px] border-emerald-400', hover: 'hover:bg-emerald-50/60', icon: '✓', iconCls: 'bg-emerald-100 text-emerald-600' },
                            info:    { dot: 'bg-blue-500',    border: 'border-l-[3px] border-blue-400',    hover: 'hover:bg-blue-50/60',    icon: 'i', iconCls: 'bg-blue-100 text-blue-600' },
                          };
                          const t = typeMap[n.type] || typeMap.info;
                          const handleClick = () => {
                            markNotificationRead(n.id);
                            setSelectedNotif(n);
                          };
                          const relTime = (() => {
                            if (!n.created_at) return '';
                            const diff = Math.floor((Date.now() - new Date(n.created_at)) / 1000);
                            if (diff < 60) return `${diff}s ago`;
                            if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
                            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
                            return `${Math.floor(diff / 86400)}d ago`;
                          })();
                          return (
                            <button
                              key={n.id}
                              onClick={handleClick}
                              className={`notif-item w-full text-left px-4 py-3.5 ${t.border} ${t.hover} transition-all duration-150 ${n.is_read ? 'opacity-55' : ''}`}
                              style={{ animationDelay: `${i * 35}ms` }}
                            >
                              <div className="flex items-start gap-3">
                                <div className={`w-7 h-7 rounded-xl flex items-center justify-center text-[11px] font-bold flex-shrink-0 mt-0.5 ${t.iconCls}`}>{t.icon}</div>
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-start justify-between gap-2">
                                    <p className={`text-[13px] leading-tight text-slate-800 ${n.is_read ? 'font-medium' : 'font-semibold'}`}>{n.title}</p>
                                    <div className="flex items-center gap-1.5 flex-shrink-0 mt-0.5">
                                      {!n.is_read && <span className={`w-2 h-2 rounded-full ${t.dot}`} />}
                                      <span className="text-[10px] text-slate-400 whitespace-nowrap">{relTime}</span>
                                    </div>
                                  </div>
                                  {n.body && <p className="text-xs text-slate-500 mt-0.5 leading-snug line-clamp-2">{n.body}</p>}
                                  <p className="notif-hint text-[10px] text-blue-500 mt-1 font-medium">Click to view →</p>
                                </div>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                      {/* Footer */}
                      {notifications.length > 0 && (
                        <div className="px-4 py-2.5 border-t border-slate-100 bg-slate-50/70 flex items-center justify-between">
                          <p className="text-[11px] text-slate-400">{notifications.length} total</p>
                          <button onClick={() => { setNotifOpen(false); setSelectedNotif(null); navigate('/settings?tab=notifications'); }} className="text-[11px] text-blue-600 font-medium hover:text-blue-700 transition-colors">
                            Notification settings →
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="relative" ref={profileRef}>
              <button onClick={() => setProfileOpen(!profileOpen)} className="group flex items-center gap-1.5 sm:gap-2 pl-1.5 pr-2 py-1 rounded-xl hover:bg-slate-100 transition-all duration-300" data-testid="profile-menu-btn">
                <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center text-[11px] font-bold text-blue-600 overflow-hidden">
                  {userAvatar ? <img src={userAvatar} alt="avatar" referrerPolicy="no-referrer" className="w-full h-full object-cover rounded-full" onError={() => saveUserAvatar('')} /> : displayNameInitial(user?.name)}
                </div>
                <div className="hidden sm:flex flex-col overflow-hidden max-w-0 opacity-0 group-hover:max-w-[220px] group-hover:opacity-100 transition-all duration-300 text-left">
                  <span className="text-[12px] font-semibold text-slate-700 truncate">{user?.name}</span>
                  <span className="text-[10px] text-slate-400 truncate">{roleAtCompanyLabel}</span>
                </div>
                <ChevronDown size={12} className="text-slate-400 hidden sm:block" />
              </button>
              {profileOpen && <div className="absolute right-0 top-10 w-64 bg-white border-2 border-slate-200 rounded-xl shadow-2xl py-1.5 z-50" data-testid="profile-dropdown">
                <div className="px-3 py-2.5 border-b border-slate-100">
                  <p className="text-sm font-semibold text-slate-900">{user?.name}</p>
                  <p className="text-[11px] text-slate-400 truncate mt-0.5">{user?.email}</p>
                  {roleAtCompanyLabel && <p className="text-[11px] text-blue-500 font-medium mt-0.5 capitalize">{roleAtCompanyLabel}</p>}
                </div>
                <button onClick={openProfileView} className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"><UserCircle size={14} /> View Account</button>
                {user?.role === 'super_admin' && <Link to="/super-admin" className="flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">Super Admin</Link>}
                {user?.role === 'admin' && <Link to="/settings" className="flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"><Settings size={14} /> Settings</Link>}
                <button onClick={handleLogout} className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-500 hover:bg-red-50" data-testid="logout-btn"><LogOut size={14} /> Sign Out</button>
              </div>}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto relative" data-testid="main-content">{children}
          {searchFocused && searchTerm.trim() && (
            <div className="search-overlay fixed inset-0 z-40 flex items-start justify-center pt-24 px-4">
              <div className="w-full max-w-4xl bg-white rounded-xl shadow-xl p-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className="flex items-center bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 flex-1 gap-2">
                    <Search size={16} className="text-slate-400" />
                    <input autoFocus value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} className="flex-1 bg-transparent outline-none" />
                  </div>
                  <button onClick={() => { setSearchFocused(false); setSearchResults(null); }} className="text-sm text-slate-500">Close</button>
                </div>
                <div className="max-h-[60vh] overflow-auto">
                  {searchResults ? (
                    searchGroups.map((group) => (
                      <div key={group} className="mb-4">
                        <p className="text-xs uppercase text-slate-400 mb-2">{group}</p>
                        {(searchResults[group] || []).length === 0 && <div className="text-sm text-slate-500">No results</div>}
                        {(searchResults[group] || []).map((item) => (
                          <button key={item.id} onClick={() => {
                            setSearchFocused(false);
                            setSearchResults(null);
                            setSearchTerm('');
                            if (group === 'customers') navigate(`/customers?customer=${encodeURIComponent(item.id)}`);
                            else if (group === 'leads') navigate(`/leads?lead=${encodeURIComponent(item.id)}`);
                            else if (group === 'conversations') navigate(`/inbox?conversation=${encodeURIComponent(item.id)}`);
                            else if (group === 'tickets') navigate(`/tickets?ticket=${encodeURIComponent(item.id)}`);
                            else navigate('/');
                          }} className="w-full text-left px-3 py-2 rounded hover:bg-slate-50 flex items-center justify-between">
                            <div>
                              <div className="text-sm text-slate-800">{item.name || item.customer_name || item.subject || item.ticket_number}</div>
                              <div className="text-xs text-slate-400">{item.email || item.phone || item.ticket_number || ''}</div>
                            </div>
                            <div className="text-xs text-slate-400">{item.id}</div>
                          </button>
                        ))}
                      </div>
                    ))
                  ) : (
                    <div className="text-sm text-slate-500">Searching...</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </main>
      </div>

      {profileView && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setProfileView(null)}>
          <div className="bg-white rounded-2xl border border-slate-200 shadow-2xl w-full max-w-md overflow-hidden" onClick={(e) => e.stopPropagation()} data-testid="profile-view-modal">
            {/* Header banner */}
            <div className="bg-gradient-to-r from-blue-600 to-indigo-600 h-24 relative">
              <button onClick={() => setProfileView(null)} className="absolute top-3 right-3 p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white transition-colors"><X size={16} /></button>
              {/* Avatar with + button */}
              <div className="absolute -bottom-9 left-6">
                <div className="relative" style={{width:72,height:72}}>
                  <div className="w-full h-full rounded-2xl bg-white border-4 border-white shadow-lg flex items-center justify-center text-2xl font-bold text-blue-600 overflow-hidden">
                    {profileImageUrl
                      ? <img src={profileImageUrl} alt="avatar" referrerPolicy="no-referrer" className="w-full h-full object-cover" onError={() => { setProfileImageUrl(''); saveUserAvatar(''); }} />
                      : displayNameInitial(profileView?.name)
                    }
                  </div>
                  {/* + change photo button */}
                  <button
                    onClick={() => avatarInputRef.current?.click()}
                    className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full bg-blue-600 border-2 border-white flex items-center justify-center hover:bg-blue-500 transition-colors shadow"
                    title="Change profile photo"
                  >
                    <Plus size={10} className="text-white" />
                  </button>
                  <input ref={avatarInputRef} type="file" accept="image/*" className="hidden" onChange={handleAvatarChange} />
                </div>
              </div>
            </div>

            {/* Body */}
            <div className="pt-12 px-6 pb-6 space-y-5">
              {/* Name + role badge */}
              <div>
                <p className="text-lg font-bold text-slate-900">{profileView.name}</p>
                <span className="mt-1 inline-block text-[11px] font-medium bg-blue-100 text-blue-700 px-2.5 py-0.5 rounded-full capitalize">
                  {profileView.role ? profileView.role.charAt(0).toUpperCase() + profileView.role.slice(1) : ''}{(modalCompanyName || companyLabel) ? ` at ${modalCompanyName || companyLabel}` : ''}
                </span>
              </div>

              {/* Read-only info row */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="bg-slate-50 rounded-xl p-3">
                  <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">Email</p>
                  <p className="text-slate-700 text-xs font-medium truncate">{profileView.email}</p>
                </div>
                <div className="bg-slate-50 rounded-xl p-3">
                  <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">Company</p>
                  <p className="text-slate-700 text-xs font-medium truncate">{modalCompanyName || companyLabel || '—'}</p>
                </div>
              </div>

              {/* Divider */}
              <div className="border-t border-slate-100" />

              {/* Inline editable fields — all roles */}
              <div className="space-y-3">
                <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Edit Your Details</p>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Full Name</label>
                  <input value={profileEditForm.name} onChange={(e) => setProfileEditForm(p => ({ ...p, name: e.target.value }))} placeholder="Your name" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" />
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Email Address</label>
                  <input value={profileEditForm.email} onChange={(e) => setProfileEditForm(p => ({ ...p, email: e.target.value }))} placeholder="your@email.com" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" />
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Mobile Number</label>
                  <input value={profileEditForm.mobile_number} onChange={(e) => setProfileEditForm(p => ({ ...p, mobile_number: e.target.value }))} placeholder="+1 555 000 0000" className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-200" />
                </div>
                <div className="flex items-center gap-3 pt-1">
                  <button onClick={saveProfileFromModal} disabled={profileSaving} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50 transition-colors">
                    <Save size={14} /> {profileSaving ? 'Saving…' : 'Save Changes'}
                  </button>
                  {profileSaved && <span className="flex items-center gap-1 text-emerald-600 text-xs font-medium"><Check size={13} /> Saved</span>}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
