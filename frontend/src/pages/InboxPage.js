import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { resolveMediaUrl } from '@/lib/backend-url';
import { getErrorMessage, showToast } from '@/hooks/use-toast';
import { useConfirmDialog } from '@/hooks/use-confirm-dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useSocket } from '@/lib/useSocket';
import { useAuth } from '@/contexts/AuthContext';
import PageSkeleton from '@/components/ui/PageSkeleton';
import {
  Send,
  Bot,
  Sparkles,
  Phone,
  Mail,
  Tag,
  MessageSquare,
  UserCircle,
  X,
  ArrowLeft,
  AlertTriangle,
  User,
  ChevronRight,
  Plus,
  Pencil,
  Trash2,
  Check,
  MoreVertical,
  Image as ImageIcon,
  PlayCircle,
} from 'lucide-react';

const CHANNELS = [
  { key: 'whatsapp', label: 'WhatsApp', color: 'bg-emerald-500', lightBg: 'bg-emerald-50', text: 'text-emerald-600', border: 'border-emerald-200' },
  { key: 'facebook', label: 'Facebook', color: 'bg-blue-500', lightBg: 'bg-blue-50', text: 'text-blue-600', border: 'border-blue-200' },
  { key: 'instagram', label: 'Instagram', color: 'bg-pink-500', lightBg: 'bg-pink-50', text: 'text-pink-600', border: 'border-pink-200' },
  { key: 'email', label: 'Email', color: 'bg-sky-500', lightBg: 'bg-sky-50', text: 'text-sky-600', border: 'border-sky-200' },
  { key: 'web_chat', label: 'Website', color: 'bg-violet-500', lightBg: 'bg-violet-50', text: 'text-violet-600', border: 'border-violet-200' },
];

const INBOX_FILTERS = {
  incoming_messages: { label: 'Incoming Messages', empty: 'No incoming messages today' },
  active_conversations: { label: 'Active Conversations', empty: 'No active conversations' },
  pending_replies: { label: 'Pending Replies', empty: 'No conversations awaiting replies' },
  ai_chats: { label: 'AI Chat', empty: 'No active AI-handled chats' },
  human_chats: { label: 'Human Chats', empty: 'No active human-handled chats' },
  unread: { label: 'Unread', empty: 'No unread conversations' },
};

function normalizeInboxFilter(value) {
  const key = String(value || '').trim().toLowerCase().replace(/-/g, '_');
  const aliases = {
    incoming: 'incoming_messages',
    incoming_messages: 'incoming_messages',
    active: 'active_conversations',
    active_conversations: 'active_conversations',
    pending: 'pending_replies',
    pending_replies: 'pending_replies',
    ai: 'ai_chats',
    ai_chat: 'ai_chats',
    ai_chats: 'ai_chats',
    human: 'human_chats',
    human_chat: 'human_chats',
    human_chats: 'human_chats',
    unread: 'unread',
  };
  return aliases[key] || '';
}

const CHAT_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const CHAT_VIDEO_TYPES = ['video/mp4', 'video/webm', 'video/quicktime'];
const CHAT_MEDIA_TYPES = [...CHAT_IMAGE_TYPES, ...CHAT_VIDEO_TYPES];
const MAX_CHAT_IMAGE_SIZE_MB = 8;
const MAX_CHAT_VIDEO_SIZE_MB = 16;
const MAX_CHAT_MEDIA = 4;
const LONG_REQUEST_TIMEOUT_MS = 120000;
const COMPOSER_DRAFTS_STORAGE_KEY = 'pulse:inbox-composer-drafts:v1';

function readComposerDrafts() {
  try {
    const parsed = JSON.parse(localStorage.getItem(COMPOSER_DRAFTS_STORAGE_KEY) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeComposerDrafts(drafts) {
  try {
    localStorage.setItem(COMPOSER_DRAFTS_STORAGE_KEY, JSON.stringify(drafts || {}));
  } catch {
    // Ignore storage quota/private-mode failures; composer still works in memory.
  }
}

function isLikelyValidDisplayPhone(value) {
  const text = String(value || '').trim();
  const digits = text.replace(/\D/g, '');
  return Boolean(text.startsWith('+') && digits.length >= 8 && digits.length <= 15);
}

function normalizeAttachments(attachments) {
  if (!Array.isArray(attachments)) return [];
  return attachments
    .map((attachment, index) => {
      if (!attachment || typeof attachment !== 'object') return null;
      return {
        id: attachment.id || `att-${index}`,
        type: String(attachment.type || attachment.file_type || '').startsWith('video') || String(attachment.mime_type || '').startsWith('video/')
          ? 'video'
          : String(attachment.type || attachment.file_type || '').startsWith('image') || String(attachment.mime_type || '').startsWith('image/')
            ? 'image'
            : attachment.type || attachment.file_type || 'unknown',
        url: resolveMediaUrl(attachment.url || attachment.file_url || ''),
        name: attachment.name || attachment.file_name || '',
        size: Number(attachment.size || attachment.file_size || 0),
        mime_type: attachment.mime_type || '',
        image_analysis_status: attachment.image_analysis_status || '',
      };
    })
    .filter((attachment) => attachment && attachment.url);
}

// Wave 5: small footer that surfaces conversation-engine metadata
// (sources_used and any product link cards) on AI message bubbles. The
// metadata is stashed on `messages.raw_metadata` when the engine produced
// the response; legacy AI replies still render without the footer.
const SOURCE_LABELS = {
  company_data: 'Company',
  product: 'Product',
  template: 'Template',
  faq: 'FAQ',
  knowledge_base: 'Knowledge Base',
};

function AiEngineFooter({ metadata }) {
  let parsed = metadata;
  if (typeof parsed === 'string') {
    try { parsed = JSON.parse(parsed); } catch { return null; }
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const sources = Array.isArray(parsed.sources_used) ? parsed.sources_used : [];
  const productLinks = Array.isArray(parsed.product_links) ? parsed.product_links : [];
  if (sources.length === 0 && productLinks.length === 0) return null;
  return (
    <div className="mt-2 space-y-1.5">
      {sources.length > 0 && (
        <div className="flex flex-wrap gap-1" data-testid="ai-sources-footer">
          <span className="text-[10px] text-slate-500 font-medium">Sources:</span>
          {sources.map((src) => (
            <span
              key={src}
              className="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded border border-purple-200 bg-purple-50 text-purple-700"
            >
              {SOURCE_LABELS[src] || src}
            </span>
          ))}
        </div>
      )}
      {productLinks.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="ai-product-links">
          {productLinks.map((link, idx) => (
            <a
              key={`${link.product_id || idx}-${idx}`}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-emerald-200 bg-white text-emerald-700 hover:bg-emerald-50"
            >
              View product
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function normalizeReactions(reactions) {
  if (!Array.isArray(reactions)) return [];
  const latest = new Map();
  reactions.forEach((reaction, index) => {
    if (!reaction || typeof reaction !== 'object') return;
    const action = String(reaction.action || 'added').toLowerCase();
    const key = reaction.id || reaction.provider_message_id || `${reaction.actor_id || 'actor'}-${reaction.emoji || index}`;
    if (action === 'removed') {
      latest.delete(key);
      return;
    }
    latest.set(key, {
      id: key,
      emoji: reaction.emoji || '',
      actor_type: reaction.actor_type || 'customer',
      actor_id: reaction.actor_id || '',
      action,
    });
  });
  return Array.from(latest.values()).filter((reaction) => reaction.emoji);
}

function ChatImageThumb({ attachment, className, onOpen }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`relative block w-full overflow-hidden bg-slate-100 text-left ${className || ''}`}
      disabled={failed}
      title={attachment.name || 'Open image'}
    >
      {!loaded && !failed && (
        <div className="absolute inset-0 flex items-center justify-center text-[11px] text-slate-400">
          Loading
        </div>
      )}
      {failed ? (
        <div className="flex h-28 items-center justify-center px-3 text-center text-[11px] text-slate-500">
          Image unavailable
        </div>
      ) : (
        <img
          src={attachment.url}
          alt={attachment.name || 'attachment'}
          className="h-full w-full object-cover"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      )}
      {attachment.name && !failed && (
        <span className="absolute bottom-1 left-1.5 max-w-[85%] truncate rounded bg-black/50 px-1 py-0.5 text-[10px] font-medium leading-tight text-white">
          {attachment.name}
        </span>
      )}
    </button>
  );
}

function ChatVideoThumb({ attachment, onOpen }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="relative flex h-40 w-full items-center justify-center overflow-hidden bg-slate-900 text-white"
      title={attachment.name || 'Open video'}
    >
      <video src={attachment.url} className="h-full w-full object-cover opacity-80" muted preload="metadata" />
      <span className="absolute inset-0 flex items-center justify-center">
        <span className="rounded-full bg-black/55 p-2">
          <PlayCircle size={28} />
        </span>
      </span>
      {attachment.name && (
        <span className="absolute bottom-1 left-1.5 max-w-[85%] truncate rounded bg-black/55 px-1 py-0.5 text-[10px] font-medium leading-tight text-white">
          {attachment.name}
        </span>
      )}
    </button>
  );
}

function ContactAvatar({ entity, name, channelMeta, className = 'w-10 h-10', textClass = 'text-sm' }) {
  const [failed, setFailed] = useState(false);
  const url = resolveMediaUrl(entity?.avatar || entity?.customer_avatar || entity?.profile_picture_url || '');
  useEffect(() => {
    setFailed(false);
  }, [url]);
  const initial = (name || entity?.customer_name || entity?.name || '?').charAt(0);
  return (
    <div className={`${className} rounded-full ${channelMeta?.lightBg || 'bg-slate-100'} flex items-center justify-center ${textClass} font-bold ${channelMeta?.text || 'text-slate-600'} overflow-hidden flex-shrink-0`}>
      {url && !failed ? (
        <img src={url} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : initial}
    </div>
  );
}

function isWhatsappGroupConversation(convo) {
  return Boolean(convo?.is_group || convo?.group_id || convo?.channel_id?.endsWith?.('@g.us'));
}

function conversationDisplayName(convo) {
  if (!convo) return '';
  return convo.group_name || convo.subject || convo.customer_name || convo.name || '';
}

function messageGroupName(message, selectedConvo) {
  return message?.whatsapp_group_name || message?.group_name || selectedConvo?.group_name || (isWhatsappGroupConversation(selectedConvo) ? conversationDisplayName(selectedConvo) : '');
}

function messageParticipantName(message) {
  return message?.whatsapp_participant_name || message?.group_participant_name || message?.sender_name || '';
}

function resolveSenderName(message) {
  const raw = normalizeMessageText(message.sender_name);
  if (raw) return raw;
  const t = String(message.sender_type || '').toLowerCase();
  if (t === 'ai') return 'AI';
  if (t === 'agent' || t === 'business') return 'Agent';
  if (t === 'customer') return 'Customer';
  if (message.from_me || message.web_bridge?.from_me) return 'Agent';
  return 'Agent';
}

function normalizeMessage(message) {
  if (!message || typeof message !== 'object') return message;
  return {
    ...message,
    content: normalizeMessageText(message.content),
    sender_name: resolveSenderName(message),
    attachments: normalizeAttachments(message.attachments),
    reactions: normalizeReactions(message.reactions),
  };
}

function normalizeMessageText(value) {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (typeof value === 'object') {
    if (typeof value.content === 'string') return value.content;
    if (typeof value.text === 'string') return value.text;
  }
  return '';
}

function isManualAiWithheldSystemMessage(content) {
  const text = normalizeMessageText(content).trim();
  return text === 'AI response withheld for manual review.' || /^AI confidence\b/i.test(text);
}

function formatMessageTimestamp(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function extractMessagesPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === 'object') {
    if (Array.isArray(payload.messages)) return payload.messages;
    if (Array.isArray(payload.data)) return payload.data;
  }
  return null;
}

function mergeMessageUpdate(currentMessage, incomingMessage) {
  const normalized = normalizeMessage(incomingMessage);
  if (!currentMessage) return normalized;
  return {
    ...currentMessage,
    ...normalized,
    attachments: normalized.attachments?.length ? normalized.attachments : (currentMessage.attachments || []),
    reactions: normalized.reactions?.length ? normalized.reactions : (currentMessage.reactions || []),
  };
}

function getDeliveryStatusMeta(status) {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'failed') return { label: 'Failed', className: 'bg-red-50 text-red-700 border-red-200' };
  if (normalized === 'delivered') return { label: 'Delivered', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' };
  if (normalized === 'sent') return { label: 'Sent', className: 'bg-blue-50 text-blue-700 border-blue-200' };
  if (normalized === 'pending' || normalized === 'sending') return { label: 'Sending', className: 'bg-amber-50 text-amber-700 border-amber-200' };
  return null;
}

function formatInboxChannel(channelKey) {
  const match = CHANNELS.find((channel) => channel.key === channelKey);
  return match?.label || String(channelKey || 'message').replace(/_/g, ' ');
}

function getChannelDisconnectMessage(conversation) {
  if (!conversation || conversation.channel_connected !== false) return '';
  const channelName = formatInboxChannel(conversation.channel);
  return String(conversation.channel_error || `${channelName} is not connected. Reconnect the channel before sending.`).trim();
}

function isConversationAiDisabled(conversation) {
  if (!conversation) return false;
  const paused = Boolean(conversation.ai_auto_paused || (!conversation.ai_handled && conversation.ai_paused_reason));
  const disabledUntil = Date.parse(conversation.ai_disabled_until || '');
  return paused || (Number.isFinite(disabledUntil) && disabledUntil > Date.now());
}

function getAiDisabledReason(conversation) {
  return String(
    conversation?.ai_paused_reason ||
    'AI auto-response is paused because the AI provider is unavailable. Please respond manually.'
  ).trim();
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function ChannelLogo({ channelKey, size = 14 }) {
  if (channelKey === 'whatsapp') return (
    <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:size,height:size,borderRadius:3,background:'#25D366',flexShrink:0}}>
      <svg viewBox="0 0 24 24" width={size*0.72} height={size*0.72} fill="white">
        <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
      </svg>
    </span>
  );
  if (channelKey === 'facebook') return (
    <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:size,height:size,borderRadius:3,background:'#1877F2',flexShrink:0}}>
      <svg viewBox="0 0 24 24" width={size*0.72} height={size*0.72} fill="white">
        <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
      </svg>
    </span>
  );
  if (channelKey === 'instagram') return (
    <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:size,height:size,borderRadius:3,background:'radial-gradient(circle at 30% 107%, #fdf497 0%, #fdf497 5%, #fd5949 45%, #d6249f 60%, #285AEB 90%)',flexShrink:0}}>
      <svg viewBox="0 0 24 24" width={size*0.72} height={size*0.72} fill="white">
        <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
      </svg>
    </span>
  );
  if (channelKey === 'email') return (
    <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:size,height:size,borderRadius:3,background:'linear-gradient(135deg,#0ea5e9,#0284c7)',flexShrink:0}}>
      <svg viewBox="0 0 24 24" width={size*0.72} height={size*0.72} fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
        <polyline points="22,6 12,13 2,6"/>
      </svg>
    </span>
  );
  return (
    <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:size,height:size,borderRadius:3,background:'#475569',flexShrink:0}}>
      <svg viewBox="0 0 24 24" width={size*0.72} height={size*0.72} fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>
        <path d="M12 2a15.3 15.3 0 010 20M12 2a15.3 15.3 0 000 20"/>
      </svg>
    </span>
  );
}

const TAG_COLORS = {
  pricing: 'bg-amber-50 text-amber-600 border-amber-200',
  sales: 'bg-green-50 text-green-600 border-green-200',
  vip: 'bg-purple-50 text-purple-600 border-purple-200',
  support: 'bg-blue-50 text-blue-600 border-blue-200',
  billing: 'bg-red-50 text-red-600 border-red-200',
  complaint: 'bg-red-50 text-red-600 border-red-200',
  technical: 'bg-cyan-50 text-cyan-600 border-cyan-200',
  api: 'bg-slate-100 text-slate-600 border-slate-200',
  'feature-request': 'bg-indigo-50 text-indigo-600 border-indigo-200',
};

function getTagColor(tag) { return TAG_COLORS[tag] || 'bg-slate-50 text-slate-500 border-slate-200'; }

function normalizeSentimentScore(rawScore) {
  if (rawScore === null || rawScore === undefined || Number.isNaN(Number(rawScore))) return null;
  const numeric = Number(rawScore);
  if (numeric >= 0 && numeric <= 1) return numeric;
  return Math.max(0, Math.min(1, (numeric + 1) / 2));
}

function getSentimentMeta(rawScore, label = '', emotion = '') {
  const score = normalizeSentimentScore(rawScore);
  if (score === null) return null;

  // normalizeSentimentScore maps DB raw [-1,1] → [0,1] via (x+1)/2.
  // Convert back to raw [-1,1] for meaningful threshold-based labelling.
  const rawNormalized = score * 2 - 1;
  const percentage = Math.round(((rawNormalized + 1) / 2) * 100);

  let tone, accentClass;
  // Threshold: >0.2 = Positive, <-0.2 = Negative, else Neutral
  if (rawNormalized > 0.2) {
    tone = 'Positive';
    accentClass = 'bg-emerald-50 text-emerald-700 border-emerald-200';
  } else if (rawNormalized < -0.2) {
    tone = 'Negative';
    accentClass = 'bg-red-50 text-red-700 border-red-200';
  } else {
    tone = 'Neutral';
    accentClass = 'bg-amber-50 text-amber-700 border-amber-200';
  }

  const displayLabel = emotion || label || tone;
  return {
    score,
    percentage,
    tone,
    accentClass,
    displayLabel,
  };
}

const MSG_CACHE_KEY_PREFIX = 'pe:inbox:msgs:';
const MSG_CACHE_MAX = 20;

function getCachedMessages(convoId) {
  try {
    const raw = sessionStorage.getItem(MSG_CACHE_KEY_PREFIX + convoId);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
}

function setCachedMessages(convoId, messages) {
  try {
    sessionStorage.setItem(MSG_CACHE_KEY_PREFIX + convoId, JSON.stringify(messages.slice(-MSG_CACHE_MAX)));
  } catch {}
}

export default function InboxPage() {
  const { user, loading: authLoading } = useAuth();
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  const navigate = useNavigate();
  const [conversations, setConversations] = useState([]);
  const [selectedConvo, setSelectedConvo] = useState(null);
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState('');
  const [composerAttachments, setComposerAttachments] = useState([]);
  const [composerError, setComposerError] = useState('');
  const [sending, setSending] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const aiLoadingRef = useRef(aiLoading);
  const [customerInfo, setCustomerInfo] = useState(null);
  const [maximized, setMaximized] = useState(false);
  const [tooltipChannel, setTooltipChannel] = useState(null);
  const tooltipTimerRef = useRef(null);
  const [aiToggling, setAiToggling] = useState(false);
  const [activeChannel, setActiveChannel] = useState(null); // For mobile channel filter
  const [showCustomerSidebar, setShowCustomerSidebar] = useState(false);
  const [outboundComposerOpen, setOutboundComposerOpen] = useState(false);
  const [outboundSubmitting, setOutboundSubmitting] = useState(false);
  const [outboundChannel, setOutboundChannel] = useState('whatsapp');
  const [unificationMatch, setUnificationMatch] = useState(null);
  const [unificationPanelOpen, setUnificationPanelOpen] = useState(false);
  const [outboundName, setOutboundName] = useState('');
  const [outboundPhone, setOutboundPhone] = useState('');
  const [outboundRecipientId, setOutboundRecipientId] = useState('');
  const [outboundMessage, setOutboundMessage] = useState('');
  const [platformView, setPlatformView] = useState(null);
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [editingMessageContent, setEditingMessageContent] = useState('');
  const [messageActionLoadingId, setMessageActionLoadingId] = useState('');
  const [menuConvoId, setMenuConvoId] = useState(null);
  const [customerEditOpen, setCustomerEditOpen] = useState(false);
  const [customerEditLoading, setCustomerEditLoading] = useState(false);
  const [customerEditTarget, setCustomerEditTarget] = useState(null);
  const [customerEditForm, setCustomerEditForm] = useState({ name: '', email: '', phone: '', company: '' });
  const [notifyNewMessage, setNotifyNewMessage] = useState(true);
  const [imagePreview, setImagePreview] = useState(null);
  const [imagePreviewFailed, setImagePreviewFailed] = useState(false);
  const [videoPreview, setVideoPreview] = useState(null);
  const [videoPreviewFailed, setVideoPreviewFailed] = useState(false);
  const [convoLoading, setConvoLoading] = useState(true);
  const firstConvoLoadRef = useRef(true);
  const messagesEndRef = useRef(null);
  const composerFileRef = useRef(null);
  const customerSidebarRef = useRef(null);
  const selectedConvoIdRef = useRef('');
  const loadConversationsRef = useRef(null);
  const skipNextDraftSaveRef = useRef('');
  const [searchParams, setSearchParams] = useSearchParams();
  const inboxFilter = normalizeInboxFilter(searchParams.get('inbox_filter') || searchParams.get('filter'));

  const loadConversations = useCallback(async ({ selectConversationId = '', filterOverride, _isRetry = false } = {}) => {
    try {
      const effectiveFilter = filterOverride === undefined ? inboxFilter : normalizeInboxFilter(filterOverride);
      const params = effectiveFilter ? { inbox_filter: effectiveFilter } : undefined;
      const res = await api.get('/conversations', params ? { params } : undefined);
      const items = Array.isArray(res.data) ? res.data : [];
      firstConvoLoadRef.current = false;
      setConversations(items);
      setSelectedConvo(prev => {
        const targetId = selectConversationId || prev?.id || '';
        if (!targetId) return prev;
        const refreshed = items.find(c => c.id === targetId);
        return refreshed || prev;
      });
    } catch (err) {
      console.error(err);
      // On the very first load, suppress the error toast and retry once after 1s
      if (firstConvoLoadRef.current && !_isRetry) {
        firstConvoLoadRef.current = false;
        setTimeout(() => {
          loadConversationsRef.current?.({ selectConversationId, filterOverride, _isRetry: true });
        }, 1000);
        return;
      }
      showToast({
        type: 'error',
        title: 'Inbox Unavailable',
        message: 'Conversations could not be loaded. Refresh the page or contact support if this continues.',
      });
    } finally {
      setConvoLoading(false);
    }
  }, [inboxFilter]);

  // Keep a stable ref so socket handlers always call the latest version
  loadConversationsRef.current = loadConversations;

  const loadNotificationSettings = useCallback(async () => {
    try {
      const res = await api.get('/notification-settings');
      setNotifyNewMessage(res.data?.notify_new_message !== false);
    } catch {
      setNotifyNewMessage(true);
    }
  }, []);

  useEffect(() => {
    selectedConvoIdRef.current = selectedConvo?.id || '';
  }, [selectedConvo?.id]);

  useEffect(() => {
    const convoId = selectedConvo?.id || '';
    if (!convoId) return;
    const draft = readComposerDrafts()[convoId] || {};
    skipNextDraftSaveRef.current = convoId;
    setNewMessage(String(draft.content || ''));
    setComposerAttachments(Array.isArray(draft.attachments) ? draft.attachments.slice(0, MAX_CHAT_MEDIA) : []);
  }, [selectedConvo?.id]);

  useEffect(() => {
    const convoId = selectedConvo?.id || '';
    if (!convoId) return;
    if (skipNextDraftSaveRef.current === convoId) {
      skipNextDraftSaveRef.current = '';
      return;
    }
    const drafts = readComposerDrafts();
    if (newMessage.trim() || composerAttachments.length) {
      drafts[convoId] = {
        content: newMessage,
        attachments: composerAttachments.slice(0, MAX_CHAT_MEDIA),
        customer_id: selectedConvo?.customer_id || '',
        channel: selectedConvo?.channel || '',
        updated_at: new Date().toISOString(),
      };
    } else {
      delete drafts[convoId];
    }
    writeComposerDrafts(drafts);
  }, [selectedConvo?.id, selectedConvo?.customer_id, selectedConvo?.channel, newMessage, composerAttachments]);

  useEffect(() => {
    aiLoadingRef.current = aiLoading;
  }, [aiLoading]);

  // Socket event dispatcher — passed to useSocket hook below
  const handleSocketEvent = useCallback((eventName, data) => {
    if (eventName === 'new_message') {
      if (!data?.conversation_id || !data?.message) return;
      const activeConvoId = selectedConvoIdRef.current;
      if (!activeConvoId || data.conversation_id !== activeConvoId) {
        loadConversations();
        if (notifyNewMessage) {
          const senderName =
            data.message?.sender_name ||
            data.customer_name ||
            data.conversation?.customer_name ||
            'A contact';
          const channel = data.message?.channel || data.channel || data.conversation?.channel || '';
          showToast({
            type: 'info',
            title: 'New Message',
            message: `${senderName} sent a message via ${formatInboxChannel(channel)}.`,
          });
        }
        return;
      }
      const senderType = String(data.message?.sender_type || '').toLowerCase();
      if (senderType === 'system' && isManualAiWithheldSystemMessage(data.message?.content)) {
        setAiLoading(false);
        showToast({
          type: 'warning',
          title: 'AI Draft Needs Review',
          message: normalizeMessageText(data.message?.content) || 'Please review the conversation and respond manually.',
        });
        return;
      }
      if (aiLoadingRef.current && senderType === 'ai') {
        const normalized = normalizeMessage(data.message);
        setNewMessage(normalized.content || '');
        setComposerAttachments(normalizeAttachments(normalized.attachments || []));
        setAiLoading(false);
        return;
      }
      setMessages(prev => {
        const nextMessage = normalizeMessage(data.message);
        if (prev.some(m => m.id === nextMessage.id)) {
          return prev.map(m => m.id === nextMessage.id ? mergeMessageUpdate(m, nextMessage) : m);
        }
        return [...prev, nextMessage];
      });
      if (['ai', 'system'].includes(senderType)) {
        setAiLoading(false);
      }
    } else if (eventName === 'conversation_updated') {
      loadConversations();
    } else if (eventName === 'message_updated') {
      if (data?.conversation_id && data?.message) {
        setMessages(prev => prev.map(m => m.id === data.message.id ? mergeMessageUpdate(m, data.message) : m));
      }
    } else if (eventName === 'message_deleted') {
      if (data?.conversation_id && data?.message_id) {
        setMessages(prev => prev.filter(m => m.id !== data.message_id));
      }
    } else if (eventName === 'message_reaction_updated') {
      const reaction = data?.reaction;
      if (data?.conversation_id && reaction?.message_id) {
        setMessages(prev => prev.map((message) => {
          if (message.id !== reaction.message_id) return message;
          const existing = Array.isArray(message.reactions) ? message.reactions : [];
          const next = normalizeReactions([
            ...existing.filter((item) => item.id !== reaction.id && item.provider_message_id !== reaction.provider_message_id),
            reaction,
          ]);
          return { ...message, reactions: next };
        }));
      }
    }
  }, [loadConversations, notifyNewMessage, setNewMessage, setComposerAttachments]);

  const { joinConversation } = useSocket(handleSocketEvent, { conversationId: selectedConvo?.id || '' });

  useEffect(() => {
    if (!user || authLoading) return;
    loadConversations();
    loadNotificationSettings();
  }, [user, authLoading, loadConversations, loadNotificationSettings]);

  // 15-second background poll for missed socket events; skips hidden tabs
  useEffect(() => {
    if (!user || authLoading) return;
    const id = setInterval(() => {
      if (document.visibilityState !== 'hidden') {
        loadConversationsRef.current?.();
      }
    }, 15000);
    return () => clearInterval(id);
  }, [user, authLoading]);

  // Refresh on tab focus to catch updates missed while backgrounded
  useEffect(() => {
    if (!user || authLoading) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') loadConversationsRef.current?.();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [user, authLoading]);

  useEffect(() => {
    const platform = searchParams.get('platform');
    if (['whatsapp', 'facebook', 'instagram', 'email', 'web_chat'].includes(platform)) {
      setPlatformView(platform);
      setActiveChannel(platform);
    } else {
      setPlatformView(null);
    }

    const directConversationId = searchParams.get('conversation');
    if (directConversationId) {
      loadConversations({ selectConversationId: directConversationId, filterOverride: '' }).then(() => {
        setMaximized(true);
      }).finally(() => {
        if (platform) setSearchParams({ platform }, { replace: true });
        else setSearchParams({}, { replace: true });
      });
      return;
    }

    const outboundMode = searchParams.get('outbound') === '1';
    if (outboundMode) {
      const requestedChannel = searchParams.get('channel') || 'whatsapp';
      const channel = ['whatsapp', 'facebook', 'instagram', 'email'].includes(requestedChannel) ? requestedChannel : 'whatsapp';
      setOutboundChannel(channel);
      setOutboundName(searchParams.get('name') || '');
      setOutboundPhone(searchParams.get('phone') || '');
      setOutboundRecipientId('');
      setOutboundMessage('');
      setOutboundComposerOpen(true);
      setActiveChannel(channel);
      if (platform) setSearchParams({ platform }, { replace: true });
      else setSearchParams({}, { replace: true });
      return;
    }

    const contactName = searchParams.get('contactName');
    const contactPhone = searchParams.get('contactPhone');
    const contactChannel = searchParams.get('channel') || 'web_chat';
    if (!contactPhone) {
      const keepOnlyPlatform = platform ? { platform } : {};
      const hasTransient = searchParams.get('contactName') || searchParams.get('contactPhone') || searchParams.get('outbound') || searchParams.get('source') || searchParams.get('channel');
      if (hasTransient) setSearchParams(keepOnlyPlatform, { replace: true });
      return;
    }

    const bootstrapConversation = async () => {
      try {
        const res = await api.post('/conversations/start', {
          name: contactName || "Profile Contact",
          phone: contactPhone,
          source: searchParams.get('source') || "profile_card",
          channel: contactChannel,
        });
        const convo = res.data?.conversation;
        if (convo?.id) {
          setSelectedConvo(convo);
          setMaximized(true);
          await loadConversations();
        }
      } catch (err) {
        console.error('Failed to open conversation from profile:', err);
        showToast({
          type: 'error',
          title: 'Load Failed',
          message: 'The conversation could not be loaded. Refresh the page or contact support if it keeps happening.',
        });
      } finally {
        if (platform) setSearchParams({ platform }, { replace: true });
        else setSearchParams({}, { replace: true });
      }
    };

    bootstrapConversation();
  }, [loadConversations, searchParams, setSearchParams]);

  const loadMessagesAbortRef = useRef(null);

  const loadMessages = useCallback(async (convoId) => {
    if (loadMessagesAbortRef.current) {
      loadMessagesAbortRef.current.abort();
    }
    const controller = new AbortController();
    loadMessagesAbortRef.current = controller;

    // Show cached messages immediately while fresh fetch runs
    const cached = getCachedMessages(convoId);
    if (cached?.length) {
      setMessages(cached.map(normalizeMessage));
    }

    try {
      const res = await api.get(`/conversations/${convoId}/messages`, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const loadedMessages = extractMessagesPayload(res.data);
      if (!loadedMessages) throw new Error('Unexpected messages response shape');
      const normalized = loadedMessages.map(normalizeMessage);
      setMessages(normalized);
      setCachedMessages(convoId, normalized);
    } catch (err) {
      if (err?.name === 'AbortError' || err?.code === 'ERR_CANCELED') return;
      console.error(err);
      showToast({
        type: 'error',
        title: 'Messages Unavailable',
        message: 'This conversation could not be loaded right now. Refresh and try again.',
      });
    }
  }, []);

  useEffect(() => {
    const convoId = selectedConvo?.id;
    const customerId = selectedConvo?.customer_id;
    if (!convoId) return;
    let cancelled = false;
    setCustomerInfo(null);
    setUnificationMatch(null);
    loadMessages(convoId);
    markConversationRead(convoId);
    if (customerId) {
      api.get(`/customers/${customerId}`).then(r => {
        if (!cancelled) setCustomerInfo(r.data);
      }).catch(() => {});
      api.get(`/identity/customer/${customerId}`).then(r => {
        if (!cancelled) setUnificationMatch(r.data?.unified ? r.data.profile : null);
      }).catch(() => { if (!cancelled) setUnificationMatch(null); });
    } else {
      setCustomerInfo(null);
      setUnificationMatch(null);
    }
    joinConversation(convoId);
    return () => { cancelled = true; };
  }, [selectedConvo?.id, selectedConvo?.customer_id, joinConversation, loadMessages]);

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  useEffect(() => {
    const closeMenu = () => setMenuConvoId(null);
    window.addEventListener('click', closeMenu);
    return () => window.removeEventListener('click', closeMenu);
  }, []);

  const openOutboundComposer = (channelKey) => {
    setOutboundChannel(channelKey);
    setOutboundName('');
    setOutboundPhone('');
    setOutboundRecipientId('');
    setOutboundMessage('');
    setOutboundComposerOpen(true);
  };

  const submitOutboundConversation = async () => {
    if (outboundSubmitting) return;
    const channel = outboundChannel;
    const name = outboundName.trim();
    const phone = outboundPhone.trim();
    const recipientId = outboundRecipientId.trim();
    const message = outboundMessage.trim();

    if (!name) {
      showToast({
        type: 'error',
        title: 'Name Required',
        message: 'Enter a contact name before starting the conversation.',
      });
      return;
    }
    if (channel === 'whatsapp' && !phone) {
      showToast({
        type: 'error',
        title: 'Phone Required',
        message: 'Enter a phone number before starting a WhatsApp conversation.',
      });
      return;
    }
    if ((channel === 'facebook' || channel === 'instagram') && !recipientId) {
      showToast({
        type: 'error',
        title: 'Recipient Required',
        message: channel === 'facebook'
          ? 'Enter the Facebook recipient ID before sending.'
          : 'Enter the Instagram recipient ID before sending.',
      });
      return;
    }
    if (channel === 'email' && !recipientId) {
      showToast({
        type: 'error',
        title: 'Email Required',
        message: 'Enter the recipient email before starting an email conversation.',
      });
      return;
    }
    if (!message) {
      showToast({
        type: 'error',
        title: 'Message Required',
        message: 'Enter the first outbound message before sending.',
      });
      return;
    }

    setOutboundSubmitting(true);
    try {
      const payload = {
        channel,
        name,
        initial_message: message,
        source: `inbox_${channel}_plus`,
      };
      if (channel === 'whatsapp') payload.phone = phone;
      else payload.recipient_id = recipientId;

      const res = await api.post(
        '/conversations/start-outbound',
        payload,
        channel === 'email' ? { timeout: LONG_REQUEST_TIMEOUT_MS } : undefined,
      );
      const convo = res.data?.conversation;
      if (convo?.id) {
        setSelectedConvo(convo);
        setMaximized(true);
      }
      setOutboundComposerOpen(false);
      await loadConversations();
      if (convo?.id) await loadMessages(convo.id);
      if (res.data?.outbound_sent === false) {
        showToast({
          type: 'warning',
          title: 'Conversation Saved',
          message: res.data?.outbound_error || `The conversation with ${name} was created, but the first message could not be delivered yet.`,
        });
      } else {
        showToast({
          type: 'success',
          title: 'Conversation Started',
          message: `Started a ${formatInboxChannel(channel)} conversation with ${name}.`,
        });
      }
    } catch (err) {
      console.error('Failed to start outbound conversation', err);
      showToast({
        type: 'error',
        title: 'Start Failed',
        message: getErrorMessage(err, 'We could not start that outbound conversation.'),
      });
    } finally {
      setOutboundSubmitting(false);
    }
  };

  const handleComposerFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setComposerError('');
    const availableSlots = Math.max(0, MAX_CHAT_MEDIA - composerAttachments.length);
    const selectedFiles = files.slice(0, availableSlots);
    if (selectedFiles.length < files.length) {
      setComposerError(`You can send up to ${MAX_CHAT_MEDIA} media files at once.`);
    }
    try {
      const nextAttachments = [];
      for (const file of selectedFiles) {
        if (!CHAT_MEDIA_TYPES.includes(file.type)) {
          setComposerError('Only JPG, PNG, WEBP, GIF, MP4, WEBM, and MOV media are supported.');
          continue;
        }
        const isVideo = CHAT_VIDEO_TYPES.includes(file.type);
        const maxSizeMb = isVideo ? MAX_CHAT_VIDEO_SIZE_MB : MAX_CHAT_IMAGE_SIZE_MB;
        if (file.size > maxSizeMb * 1024 * 1024) {
          setComposerError(`Each ${isVideo ? 'video' : 'image'} must be ${maxSizeMb}MB or smaller.`);
          continue;
        }
        const url = await fileToDataUrl(file);
        nextAttachments.push({
          type: isVideo ? 'video' : 'image',
          url,
          name: file.name,
          size: file.size,
          mime_type: file.type,
        });
      }
      if (nextAttachments.length) {
        setComposerAttachments((prev) => [...prev, ...nextAttachments].slice(0, MAX_CHAT_MEDIA));
      }
    } catch (err) {
      console.error('Attachment read failed:', err);
      setComposerError('Failed to read one of the selected media files.');
    } finally {
      if (composerFileRef.current) composerFileRef.current.value = '';
    }
  };

  const markConversationRead = async (convoId) => {
    try {
      await api.put(`/conversations/${convoId}/mark-read`);
      setConversations(prev => prev.map(c => c.id === convoId ? { ...c, unread_count: 0 } : c));
      setSelectedConvo(prev => prev && prev.id === convoId ? { ...prev, unread_count: 0 } : prev);
    } catch (err) { console.error('Failed to mark as read:', err); }
  };

  const sendMessage = async () => {
    if ((!newMessage.trim() && composerAttachments.length === 0) || !selectedConvo || sending) return;
    const channelError = getChannelDisconnectMessage(selectedConvo);
    if (channelError) {
      showToast({
        type: 'warning',
        title: 'Channel Disconnected',
        message: channelError,
      });
      return;
    }

    setSending(true);
    try {
      const res = await api.post(
        `/conversations/${selectedConvo.id}/messages`,
        {
          content: newMessage,
          sender_type: 'agent',
          attachments: composerAttachments,
        },
        selectedConvo.channel === 'email' ? { timeout: LONG_REQUEST_TIMEOUT_MS } : undefined,
      );
      if (res.data.message) {
        setMessages(prev => {
          const base = res.data.message;
          const isPendingStatus = !base.delivery_status || base.delivery_status === 'pending' || base.delivery_status === 'sending';
          const nextMessage = normalizeMessage({
            ...base,
            delivery_status: isPendingStatus ? 'sent' : base.delivery_status,
          });
          if (prev.some(m => m.id === nextMessage.id)) return prev;
          return [...prev, nextMessage];
        });
      }
      if (res.data.ai_response) {
        setMessages(prev => {
          const nextMessage = normalizeMessage(res.data.ai_response);
          if (prev.some(m => m.id === nextMessage.id)) return prev;
          return [...prev, nextMessage];
        });
      }
      setNewMessage('');
      setComposerAttachments([]);
      setComposerError('');
      if (composerFileRef.current) composerFileRef.current.value = '';
      loadConversations();
      if (res.data?.outbound_delivered === false) {
        showToast({
          type: 'warning',
          title: 'Delivery Failed',
          message: res.data?.outbound_error || `The message was saved, but ${selectedConvo.customer_name || 'this contact'} did not receive it yet.`,
          dedupeKey: `delivery-failed:${selectedConvo.id}:${res.data?.outbound_error || ''}`,
        });
      } else {
        showToast({
          type: 'success',
          title: 'Message Sent',
          message: `Sent to ${selectedConvo.customer_name || 'this contact'} via ${formatInboxChannel(selectedConvo.channel)}.`,
        });
      }
    } catch (err) {
      console.error('Send message error:', err);
      showToast({
        type: 'error',
        title: 'Send Failed',
        message: getErrorMessage(err, 'We could not send that message.'),
      });
    }
    finally { setSending(false); }
  };

  const startEditMessage = (msg) => {
    setEditingMessageId(msg.id);
    setEditingMessageContent(normalizeMessageText(msg.content));
  };

  const cancelEditMessage = () => {
    setEditingMessageId(null);
    setEditingMessageContent('');
  };

  const saveEditedMessage = async (msg) => {
    if (!selectedConvo || !editingMessageId || !editingMessageContent.trim()) return;
    setMessageActionLoadingId(msg.id);
    try {
      const res = await api.put(`/conversations/${selectedConvo.id}/messages/${msg.id}`, {
        content: editingMessageContent.trim(),
      });
      if (res.data?.message) {
        setMessages(prev => prev.map(m => m.id === res.data.message.id ? res.data.message : m));
      }
      cancelEditMessage();
      loadConversations();
      showToast({
        type: 'success',
        title: 'Message Updated',
        message: `The reply for ${selectedConvo.customer_name || 'this conversation'} was updated.`,
      });
    } catch (err) {
      console.error('Edit message failed:', err);
      showToast({
        type: 'error',
        title: 'Edit Failed',
        message: getErrorMessage(err, 'We could not update that message.'),
      });
    } finally {
      setMessageActionLoadingId('');
    }
  };

  const deleteConversationMessage = async (msg) => {
    if (!selectedConvo) return;
    requestConfirmation({
      title: 'Delete Message',
      description: 'Delete this message from the conversation. This action cannot be undone.',
      confirmLabel: 'Delete message',
      onConfirm: async () => {
        setMessageActionLoadingId(msg.id);
        try {
          await api.delete(`/conversations/${selectedConvo.id}/messages/${msg.id}`);
          setMessages(prev => prev.filter(m => m.id !== msg.id));
          if (editingMessageId === msg.id) cancelEditMessage();
          loadConversations();
          showToast({
            type: 'success',
            title: 'Message Deleted',
            message: `The message in ${selectedConvo.customer_name || 'this conversation'} was removed.`,
          });
        } catch (err) {
          console.error('Delete message failed:', err);
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that message.'),
          });
        } finally {
          setMessageActionLoadingId('');
        }
      },
    });
  };

  const triggerAI = async () => {
    if (!selectedConvo || aiLoading) return;
    const channelError = getChannelDisconnectMessage(selectedConvo);
    if (channelError) {
      showToast({
        type: 'warning',
        title: 'Channel Disconnected',
        message: channelError,
      });
      return;
    }
    setAiLoading(true);
    let keepLoading = false;
    const queuedConvoId = selectedConvo.id;
    try {
      const res = await api.post(
        `/conversations/${selectedConvo.id}/ai-respond`,
        {},
        { timeout: LONG_REQUEST_TIMEOUT_MS },
      );
      if (res.status === 202 || res.data?.status === 'processing') {
        keepLoading = true;
        loadConversations();
        showToast({
          type: 'info',
          title: 'AI Reply Queued',
          message: `AI is preparing a reply for ${selectedConvo.customer_name || 'this conversation'}.`,
        });
        setTimeout(() => {
          if (selectedConvoIdRef.current === queuedConvoId) {
            setAiLoading(false);
          }
        }, 45000);
        return;
      }
      const aiData = res.data || {};
      if (aiData.status === 'draft_ready') {
        const draft = normalizeMessageText(aiData.draft || aiData.response || '');
        setNewMessage(draft);
        setComposerAttachments(normalizeAttachments(aiData.attachments || aiData.product_images || []));
        loadConversations();
        const fallbackReason = normalizeMessageText(aiData.fallback_reason || aiData.error_type || '');
        showToast({
          type: aiData.fallback_used ? 'warning' : 'info',
          title: aiData.fallback_used ? 'Fallback Draft Inserted' : 'AI Draft Ready',
          message: aiData.fallback_used
            ? (fallbackReason ? `Provider issue: ${fallbackReason}. Review before sending.` : 'Review this fallback before sending.')
            : 'Review and edit the draft before sending.',
        });
        return;
      }
      const isSystemFallback = aiData.sender_type === 'system';
      const isAiMessage = aiData.sender_type === 'ai';
      if (isAiMessage) {
        // If the AI returned a message, place it into the composer for editing instead of auto-sending.
        const normalized = normalizeMessage(aiData);
        setNewMessage(normalized.content || '');
        setComposerAttachments(normalizeAttachments(normalized.attachments || []));
        loadConversations();
        showToast({
          type: 'info',
          title: 'AI Draft Ready',
          message: `AI drafted a reply for ${selectedConvo.customer_name || 'this conversation'}. Review and edit before sending.`,
        });
      } else {
        if (isSystemFallback && isManualAiWithheldSystemMessage(aiData.content)) {
          showToast({
            type: 'warning',
            title: 'AI Draft Needs Review',
            message: normalizeMessageText(aiData.content) || 'Please review the conversation and respond manually.',
          });
          return;
        }
        // Handle system fallback or other types of messages normally.
        setMessages(prev => {
          const nextMessage = normalizeMessage(aiData);
          if (prev.some(m => m.id === nextMessage.id)) return prev;
          return [...prev, nextMessage];
        });
        loadConversations();
        showToast({
          type: isSystemFallback ? 'warning' : 'success',
          title: isSystemFallback ? 'AI Unavailable' : 'AI Reply Ready',
          message: isSystemFallback
            ? 'The inbox is still available. Please respond manually.'
            : `AI generated a reply for ${selectedConvo.customer_name || 'this conversation'}.`,
        });
      }
    } catch (err) {
      console.error(err);
      showToast({
        type: 'error',
        title: 'AI Reply Failed',
        message: getErrorMessage(err, 'We could not generate an AI reply.'),
      });
    }
    finally {
      if (!keepLoading) setAiLoading(false);
    }
  };

  const toggleAIMode = async () => {
    if (!selectedConvo || aiToggling) return;
    setAiToggling(true);
    const newAiState = isConversationAiDisabled(selectedConvo) ? true : !selectedConvo.ai_handled;
    try {
      const res = await api.put(`/conversations/${selectedConvo.id}/toggle-ai`, {
        enable_ai: newAiState
      });
      const updatedConvo = res.data.conversation;
      setSelectedConvo(updatedConvo);
      setConversations(prev => prev.map(c => c.id === updatedConvo.id ? updatedConvo : c));
      await loadMessages(selectedConvo.id);
      showToast({
        type: 'success',
        title: 'AI Mode Updated',
        message: `${newAiState ? 'AI responses enabled' : 'AI responses paused'} for ${selectedConvo.customer_name || 'this conversation'}.`,
      });
    } catch (err) {
      console.error('Toggle AI failed:', err);
      showToast({
        type: 'error',
        title: 'Toggle Failed',
        message: getErrorMessage(err, 'We could not update AI handling for this conversation.'),
      });
    }
    finally { setAiToggling(false); }
  };

  const handleSelectConvo = (convo) => {
    setMessages([]);
    setCustomerInfo(null);
    setComposerAttachments([]);
    setComposerError('');
    setSelectedConvo(convo);
    setMaximized(true);
    setShowCustomerSidebar(false);
  };

  const handleBack = () => {
    setMaximized(false);
    setSelectedConvo(null);
    setCustomerInfo(null);
    setShowCustomerSidebar(false);
    setComposerAttachments([]);
    setComposerError('');
    setUnificationMatch(null);
    setUnificationPanelOpen(false);
  };

  const openCustomerProfileSidebar = useCallback(() => {
    setShowCustomerSidebar(true);
  }, []);

  const openCustomerEdit = async (convo) => {
    if (!convo?.customer_id) {
      showToast({
        type: 'error',
        title: 'No Customer Linked',
        message: 'This conversation is not linked to a customer profile yet.',
      });
      return;
    }
    setMenuConvoId(null);
    setCustomerEditTarget(convo);
    setCustomerEditLoading(true);
    try {
      const res = await api.get(`/customers/${convo.customer_id}`);
      const c = res.data || {};
      setCustomerEditForm({
        name: c.name || convo.customer_name || '',
        email: c.email || '',
        phone: c.phone || '',
        company: c.company || '',
      });
      setCustomerEditOpen(true);
    } catch (err) {
      console.error('Failed to load customer for edit:', err);
      showToast({
        type: 'error',
        title: 'Load Failed',
        message: 'The customer profile could not be loaded. Refresh and try again.',
      });
    } finally {
      setCustomerEditLoading(false);
    }
  };

  const saveCustomerEdit = async () => {
    if (!customerEditTarget?.customer_id) return;
    setCustomerEditLoading(true);
    try {
      const res = await api.put(`/customers/${customerEditTarget.customer_id}`, {
        name: customerEditForm.name,
        email: customerEditForm.email,
        phone: customerEditForm.phone,
        company: customerEditForm.company,
      });
      const updated = res.data || {};
      setConversations(prev => prev.map(c => c.customer_id === customerEditTarget.customer_id
        ? { ...c, customer_name: updated.name || c.customer_name }
        : c));
      setSelectedConvo(prev => prev && prev.customer_id === customerEditTarget.customer_id
        ? { ...prev, customer_name: updated.name || prev.customer_name }
        : prev);
      setCustomerInfo(prev => prev && prev.id === customerEditTarget.customer_id
        ? { ...prev, ...updated }
        : prev);
      setCustomerEditOpen(false);
      showToast({
        type: 'success',
        title: 'Customer Saved',
        message: `${updated.name || customerEditForm.name || 'The customer'} was updated.`,
      });
    } catch (err) {
      console.error('Failed to save customer:', err);
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: getErrorMessage(err, 'We could not save the customer details.'),
      });
    } finally {
      setCustomerEditLoading(false);
    }
  };

  const deleteFullConversation = async (convo) => {
    setMenuConvoId(null);
    requestConfirmation({
      title: 'Delete Conversation',
      description: `Delete the full chat for ${convo.customer_name || 'this contact'}. All messages in this conversation will be removed.`,
      confirmLabel: 'Delete chat',
      onConfirm: async () => {
        try {
          await api.delete(`/conversations/${convo.id}`);
          setConversations(prev => prev.filter(c => c.id !== convo.id));
          if (selectedConvo?.id === convo.id) {
            setSelectedConvo(null);
            setMessages([]);
            setCustomerInfo(null);
            setMaximized(false);
          }
          await loadConversations();
          showToast({
            type: 'success',
            title: 'Chat Deleted',
            message: `${convo.customer_name || 'The conversation'} was removed.`,
          });
        } catch (err) {
          console.error('Failed to delete conversation:', err);
          showToast({
            type: 'error',
            title: 'Delete Failed',
            message: getErrorMessage(err, 'We could not delete that conversation.'),
          });
        }
      },
    });
  };

  const grouped = {};
  CHANNELS.forEach(ch => { grouped[ch.key] = []; });
  conversations.forEach(c => {
    if (grouped[c.channel]) grouped[c.channel].push(c);
    else if (grouped.web_chat) grouped.web_chat.push(c);
  });

  const isEscalationSystemMessage = (content) => /conversation escalated|ai service unavailable|please respond manually/i.test(content || '');

  const chInfo = selectedConvo
    ? CHANNELS.find(c => c.key === selectedConvo.channel) || CHANNELS.find(c => c.key === 'web_chat')
    : null;
  const selectedConvoIsGroup = isWhatsappGroupConversation(selectedConvo);
  const selectedConvoDisplayName = conversationDisplayName(selectedConvo);
  const selectedConvoSentiment = getSentimentMeta(selectedConvo?.sentiment_score, selectedConvo?.sentiment_label);
  const channelDisconnectMessage = getChannelDisconnectMessage(selectedConvo);
  const channelDisconnected = Boolean(channelDisconnectMessage);
  const selectedAiDisabled = isConversationAiDisabled(selectedConvo);
  const selectedAiToggleOn = Boolean(selectedConvo?.ai_handled && !selectedAiDisabled);

  // Get filtered conversations for mobile
  const displayChannel = platformView || activeChannel;
  const filteredConvos = displayChannel ? grouped[displayChannel] || [] : conversations;
  const desktopChannels = platformView ? CHANNELS.filter((ch) => ch.key === platformView) : CHANNELS;
  const inboxFilterMeta = INBOX_FILTERS[inboxFilter] || null;
  const clearPlatformView = () => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('platform');
    setSearchParams(nextParams, { replace: true });
    setPlatformView(null);
    setActiveChannel(null);
  };
  const clearInboxFilter = () => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('inbox_filter');
    nextParams.delete('filter');
    setSearchParams(nextParams, { replace: true });
  };

  if (convoLoading) {
    return <PageSkeleton variant="inbox" />;
  }

  return (
    <>
    <div className="flex h-[calc(100vh-3.5rem)]" data-testid="inbox-page">
      {(inboxFilterMeta || platformView) && (
        <div className="absolute top-2 right-4 z-20 flex flex-wrap items-center gap-2 max-w-[calc(100%-2rem)]">
          {inboxFilterMeta && (
            <div className="bg-white border border-slate-200 shadow-sm rounded-xl px-3 py-1.5 text-xs text-slate-600">
              <span>
                Filter: {inboxFilterMeta.label}
                <button
                  onClick={clearInboxFilter}
                  className="ml-2 text-blue-600 hover:text-blue-700 font-medium"
                >
                  Clear
                </button>
              </span>
            </div>
          )}
          {platformView && (
            <div className="bg-white border border-slate-200 shadow-sm rounded-xl px-3 py-1.5 text-xs text-slate-600">
              <span>
                Channel: {CHANNELS.find(ch => ch.key === platformView)?.label || platformView}
                <button
                  onClick={clearPlatformView}
                  className="ml-2 text-blue-600 hover:text-blue-700 font-medium"
                >
                  Clear
                </button>
              </span>
            </div>
          )}
        </div>
      )}
      {/* Mobile: Channel Tabs + Conversation List */}
      {!maximized && (
        <div className="flex-1 flex flex-col lg:hidden">
          {/* Mobile Channel Tabs */}
          <div className="flex border-b border-slate-100 bg-white overflow-x-auto">
            <button
              onClick={() => setActiveChannel(null)}
              className={`flex-shrink-0 px-4 py-3 text-xs font-medium border-b-2 transition-colors ${!activeChannel ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500'}`}
            >
              All ({conversations.length})
            </button>
            {CHANNELS.map(ch => (
              <button
                key={ch.key}
                onClick={() => setActiveChannel(ch.key)}
                className={`flex-shrink-0 px-4 py-3 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 ${activeChannel === ch.key ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500'}`}
              >
                <ChannelLogo channelKey={ch.key} size={14} />
                {ch.label} ({grouped[ch.key]?.length || 0})
              </button>
            ))}
          </div>

          {/* Mobile section-level new button */}
          {(activeChannel === 'whatsapp' || activeChannel === 'facebook' || activeChannel === 'instagram' || activeChannel === 'email') && (
            <div className="px-3 py-2 bg-white border-b border-slate-100 flex items-center justify-end">
              <button
                onClick={() => openOutboundComposer(activeChannel)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
                data-testid={`mobile-new-${activeChannel}`}
              >
                <Plus size={14} /> New
              </button>
            </div>
          )}
          
          {/* Mobile Conversation List */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-slate-50">
            {filteredConvos.map(convo => {
              const ch = CHANNELS.find(c => c.key === convo.channel) || CHANNELS.find(c => c.key === 'web_chat') || CHANNELS[0];
              const convoSentiment = getSentimentMeta(convo.sentiment_score, convo.sentiment_label);
              const isGroup = isWhatsappGroupConversation(convo);
              const displayName = conversationDisplayName(convo);
              const aiDisabled = isConversationAiDisabled(convo);
              return (
                <div
                  key={convo.id}
                  onClick={() => handleSelectConvo(convo)}
                  className={`rounded-xl p-3.5 border cursor-pointer transition-all hover:shadow-md active:scale-[0.98] ${aiDisabled ? 'bg-amber-50/60 border-amber-200' : 'bg-white border-slate-100'}`}
                  data-testid={`convo-card-${convo.id}`}
                >
                  <div className="flex items-start gap-3">
                    <ContactAvatar entity={convo} name={displayName} channelMeta={ch} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-semibold text-slate-800 truncate">{displayName}</span>
                        <div className="flex items-center gap-1.5">
                          <div className="relative" onClick={(e) => e.stopPropagation()}>
                            <button
                              type="button"
                              onClick={() => setMenuConvoId(prev => prev === convo.id ? null : convo.id)}
                              className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100"
                              title="More actions"
                            >
                              <MoreVertical size={14} />
                            </button>
                            {menuConvoId === convo.id && (
                              <div className="absolute right-0 top-full mt-1 w-40 bg-white border border-slate-200 rounded-lg shadow-lg z-30 overflow-hidden">
                                <button
                                  type="button"
                                  onClick={() => openCustomerEdit(convo)}
                                  className="w-full text-left px-3 py-2 text-xs text-slate-700 hover:bg-slate-50"
                                >
                                  Edit customer
                                </button>
                                <button
                                  type="button"
                                  onClick={() => deleteFullConversation(convo)}
                                  className="w-full text-left px-3 py-2 text-xs text-red-600 hover:bg-red-50"
                                >
                                  Delete chat
                                </button>
                              </div>
                            )}
                          </div>
                          {convo.unread_count > 0 && <span className="min-w-[20px] h-5 rounded-full bg-blue-500 text-[10px] font-bold text-white flex items-center justify-center px-1.5">{convo.unread_count}</span>}
                          <ChevronRight size={16} className="text-slate-300" />
                        </div>
                      </div>
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${ch.lightBg} ${ch.text} font-medium inline-flex items-center gap-1`}><ChannelLogo channelKey={ch.key} size={11} />{ch.label}</span>
                        {isGroup && <span className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-200 bg-emerald-50 text-emerald-700 font-medium">Group</span>}
                        <span className="text-[11px] text-slate-400">{new Date(convo.last_message_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                        {convoSentiment && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${convoSentiment.accentClass}`}>
                            {convoSentiment.tone} <span className="opacity-60">({convoSentiment.percentage}%)</span>
                          </span>
                        )}
                      </div>
                      {convo.escalation_notice && (
                        <div className="mb-1.5 px-2 py-1 bg-red-50 border border-red-200 rounded-lg flex items-center gap-1.5">
                          <AlertTriangle size={10} className="text-red-500 flex-shrink-0" />
                          <span className="text-[10px] text-red-700 font-semibold uppercase tracking-wide truncate">{convo.escalation_notice}</span>
                        </div>
                      )}
                      {aiDisabled && (
                        <div className="mb-1.5 px-2 py-1 bg-amber-100 border border-amber-200 rounded-lg flex items-center gap-1.5" data-testid={`convo-${convo.id}-ai-disabled`}>
                          <AlertTriangle size={10} className="text-amber-600 flex-shrink-0" />
                          <span className="text-[10px] text-amber-800 font-semibold uppercase tracking-wide truncate">AI paused</span>
                        </div>
                      )}
                      <p className="text-xs text-slate-500 line-clamp-1">{convo.last_message}</p>
                    </div>
                  </div>
                </div>
              );
            })}
            {filteredConvos.length === 0 && (
              <div className="text-center py-12 text-slate-300">
                <MessageSquare size={32} className="mx-auto mb-2 opacity-40" />
                <p className="text-sm">{inboxFilterMeta?.empty || 'No conversations'}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Outbound Conversation Modal */}
      {outboundComposerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOutboundComposerOpen(false)} />
          <div className="relative w-full max-w-md bg-white rounded-xl shadow-xl p-5 z-10">
            <h3 className="text-lg font-semibold mb-1 flex items-center gap-2"><ChannelLogo channelKey={outboundChannel} size={20} />New {CHANNELS.find((c) => c.key === outboundChannel)?.label} Conversation</h3>
            <p className="text-xs text-slate-500 mb-3">Contact will be saved and outbound message will be sent immediately.</p>
            <div className="space-y-3">
              <input value={outboundName} onChange={(e) => setOutboundName(e.target.value)} placeholder="Contact name" className="w-full px-3 py-2 border rounded-lg" data-testid="outbound-name-input" />
              {outboundChannel === 'whatsapp' ? (
                <input value={outboundPhone} onChange={(e) => setOutboundPhone(e.target.value)} placeholder="Phone number (e.g. +15551234567)" className="w-full px-3 py-2 border rounded-lg" data-testid="outbound-phone-input" />
              ) : outboundChannel === 'email' ? (
                <input
                  type="email"
                  value={outboundRecipientId}
                  onChange={(e) => setOutboundRecipientId(e.target.value)}
                  placeholder="Recipient email (e.g. user@example.com)"
                  className="w-full px-3 py-2 border rounded-lg"
                  data-testid="outbound-recipient-id-input"
                />
              ) : (
                <input
                  value={outboundRecipientId}
                  onChange={(e) => setOutboundRecipientId(e.target.value)}
                  placeholder={outboundChannel === 'facebook' ? 'Facebook recipient ID (PSID)' : 'Instagram recipient ID'}
                  className="w-full px-3 py-2 border rounded-lg"
                  data-testid="outbound-recipient-id-input"
                />
              )}
              <textarea value={outboundMessage} onChange={(e) => setOutboundMessage(e.target.value)} placeholder="Initial outbound message" rows={3} className="w-full px-3 py-2 border rounded-lg resize-none" data-testid="outbound-message-input" />
              <div className="flex items-center justify-end gap-2">
                <button onClick={() => setOutboundComposerOpen(false)} className="px-3 py-2 rounded-lg border">Cancel</button>
                <button onClick={submitOutboundConversation} disabled={outboundSubmitting} className="px-3 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-50">
                  {outboundSubmitting ? 'Sending...' : 'Start & Send'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {customerEditOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50" onClick={() => setCustomerEditOpen(false)} />
          <div className="relative w-full max-w-md bg-white rounded-xl shadow-xl p-5 z-10">
            <h3 className="text-lg font-semibold mb-3">Edit Customer</h3>
            <div className="space-y-3">
              <input value={customerEditForm.name} onChange={(e) => setCustomerEditForm(prev => ({ ...prev, name: e.target.value }))} placeholder="Name" className="w-full px-3 py-2 border rounded-lg" />
              <input value={customerEditForm.email} onChange={(e) => setCustomerEditForm(prev => ({ ...prev, email: e.target.value }))} placeholder="Email" className="w-full px-3 py-2 border rounded-lg" />
              <input value={customerEditForm.phone} onChange={(e) => setCustomerEditForm(prev => ({ ...prev, phone: e.target.value }))} placeholder="Phone" className="w-full px-3 py-2 border rounded-lg" />
              <input value={customerEditForm.company} onChange={(e) => setCustomerEditForm(prev => ({ ...prev, company: e.target.value }))} placeholder="Company" className="w-full px-3 py-2 border rounded-lg" />
              <div className="flex items-center justify-end gap-2 pt-1">
                <button onClick={() => setCustomerEditOpen(false)} className="px-3 py-2 rounded-lg border">Cancel</button>
                <button onClick={saveCustomerEdit} disabled={customerEditLoading} className="px-3 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-50">
                  {customerEditLoading ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Desktop: Channel Columns */}
      {!maximized && (
        <div className="hidden lg:flex flex-1 overflow-x-auto border-r border-slate-100 bg-slate-50">
          <div className="flex h-full w-full min-w-[800px]" data-testid="channel-columns">
            {desktopChannels.map((ch) => {
              const items = grouped[ch.key] || [];
              const showNewButton = ch.key === 'whatsapp' || ch.key === 'facebook' || ch.key === 'instagram' || ch.key === 'email';
              return (
                <div key={ch.key} className="flex-1 flex flex-col border-r border-slate-100 last:border-r-0 min-w-[200px]" data-testid={`channel-col-${ch.key}`}>
                  <div className="px-4 py-3 border-b border-slate-100 bg-white flex items-center gap-2">
                    <ChannelLogo channelKey={ch.key} size={18} />
                    <span className="text-[13px] font-semibold text-slate-800">{ch.label}</span>
                    <span className="ml-auto text-[11px] font-medium text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">{items.length}</span>
                    {showNewButton && (
                      <div className="relative ml-1">
                        <button
                          onClick={(e) => { e.stopPropagation(); openOutboundComposer(ch.key); }}
                          onMouseEnter={() => {
                            clearTimeout(tooltipTimerRef.current);
                            setTooltipChannel(ch.key);
                            tooltipTimerRef.current = setTimeout(() => setTooltipChannel(null), 2000);
                          }}
                          onMouseLeave={() => {
                            clearTimeout(tooltipTimerRef.current);
                            setTooltipChannel(null);
                          }}
                          className="w-6 h-6 rounded-md border border-blue-300 bg-blue-50 text-blue-500 inline-flex items-center justify-center transition-all duration-200 hover:scale-110 hover:bg-blue-500 hover:text-white hover:border-blue-500 hover:shadow-md hover:shadow-blue-200"
                          data-testid={`new-${ch.key}-btn`}
                        >
                          <Plus size={13} className={`transition-transform duration-200 ${tooltipChannel === ch.key ? 'rotate-90' : ''}`} />
                        </button>
                        {tooltipChannel === ch.key && (
                          <span className="pointer-events-none absolute right-0 top-full mt-1.5 whitespace-nowrap rounded-lg bg-slate-800 text-white text-[10px] font-medium px-2.5 py-1.5 shadow-lg z-50 animate-in fade-in-0 duration-150">
                            ✦ Start new conversation
                            <span className="absolute bottom-full right-3 border-4 border-transparent border-b-slate-800" />
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex-1 overflow-y-auto p-2 space-y-2">
                    {items.map(convo => {
                      const convoSentiment = getSentimentMeta(convo.sentiment_score, convo.sentiment_label);
                      const isGroup = isWhatsappGroupConversation(convo);
                      const displayName = conversationDisplayName(convo);
                      const aiDisabled = isConversationAiDisabled(convo);
                      return (
                        <div
                          key={convo.id}
                          onClick={() => handleSelectConvo(convo)}
                          className={`rounded-xl p-3.5 border cursor-pointer transition-all duration-150 hover:shadow-md ${aiDisabled ? 'bg-amber-50/60 border-amber-200 hover:border-amber-300' : 'bg-white border-slate-100 hover:border-slate-200'}`}
                          data-testid={`convo-card-${convo.id}`}
                        >
                          <div className="flex items-start gap-2.5 mb-2">
                            <ContactAvatar entity={convo} name={displayName} channelMeta={ch} className="w-8 h-8" textClass="text-xs" />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center justify-between">
                                <span className="text-[13px] font-semibold text-slate-800 truncate">{displayName}</span>
                                <div className="flex items-center gap-1">
                                  <div className="relative" onClick={(e) => e.stopPropagation()}>
                                    <button
                                      type="button"
                                      onClick={() => setMenuConvoId(prev => prev === convo.id ? null : convo.id)}
                                      className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100"
                                      title="More actions"
                                    >
                                      <MoreVertical size={12} />
                                    </button>
                                    {menuConvoId === convo.id && (
                                      <div className="absolute right-0 top-full mt-1 w-40 bg-white border border-slate-200 rounded-lg shadow-lg z-30 overflow-hidden">
                                        <button
                                          type="button"
                                          onClick={() => openCustomerEdit(convo)}
                                          className="w-full text-left px-3 py-2 text-xs text-slate-700 hover:bg-slate-50"
                                        >
                                          Edit customer
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => deleteFullConversation(convo)}
                                          className="w-full text-left px-3 py-2 text-xs text-red-600 hover:bg-red-50"
                                        >
                                          Delete chat
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                  {convo.unread_count > 0 && <span className="min-w-[18px] h-[18px] rounded-full bg-blue-500 text-[9px] font-bold text-white flex items-center justify-center">{convo.unread_count}</span>}
                                </div>
                              </div>
                              <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                                <p className="text-[11px] text-slate-400">{new Date(convo.last_message_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
                                {convoSentiment && (
                                  <span className={`text-[10px] px-1.5 py-0.5 rounded-md border font-medium ${convoSentiment.accentClass}`}>
                                    {convoSentiment.tone} <span className="opacity-60">({convoSentiment.percentage}%)</span>
                                  </span>
                                )}
                                {isGroup && <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-emerald-200 bg-emerald-50 text-emerald-700 font-medium">Group</span>}
                              </div>
                            </div>
                          </div>
                          {convo.escalation_notice && (
                            <div className="mb-2 px-2 py-1.5 bg-red-50 border border-red-200 rounded-lg flex items-center gap-1.5">
                              <AlertTriangle size={11} className="text-red-500 flex-shrink-0" />
                              <span className="text-[10px] text-red-700 font-semibold uppercase tracking-wide truncate">{convo.escalation_notice}</span>
                            </div>
                          )}
                          {aiDisabled && (
                            <div className="mb-2 px-2 py-1.5 bg-amber-100 border border-amber-200 rounded-lg flex items-center gap-1.5" data-testid={`convo-${convo.id}-ai-disabled`}>
                              <AlertTriangle size={11} className="text-amber-600 flex-shrink-0" />
                              <span className="text-[10px] text-amber-800 font-semibold uppercase tracking-wide truncate">AI paused</span>
                            </div>
                          )}
                          <p className="text-xs text-slate-500 line-clamp-2 mb-2 leading-relaxed">{convo.last_message}</p>
                          <div className="flex flex-wrap gap-1">
                            {(convo.tags || []).slice(0, 3).map(tag => (
                              <span key={tag} className={`text-[10px] px-1.5 py-0.5 rounded-md border font-medium ${getTagColor(tag)}`}>{tag}</span>
                            ))}
                            {aiDisabled ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-amber-100 text-amber-700 border border-amber-200 font-medium">AI paused</span>
                            ) : convo.ai_handled && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-purple-50 text-purple-600 border border-purple-200 font-medium">AI</span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                    {items.length === 0 && (
                      <div className="text-center py-8 text-slate-300">
                        <MessageSquare size={24} className="mx-auto mb-2 opacity-40" />
                        <p className="text-xs">{inboxFilterMeta?.empty || 'No conversations'}</p>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Maximized Message View */}
      {maximized && selectedConvo && (
        <div className="flex-1 flex min-w-0 bg-white" data-testid="maximized-view">
          {/* Message Thread */}
          <div className="flex-1 flex flex-col min-w-0">
            {/* Header */}
            <div className="h-14 px-3 sm:px-5 flex items-center justify-between border-b border-slate-100 bg-white">
              <div className="flex items-center gap-2 sm:gap-3 min-w-0">
                <button onClick={handleBack} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors flex-shrink-0" data-testid="back-to-inbox-btn">
                  <ArrowLeft size={18} />
                </button>
                <button
                  type="button"
                  onClick={openCustomerProfileSidebar}
                  className="flex items-center gap-2 sm:gap-3 min-w-0 rounded-xl px-1.5 py-1 -mx-1.5 hover:bg-slate-50 transition-colors text-left"
                >
                  <ContactAvatar entity={selectedConvo} name={selectedConvoDisplayName} channelMeta={chInfo} className="w-8 h-8 sm:w-10 sm:h-10" textClass="text-xs sm:text-sm" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-slate-900 truncate">{selectedConvoDisplayName}</h3>
                      {selectedConvoIsGroup && <span className="inline-flex text-[10px] px-1.5 py-0.5 rounded border border-emerald-200 bg-emerald-50 text-emerald-700 font-medium">Group</span>}
                      <span className="hidden md:inline-flex items-center text-[10px] text-slate-400">View profile <ChevronRight size={11} className="ml-0.5" /></span>
                    </div>
                  </div>
                </button>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${chInfo?.lightBg} ${chInfo?.text} font-medium inline-flex items-center gap-1`}><ChannelLogo channelKey={chInfo?.key} size={11} />{chInfo?.label}</span>
                    {selectedConvoSentiment && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${selectedConvoSentiment.accentClass}`}>
                        {selectedConvoSentiment.tone} <span className="opacity-60">({selectedConvoSentiment.percentage}%)</span>
                      </span>
                    )}
                    <span className="text-[11px] text-slate-400 hidden sm:inline truncate">{selectedConvo.subject}</span>
                    {unificationMatch && (
                      <button onClick={() => setUnificationPanelOpen(!unificationPanelOpen)}
                        className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-gradient-to-r from-indigo-50 to-purple-50 border border-indigo-200 text-indigo-700 font-semibold hover:from-indigo-100 hover:to-purple-100 transition-all animate-pulse"
                        title="Cross-platform match found — click to review">
                        <span>🔗</span>
                        <span className="hidden sm:inline">Unified ({unificationMatch.members?.length || 0} profiles)</span>
                        <span className="sm:hidden">Match</span>
                      </button>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1 sm:gap-2 flex-shrink-0">
                {/* Escalation Notice - hidden on mobile, shown on sm+ */}
                {selectedConvo.escalation_notice && (
                  <div className="hidden md:flex items-center gap-1.5 px-2 py-1 bg-red-50 border border-red-200 rounded-lg">
                    <AlertTriangle size={12} className="text-red-500" />
                    <span className="text-[11px] text-red-700 font-semibold uppercase tracking-wide max-w-[240px] truncate">{selectedConvo.escalation_notice}</span>
                  </div>
                )}

                {/* AI Toggle Button */}
                <button
                  onClick={toggleAIMode}
                  disabled={aiToggling}
                  className={`flex items-center gap-1 sm:gap-1.5 px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-[11px] font-medium border transition-all duration-200 ${
                    selectedAiDisabled
                      ? 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'
                      : selectedConvo.ai_handled
                      ? 'bg-purple-50 text-purple-600 border-purple-200 hover:bg-purple-100'
                      : 'bg-slate-50 text-slate-500 border-slate-200 hover:bg-slate-100'
                  } disabled:opacity-50`}
                  data-testid="ai-toggle-btn"
                  title={selectedAiDisabled ? 'AI is paused - Click to enable AI automation' : selectedConvo.ai_handled ? 'AI is ON — Click to switch to human agent' : 'AI is OFF — Click to enable AI automation'}
                >
                  {aiToggling ? (
                    <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
                  ) : selectedAiDisabled ? (
                    <AlertTriangle size={14} />
                  ) : selectedConvo.ai_handled ? (
                    <Bot size={14} />
                  ) : (
                    <User size={14} />
                  )}
                  <span className="hidden sm:inline">{selectedAiDisabled ? 'AI Paused' : selectedConvo.ai_handled ? 'AI Auto' : 'Human'}</span>
                  <div className={`relative w-6 sm:w-8 h-3 sm:h-4 rounded-full transition-colors ${selectedAiToggleOn ? 'bg-purple-500' : selectedAiDisabled ? 'bg-amber-400' : 'bg-slate-300'}`}>
                    <span className={`absolute top-0.5 left-0.5 w-2 sm:w-3 h-2 sm:h-3 bg-white rounded-full transition-transform ${selectedAiToggleOn ? 'translate-x-3 sm:translate-x-4' : ''}`}></span>
                  </div>
                </button>

                {/* Customer info button - mobile */}
                <button 
                  onClick={() => setShowCustomerSidebar(!showCustomerSidebar)}
                  className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 lg:hidden"
                >
                  <UserCircle size={18} />
                </button>
              </div>
            </div>

            {/* Mobile Escalation Notice */}
            {selectedConvo.escalation_notice && (
              <div className="md:hidden px-3 py-2 bg-red-50 border-b border-red-200 flex items-center gap-1.5">
                <AlertTriangle size={12} className="text-red-500 flex-shrink-0" />
                <span className="text-[11px] text-red-700 font-semibold uppercase tracking-wide truncate">{selectedConvo.escalation_notice}</span>
              </div>
            )}

            {selectedAiDisabled && (
              <div className="px-3 sm:px-4 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2" data-testid="ai-paused-warning">
                <AlertTriangle size={13} className="text-amber-600 flex-shrink-0" />
                <span className="text-xs text-amber-800 font-medium">
                  {getAiDisabledReason(selectedConvo)}
                </span>
              </div>
            )}

            {channelDisconnected && (
              <div className="px-3 sm:px-4 py-2 bg-amber-50 border-b border-amber-200 flex items-center justify-between gap-3" data-testid="channel-disconnected-warning">
                <div className="flex min-w-0 items-center gap-2">
                  <AlertTriangle size={13} className="text-amber-600 flex-shrink-0" />
                  <span className="text-xs text-amber-800 font-medium truncate">{channelDisconnectMessage}</span>
                </div>
                <a href="/settings?tab=channels" className="text-xs font-semibold text-amber-900 underline decoration-amber-400 underline-offset-2">
                  Reconnect
                </a>
              </div>
            )}

            {/* Unification Match Banner */}
            {unificationMatch && unificationPanelOpen && (
              <div className="bg-gradient-to-r from-indigo-50 to-purple-50 border-b border-indigo-200">
                <div className="px-4 py-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm">🔗</span>
                      <p className="text-xs font-bold text-indigo-800">Cross-Platform Match Found</p>
                      <span className="text-[9px] text-indigo-500 bg-indigo-100 px-1.5 py-0.5 rounded-full">{unificationMatch.members?.length || 0} linked profiles</span>
                    </div>
                    <button onClick={() => setUnificationPanelOpen(false)} className="text-indigo-400 hover:text-indigo-600"><X size={14} /></button>
                  </div>
                  <p className="text-[10px] text-indigo-600 mb-3">
                    This customer has been identified across multiple platforms. All interactions are linked to a unified identity.
                  </p>
                  <div className="space-y-2">
                    {(unificationMatch.members || []).map((m) => (
                      <div key={m.customer_id} className="flex items-center gap-2 p-2 bg-white rounded-lg border border-indigo-100">
                        <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-xs font-bold text-indigo-600 flex-shrink-0">
                          {(m.name || '?').charAt(0)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-semibold text-slate-800 truncate">{m.name || 'Unknown'}</p>
                          <p className="text-[9px] text-slate-400 truncate">{m.email || m.phone || ''}</p>
                        </div>
                        {m.is_primary && <span className="text-[8px] font-bold text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">PRIMARY</span>}
                        <span className="text-[9px] text-slate-400">{m.match_method?.replace('auto_', '').replace('manual', 'Manual') || 'Match'}</span>
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-2 mt-3">
                    <button onClick={() => navigate('/unification')}
                      className="flex-1 py-2 text-xs font-medium text-indigo-600 bg-white border border-indigo-200 rounded-lg hover:bg-indigo-50 transition-colors">
                      Manage Profiles
                    </button>
                    <button onClick={() => {
                      if (unificationMatch?.id) navigate('/unification');
                    }} className="flex-1 py-2 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-500 transition-colors">
                      View Full History
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-3 sm:px-6 py-4 sm:py-5 space-y-3 sm:space-y-4 bg-slate-50/50" data-testid="message-thread">
              {messages.length === 0 && (
                <div className="flex h-full min-h-[220px] items-center justify-center text-center text-slate-400">
                  <div>
                    <MessageSquare size={30} className="mx-auto mb-2 opacity-40" />
                    <p className="text-sm font-medium text-slate-500">No messages yet</p>
                    <p className="text-xs">Start the conversation from the composer below.</p>
                  </div>
                </div>
              )}
              {messages.map((msg) => {
                const isCustomer = msg.sender_type === 'customer';
                const isAI = msg.sender_type === 'ai';
                const isSystem = msg.sender_type === 'system';
                const messageSentiment = getSentimentMeta(msg.sentiment_score, '', msg.sentiment_emotion);
                const imageAttachments = Array.isArray(msg.attachments) ? msg.attachments.filter((attachment) => attachment.type === 'image') : [];
                const videoAttachments = Array.isArray(msg.attachments) ? msg.attachments.filter((attachment) => attachment.type === 'video') : [];
                const reactions = Array.isArray(msg.reactions) ? msg.reactions : [];
                const messageTime = formatMessageTimestamp(msg.created_at);
                const messageContent = normalizeMessageText(msg.content);
                const groupName = messageGroupName(msg, selectedConvo);
                const participantName = messageParticipantName(msg);

                if (isSystem && isManualAiWithheldSystemMessage(messageContent)) {
                  return null;
                }

                if (isSystem) {
                  const isEscalationAlert = isEscalationSystemMessage(messageContent) || msg.is_alert;
                  return (
                    <div key={msg.id} className="flex justify-center animate-fadeIn" data-testid={`msg-${msg.id}`}>
                      <div className={`px-3 sm:px-4 py-2 rounded-full border max-w-[90%] sm:max-w-[80%] ${
                        isEscalationAlert ? 'bg-red-50 border-red-200' : 'bg-slate-100 border-slate-200'
                      }`}>
                        <p className={`text-[10px] sm:text-[11px] text-center font-medium ${
                          isEscalationAlert ? 'text-red-700 uppercase tracking-wide' : 'text-slate-500'
                        }`}>{messageContent}</p>
                      </div>
                    </div>
                  );
                }

                return (
                  <div key={msg.id} className={`flex ${isCustomer ? 'justify-start' : 'justify-end'} animate-fadeIn`} data-testid={`msg-${msg.id}`}>
                    <div className={editingMessageId === msg.id ? 'w-full max-w-[92%] sm:max-w-[760px]' : 'max-w-[85%] sm:max-w-[65%]'}>
                      <div className={`flex items-center gap-1.5 mb-1 ${isCustomer ? '' : 'justify-end'}`}>
                        <span className="text-[10px] text-slate-400 font-medium">{participantName}</span>
                        {groupName && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-200 bg-emerald-50 text-emerald-700 font-medium" data-testid={`msg-${msg.id}-group-name`}>
                            {groupName}
                          </span>
                        )}
                        {messageTime && <span className="text-[10px] text-slate-300">{messageTime}</span>}
                        {msg.edited_at && <span className="text-[10px] text-slate-300 italic">(edited)</span>}
                        {isAI && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-50 text-purple-500 font-medium">AI {msg.ai_confidence ? `${Math.round(msg.ai_confidence * 100)}%` : ''}</span>}
                        {!isCustomer && !isAI && getDeliveryStatusMeta(msg.delivery_status) && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${getDeliveryStatusMeta(msg.delivery_status).className}`}>
                            {getDeliveryStatusMeta(msg.delivery_status).label}
                          </span>
                        )}
                        <div className="flex items-center gap-1 ml-1">
                          <button
                            type="button"
                            onClick={() => startEditMessage(msg)}
                            disabled={messageActionLoadingId === msg.id}
                            className="p-1 rounded text-slate-400 hover:text-blue-600 hover:bg-blue-50 disabled:opacity-50"
                            title="Edit message"
                          >
                            <Pencil size={11} />
                          </button>
                          <button
                            type="button"
                            onClick={() => deleteConversationMessage(msg)}
                            disabled={messageActionLoadingId === msg.id}
                            className="p-1 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-50"
                            title="Delete message"
                          >
                            <Trash2 size={11} />
                          </button>
                        </div>
                      </div>
                      <div className={`rounded-2xl text-sm leading-relaxed overflow-hidden ${
                        isCustomer ? 'bg-white border border-slate-200 text-slate-700 rounded-bl-sm' :
                        isAI ? 'bg-purple-100 text-slate-800 border-2 border-purple-300 rounded-br-sm shadow-sm shadow-purple-100' :
                        'bg-blue-600 text-white rounded-br-sm shadow-sm'
                      }`}>
                        {imageAttachments.length > 0 && (
                          <div className={`grid gap-0.5 ${imageAttachments.length === 1 ? 'grid-cols-1' : 'grid-cols-2'}`}>
                            {imageAttachments.slice(0, 4).map((att, idx) => (
                              <ChatImageThumb
                                key={att.id || idx}
                                attachment={att}
                                onOpen={() => { setImagePreviewFailed(false); setImagePreview(att); }}
                                className={`${imageAttachments.length === 1 ? 'h-52 rounded-t-xl' : 'h-28'} ${idx === 0 && imageAttachments.length > 1 ? 'rounded-tl-xl' : ''} ${idx === 1 && imageAttachments.length > 1 ? 'rounded-tr-xl' : ''}`}
                              />
                            ))}
                          </div>
                        )}
                        {videoAttachments.length > 0 && (
                          <div className="grid gap-0.5">
                            {videoAttachments.slice(0, 2).map((att, idx) => (
                              <ChatVideoThumb
                                key={att.id || idx}
                                attachment={att}
                                onOpen={() => { setVideoPreviewFailed(false); setVideoPreview(att); }}
                              />
                            ))}
                          </div>
                        )}
                        <div className="px-3 sm:px-4 py-2.5 sm:py-3">
                          {editingMessageId === msg.id ? (
                            <div className="w-full min-w-[min(72vw,420px)] space-y-2">
                              <textarea
                                value={editingMessageContent}
                                onChange={(e) => setEditingMessageContent(e.target.value)}
                                rows={4}
                                className="w-full min-h-[112px] px-3 py-2.5 text-sm leading-relaxed rounded-lg border border-slate-300 text-slate-700 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400 resize-y"
                              />
                              <div className="flex items-center justify-end gap-1.5">
                                <button
                                  type="button"
                                  onClick={cancelEditMessage}
                                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-slate-100 text-slate-600 hover:bg-slate-200"
                                >
                                  <X size={11} /> Cancel
                                </button>
                                <button
                                  type="button"
                                  onClick={() => saveEditedMessage(msg)}
                                  disabled={!editingMessageContent.trim() || messageActionLoadingId === msg.id}
                                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                                >
                                  <Check size={11} /> Save
                                </button>
                              </div>
                            </div>
                          ) : (
                            <>
                              {isAI && <Sparkles size={12} className="inline-block text-purple-400 mr-1" />}
                              {messageContent}
                            </>
                          )}
                          {isAI && msg.raw_metadata ? (
                            <AiEngineFooter metadata={msg.raw_metadata} />
                          ) : null}
                        </div>
                      </div>
                      {messageSentiment && (
                          <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium ${messageSentiment.accentClass}`}>
                            {messageSentiment.tone} <span className="opacity-60">({messageSentiment.percentage}%)</span>
                          </span>
                        )}
                      {reactions.length > 0 && (
                        <div className={`mt-1 flex flex-wrap gap-1 ${isCustomer ? 'justify-start' : 'justify-end'}`} data-testid={`msg-${msg.id}-reactions`}>
                          {reactions.map((reaction) => (
                            <span
                              key={reaction.id}
                              className="inline-flex min-h-6 items-center rounded-full border border-slate-200 bg-white px-2 py-0.5 text-sm shadow-sm"
                              title={reaction.actor_type || 'reaction'}
                            >
                              {reaction.emoji}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>

            {/* Compose */}
            <div className="px-3 sm:px-5 py-3 border-t border-slate-100 bg-white">
              {composerAttachments.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-2">
                  {composerAttachments.map((attachment, index) => (
                    <div key={`${attachment.name || 'attachment'}-${index}`} className="relative w-16 h-16 rounded-xl overflow-hidden border border-slate-200 bg-slate-50">
                      {attachment.type === 'video' ? (
                        <video src={attachment.url} className="w-full h-full object-cover" muted preload="metadata" />
                      ) : (
                        <img src={attachment.url} alt={attachment.name || 'attachment'} className="w-full h-full object-cover" />
                      )}
                      <button
                        type="button"
                        onClick={() => setComposerAttachments((prev) => prev.filter((_, itemIndex) => itemIndex !== index))}
                        className="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/60 text-white flex items-center justify-center"
                      >
                        <X size={10} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {composerError && (
                <Alert variant="destructive" className="mb-3 border-red-200 bg-red-50 text-red-700">
                  <AlertDescription>{composerError}</AlertDescription>
                </Alert>
              )}
              <div className="flex items-center gap-2">
                <button onClick={triggerAI} disabled={aiLoading || channelDisconnected} className="p-2 sm:p-2.5 rounded-lg bg-purple-50 border border-purple-200 text-purple-500 hover:bg-purple-100 transition-colors disabled:opacity-50 flex-shrink-0" data-testid="ai-respond-btn" title={channelDisconnected ? 'Channel not connected' : 'Generate AI response'}>
                  {aiLoading ? <div className="w-4 h-4 border-2 border-purple-400 border-t-transparent rounded-full animate-spin"></div> : <Sparkles size={16} />}
                </button>
                <input
                  ref={composerFileRef}
                  type="file"
                  accept={CHAT_MEDIA_TYPES.join(',')}
                  multiple
                  className="hidden"
                  onChange={(e) => handleComposerFiles(e.target.files)}
                />
                <button
                  type="button"
                  onClick={() => composerFileRef.current?.click()}
                  className="p-2 sm:p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-slate-500 hover:bg-slate-100 transition-colors flex-shrink-0"
                  title="Attach media"
                >
                  <ImageIcon size={16} />
                </button>
                <input
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                  placeholder="Type a message..."
                  className="flex-1 px-3 sm:px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all"
                  data-testid="message-input"
                />
                <button onClick={sendMessage} disabled={(!newMessage.trim() && composerAttachments.length === 0) || sending || channelDisconnected} className="p-2 sm:p-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50 shadow-sm flex-shrink-0" data-testid="send-message-btn" title={channelDisconnected ? 'Channel not connected' : 'Send message'}>
                  {sending ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div> : <Send size={16} />}
                </button>
              </div>
            </div>
          </div>

          {imagePreview && (
            <div
              className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4"
              onClick={() => { setImagePreview(null); setImagePreviewFailed(false); }}
              role="dialog"
              aria-modal="true"
            >
              <div className="relative max-h-[92vh] w-full max-w-4xl" onClick={(event) => event.stopPropagation()}>
                <button
                  type="button"
                  onClick={() => { setImagePreview(null); setImagePreviewFailed(false); }}
                  className="absolute right-2 top-2 z-10 rounded-full bg-black/60 p-2 text-white hover:bg-black/75"
                  title="Close preview"
                >
                  <X size={18} />
                </button>
                {imagePreviewFailed ? (
                  <div className="flex min-h-[280px] items-center justify-center rounded-lg bg-white px-6 text-center text-sm font-medium text-slate-500 shadow-2xl">
                    Image unavailable
                  </div>
                ) : (
                  <img
                    src={imagePreview.url}
                    alt={imagePreview.name || 'attachment preview'}
                    className="max-h-[92vh] w-full rounded-lg object-contain shadow-2xl"
                    onError={() => setImagePreviewFailed(true)}
                  />
                )}
              </div>
            </div>
          )}

          {videoPreview && (
            <div
              className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4"
              onClick={() => { setVideoPreview(null); setVideoPreviewFailed(false); }}
              role="dialog"
              aria-modal="true"
            >
              <div className="relative max-h-[92vh] w-full max-w-4xl" onClick={(event) => event.stopPropagation()}>
                <button
                  type="button"
                  onClick={() => { setVideoPreview(null); setVideoPreviewFailed(false); }}
                  className="absolute right-2 top-2 z-10 rounded-full bg-black/60 p-2 text-white hover:bg-black/75"
                  title="Close preview"
                >
                  <X size={18} />
                </button>
                {videoPreviewFailed ? (
                  <div className="flex min-h-[280px] items-center justify-center rounded-lg bg-white px-6 text-center text-sm font-medium text-slate-500 shadow-2xl">
                    Video unavailable
                  </div>
                ) : (
                  <video
                    src={videoPreview.url}
                    className="max-h-[92vh] w-full rounded-lg bg-black shadow-2xl"
                    controls
                    autoPlay
                    onError={() => setVideoPreviewFailed(true)}
                  />
                )}
              </div>
            </div>
          )}

          {/* Customer Sidebar - opened on demand */}
              {showCustomerSidebar && (
                customerInfo && customerInfo.id === selectedConvo?.customer_id ? (
                  <>
              <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setShowCustomerSidebar(false)} />
              <div className={`
                fixed lg:relative inset-y-0 right-0 z-50 lg:z-0
                w-72 flex-shrink-0 border-l border-slate-100 bg-white overflow-y-auto
                transform transition-transform duration-300 translate-x-0
              `} data-testid="customer-sidebar" ref={customerSidebarRef}>
                <div className="h-14 px-4 flex items-center justify-between border-b border-slate-100">
                  <span className="text-sm font-semibold text-slate-900">Contact Info</span>
                  <button onClick={() => setShowCustomerSidebar(false)} className="p-1.5 rounded-md hover:bg-slate-100 text-slate-400">
                    <X size={18} />
                  </button>
                </div>
                <div className="p-5">
                  <div className="text-center mb-5">
                    <div className="mx-auto mb-2 w-14">
                      <ContactAvatar entity={customerInfo} name={customerInfo.name} channelMeta={chInfo} className="w-14 h-14" textClass="text-lg" />
                    </div>
                    <h4 className="text-sm font-semibold text-slate-900">{customerInfo.name}</h4>
                    <p className="text-[11px] text-slate-400">{customerInfo.company}</p>
                    <span className={`inline-block mt-1.5 text-[10px] px-2 py-0.5 rounded-full font-medium border capitalize ${customerInfo.segment === 'vip' ? 'bg-amber-50 text-amber-600 border-amber-200' : customerInfo.segment === 'enterprise' ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                      {customerInfo.segment}
                    </span>
                  </div>

                  <div className="space-y-4">
                    <div className="space-y-1.5">
                      {customerInfo.email && <p className="text-xs text-slate-600 flex items-center gap-2"><Mail size={12} className="text-slate-400" /> <span className="truncate">{customerInfo.email}</span></p>}
                      {customerInfo.phone && isLikelyValidDisplayPhone(customerInfo.phone) && (
                        <p className="text-xs text-slate-600 flex items-center gap-2"><Phone size={12} className="text-slate-400" /> {customerInfo.phone}</p>
                      )}
                      {customerInfo.phone && !isLikelyValidDisplayPhone(customerInfo.phone) && (
                        <p className="text-xs text-amber-800 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5">
                          <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
                          <span>Stored phone needs review before WhatsApp replies.</span>
                        </p>
                      )}
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div className="bg-slate-50 rounded-lg p-2.5 text-center">
                        <p className="text-base font-bold text-slate-900">{customerInfo.total_conversations}</p>
                        <p className="text-[10px] text-slate-400">Convos</p>
                      </div>
                      <div className="bg-slate-50 rounded-lg p-2.5 text-center">
                        <p className="text-base font-bold text-slate-900">${((customerInfo.lifetime_value || 0) / 1000).toFixed(0)}k</p>
                        <p className="text-[10px] text-slate-400">LTV</p>
                      </div>
                    </div>

                    {customerInfo.churn_risk && (
                      <div className={`p-3 rounded-lg border ${customerInfo.churn_risk.risk_level === 'critical' || customerInfo.churn_risk.risk_level === 'high' ? 'bg-red-50 border-red-200' : 'bg-slate-50 border-slate-200'}`}>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[11px] font-medium text-slate-600 capitalize">Churn: {customerInfo.churn_risk.risk_level}</span>
                          <span className="text-[11px] font-bold text-slate-800">{Math.round(customerInfo.churn_risk.risk_score * 100)}%</span>
                        </div>
                        <div className="h-1.5 bg-white rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${customerInfo.churn_risk.risk_score > 0.7 ? 'bg-red-500' : customerInfo.churn_risk.risk_score > 0.3 ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${customerInfo.churn_risk.risk_score * 100}%` }}></div>
                        </div>
                      </div>
                    )}

                    <div>
                      <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-1.5">Lifecycle</p>
                      <div className="flex flex-wrap gap-1.5">
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200 capitalize">
                          {customerInfo.lifecycle_stage || 'customer'}
                        </span>
                        {(customerInfo.channels || []).map(channel => (
                          <span key={channel} className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 border border-blue-200 capitalize">
                            {formatInboxChannel(channel)}
                          </span>
                        ))}
                      </div>
                    </div>

                    {selectedConvo?.ai_handled && (() => {
                      const rawConfidence = selectedConvo.ai_confidence != null ? Number(selectedConvo.ai_confidence) : null;
                      const confidencePct = rawConfidence != null ? Math.round(rawConfidence * (rawConfidence <= 1 ? 100 : 1)) : null;
                      const isResolved = selectedConvo.status === 'resolved';
                      const isEscalated = selectedConvo.status === 'escalated' || Boolean(selectedConvo.escalation_notice);
                      let aiNature = 'Moderate';
                      let natureCls = 'bg-amber-50 text-amber-700 border-amber-200';
                      if (isEscalated || (confidencePct != null && confidencePct < 40)) {
                        aiNature = 'Low'; natureCls = 'bg-red-50 text-red-700 border-red-200';
                      } else if (confidencePct != null && confidencePct >= 80 && isResolved) {
                        aiNature = 'Excellent'; natureCls = 'bg-emerald-50 text-emerald-700 border-emerald-200';
                      } else if (confidencePct != null && confidencePct >= 60 && isResolved) {
                        aiNature = 'Good'; natureCls = 'bg-blue-50 text-blue-700 border-blue-200';
                      }
                      return (
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-1.5">AI Performance</p>
                          <div className="bg-purple-50 border border-purple-100 rounded-lg p-2.5 space-y-2">
                            {confidencePct != null && (
                              <div>
                                <div className="flex items-center justify-between mb-1">
                                  <span className="text-[10px] text-slate-500 font-medium">AI Score</span>
                                  <span className="text-[10px] font-bold text-purple-700">{confidencePct}%</span>
                                </div>
                                <div className="h-1.5 bg-white rounded-full overflow-hidden">
                                  <div className={`h-full rounded-full ${confidencePct >= 80 ? 'bg-emerald-500' : confidencePct >= 60 ? 'bg-blue-500' : confidencePct >= 40 ? 'bg-amber-500' : 'bg-red-500'}`} style={{ width: `${confidencePct}%` }} />
                                </div>
                              </div>
                            )}
                            <div className="flex items-center justify-between">
                              <span className="text-[10px] text-slate-500 font-medium">AI Nature</span>
                              <span className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold ${natureCls}`}>{aiNature}</span>
                            </div>
                          </div>
                        </div>
                      );
                    })()}

                    {customerInfo.social_profiles && Object.keys(customerInfo.social_profiles).length > 0 && (
                      <div>
                        <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-1.5">Linked Channels</p>
                        <div className="space-y-1.5">
                          {Object.keys(customerInfo.social_profiles).map((platform) => (
                            <div key={platform} className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">
                              <p className="text-[10px] font-semibold text-slate-500 capitalize">{platform}</p>
                              <p className="text-[11px] text-slate-700">Connected</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div>
                      <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-1.5">Tags</p>
                      <div className="flex flex-wrap gap-1">
                        {(customerInfo.tags || []).map(tag => (
                          <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200"><Tag size={8} className="inline mr-0.5" />{tag}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
                  </>
                ) : (
                  <>
              <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setShowCustomerSidebar(false)} />
              <div className="
                fixed lg:relative inset-y-0 right-0 z-50 lg:z-0
                w-72 flex-shrink-0 border-l border-slate-100 bg-white
                flex items-center justify-center px-4 text-center
              " data-testid="customer-sidebar-loading">
                <span className="text-xs font-medium text-slate-400">Loading contact...</span>
              </div>
                  </>
                )
              )}
        </div>
      )}
    </div>
    {confirmDialog}
    </>
  );
}
