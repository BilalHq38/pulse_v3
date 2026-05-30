import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { MessageCircle, X, Send, Loader2 } from 'lucide-react';
import { BACKEND_BASE_URL, resolveMediaUrl } from '@/lib/backend-url';
import './ChatWidget.css';

const STORAGE_KEY = 'pulse_widget_session';
const HISTORY_KEY = 'pulse_widget_history';
const MAX_HISTORY = 50;

const WELCOME_MESSAGES = [
  (name) => `Hi${name ? ` ${name}` : ''}! Welcome — feel free to ask me anything.`,
  (name) => `Hello${name ? ` ${name}` : ''}! What can I help you with today?`,
  (name) => `Hey${name ? ` ${name}` : ''}! Great to have you here. What brings you in?`,
  (name) => `Hi${name ? ` ${name}` : ''}! Ask me anything about our products or services.`,
  (name) => `Welcome${name ? `, ${name}` : ''}! Let me know what you're looking for.`,
  (name) => `Hello${name ? ` ${name}` : ''}! I'm here if you have any questions.`,
];

function pickWelcomeMessage(senderName) {
  const first = senderName ? senderName.split(' ')[0] : '';
  const idx = Math.floor(Math.random() * WELCOME_MESSAGES.length);
  return WELCOME_MESSAGES[idx](first);
}

function loadSession() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch {
    // ignore
  }
  return null;
}

function saveSession(session) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // ignore
  }
}

function loadHistory() {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .slice(-MAX_HISTORY)
      .map((m) => ({ ...m, ts: m.ts ? new Date(m.ts) : new Date() }));
  } catch {
    return [];
  }
}

function safeParseJson(value) {
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function saveHistory(messages) {
  try {
    const serializable = messages.slice(-MAX_HISTORY).map((m) => ({
      ...m,
      ts: m.ts instanceof Date ? m.ts.toISOString() : m.ts,
    }));
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(serializable));
  } catch {
    // ignore
  }
}

function genId() {
  return 'w' + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function formatTime(date) {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(date);
  } catch {
    return '';
  }
}

export default function ChatWidget({
  companyId: companyIdProp,
  defaultOpen = false,
  senderName: initialSenderName = 'Website Visitor',
  senderContact: initialSenderContact = '',
  title = 'Chat with us',
  subtitle = 'Typically replies within a minute',
}) {
  const envTenant =
    (typeof process !== 'undefined' && process.env && process.env.REACT_APP_DEFAULT_TENANT_ID) || '';
  const companyId = (companyIdProp || envTenant || '').trim();

  const [open, setOpen] = useState(defaultOpen);
  const [messages, setMessages] = useState(() => loadHistory());
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [lastFailedText, setLastFailedText] = useState('');
  const [session, setSession] = useState(() => loadSession() || { session_id: genId() });
  const listRef = useRef(null);

  const senderName = session.sender_name || initialSenderName;
  const senderContact = session.sender_contact || initialSenderContact;

  useEffect(() => {
    saveSession(session);
  }, [session]);

  useEffect(() => {
    saveHistory(messages);
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open, sending]);

  useEffect(() => {
    if (!open) return;
    if (messages.length === 0) {
      setMessages([
        {
          id: genId(),
          role: 'assistant',
          text: pickWelcomeMessage(senderName),
          ts: new Date(),
        },
      ]);
    }
  }, [open, messages.length, senderName]);

  const endpoint = useMemo(() => `${BACKEND_BASE_URL}/api/webhooks/web-chat`, []);

  const sendMessage = useCallback(
    async (text) => {
      const trimmed = (text || '').trim();
      if (!trimmed) return;
      if (!companyId) {
        setError('Widget misconfigured: missing company_id');
        return;
      }
      const clientMessageId = genId();
      const userMessage = { id: clientMessageId, role: 'user', text: trimmed, ts: new Date() };
      setMessages((prev) => [...prev, userMessage]);
      setInput('');
      setSending(true);
      setError('');
      setLastFailedText('');

      try {
        const payload = {
          company_id: companyId,
          session_id: session.session_id,
          message: trimmed,
          sender_name: senderName,
          sender_contact: senderContact,
          page_url: typeof window !== 'undefined' ? window.location.href : '',
          client_message_id: clientMessageId,
          widget_version: 'pulse-widget/1.0',
        };
        const headers = { 'Content-Type': 'application/json' };
        const widgetKey =
          (typeof process !== 'undefined' && process.env && process.env.REACT_APP_WIDGET_KEY) || '';
        if (widgetKey) headers['X-Pulse-Widget-Key'] = widgetKey;

        const res = await axios.post(endpoint, payload, { headers, withCredentials: false });
        const data = res?.data || {};
        const aiReply =
          data.ai_reply ||
          data.ai_response ||
          data.response ||
          data.message ||
          (Array.isArray(data.messages) ? data.messages.find((m) => m.sender_type === 'ai')?.content : '');

        // The engine path exposes product_links on the ai_message metadata or
        // at the top level of the webhook response (legacy path leaves both
        // empty). We surface them as small "View product" cards under the
        // bubble so customers can jump straight to the product page.
        const aiMessage = data.ai_message || {};
        const rawMetadata = aiMessage.raw_metadata
          ? (typeof aiMessage.raw_metadata === 'string'
              ? safeParseJson(aiMessage.raw_metadata)
              : aiMessage.raw_metadata)
          : {};
        const productLinks = Array.isArray(data.product_links)
          ? data.product_links
          : (Array.isArray(rawMetadata?.product_links) ? rawMetadata.product_links : []);
        const companyLink = String(data.company_link || '').trim();
        const productImageUrls = Array.isArray(data.product_image_urls) ? data.product_image_urls : (
          // Fallback: extract image_url from product_links if product_image_urls is absent
          Array.isArray(data.product_links)
            ? data.product_links.map((pl) => pl.image_url).filter(Boolean)
            : []
        );

        // Add image messages FIRST, then the text message.
        if (productImageUrls.length > 0) {
          const imgProductLinks = Array.isArray(data.product_links) ? data.product_links : [];
          setMessages((prev) => [
            ...prev,
            ...productImageUrls.map((url, idx) => {
              const name = (imgProductLinks[idx] || {}).name || '';
              return {
                id: genId(),
                role: 'assistant',
                imageUrl: String(url),
                imageCaption: name ? `Here's an image of ${name}:` : 'Here\'s the product image:',
                ts: new Date(),
              };
            }),
          ]);
        }

        if (aiReply) {
          setMessages((prev) => [
            ...prev,
            {
              id: genId(),
              role: 'assistant',
              text: String(aiReply),
              productLinks,
              companyLink,
              ts: new Date(),
            },
          ]);
        } else if (data?.status === 'received' || data?.status === 'ok') {
          setMessages((prev) => [
            ...prev,
            {
              id: genId(),
              role: 'assistant',
              text: 'Thanks! A team member will reply here shortly.',
              ts: new Date(),
            },
          ]);
        } else if (data?.status === 'error') {
          setError(String(data?.detail || 'Chat service returned an error'));
        }
      } catch (err) {
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          'Failed to send message';
        setError(String(detail));
        setLastFailedText(trimmed);
      } finally {
        setSending(false);
      }
    },
    [companyId, endpoint, senderContact, senderName, session.session_id],
  );

  const handleSubmit = (e) => {
    e?.preventDefault?.();
    if (sending) return;
    sendMessage(input);
  };

  const identify = (name, contact) => {
    setSession((prev) => ({ ...prev, sender_name: name, sender_contact: contact }));
  };

  return (
    <div className="pulse-widget-root" aria-live="polite">
      {open && (
        <div className="pulse-widget-panel" role="dialog" aria-label={title}>
          <div className="pulse-widget-header">
            <div>
              <div className="pulse-widget-title">{title}</div>
              <div className="pulse-widget-subtitle">{subtitle}</div>
            </div>
            <button
              type="button"
              className="pulse-widget-close"
              onClick={() => setOpen(false)}
              aria-label="Close chat"
            >
              <X size={18} />
            </button>
          </div>

          {!session.sender_contact && (
            <IdentifyForm onSubmit={identify} initialName={initialSenderName} />
          )}

          <div className="pulse-widget-messages" ref={listRef}>
            {messages.map((m) => (
              <div key={m.id} className={`pulse-widget-msg pulse-widget-msg-${m.role}`}>
                {m.role === 'assistant' && m.imageUrl ? (
                  <div className="pulse-widget-image-block">
                    {m.imageCaption ? (
                      <div className="pulse-widget-bubble pulse-widget-image-caption">{m.imageCaption}</div>
                    ) : null}
                    <img
                      src={resolveMediaUrl(m.imageUrl)}
                      alt="Product"
                      className="pulse-widget-chat-image"
                      onError={(e) => {
                        e.currentTarget.style.display = 'none';
                        const fb = e.currentTarget.nextSibling;
                        if (fb) fb.style.display = 'block';
                      }}
                    />
                    <div className="pulse-widget-bubble" style={{ display: 'none', marginTop: '4px' }}>
                      {m.imageCaption || 'Product image'}
                    </div>
                  </div>
                ) : null}
                {m.role === 'assistant' && !m.imageUrl && Array.isArray(m.productLinks) && m.productLinks.length > 0 ? (
                  <div className="pulse-widget-product-cards" data-testid="widget-product-links">
                    {m.productLinks.map((link, idx) => {
                      const imgSrc = link.image_url ? resolveMediaUrl(link.image_url) : '';
                      // Resolve relative paths (e.g. /c/company/product/slug) to
                      // absolute using the current page origin so the link always works.
                      let href = String(link.url || '').trim();
                      if (href && href.startsWith('/')) {
                        href = `${window.location.origin}${href}`;
                      }
                      const CardTag = href ? 'a' : 'div';
                      const cardProps = href
                        ? { href, target: '_blank', rel: 'noopener noreferrer' }
                        : {};
                      return (
                        <CardTag
                          key={`${link.product_id || idx}-${idx}`}
                          {...cardProps}
                          className="pulse-widget-product-card"
                        >
                          {imgSrc ? (
                            <img
                              src={imgSrc}
                              alt={link.name || 'Product'}
                              className="pulse-widget-product-card-img"
                            />
                          ) : (
                            <div className="pulse-widget-product-card-img pulse-widget-product-card-img-placeholder" />
                          )}
                          <span className="pulse-widget-product-card-name">
                            {link.name || 'View product'}
                          </span>
                        </CardTag>
                      );
                    })}
                  </div>
                ) : null}
                {!m.imageUrl ? <div className="pulse-widget-bubble">{m.text}</div> : null}
                {!m.imageUrl && m.role === 'assistant' && m.companyLink ? (
                  <div className="pulse-widget-company-link">
                    <a href={m.companyLink} target="_blank" rel="noopener noreferrer">
                      Explore more on our website
                    </a>
                  </div>
                ) : null}
                <div className="pulse-widget-ts">{formatTime(m.ts)}</div>
              </div>
            ))}
            {sending && (
              <div className="pulse-widget-msg pulse-widget-msg-assistant">
                <div className="pulse-widget-bubble pulse-widget-typing">
                  <span /> <span /> <span />
                </div>
              </div>
            )}
          </div>

          {error && (
            <div className="pulse-widget-error">
              <span>{error}</span>
              {lastFailedText && (
                <button
                  type="button"
                  className="pulse-widget-retry"
                  onClick={() => sendMessage(lastFailedText)}
                >
                  Retry
                </button>
              )}
            </div>
          )}

          <form className="pulse-widget-input" onSubmit={handleSubmit}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type your message..."
              aria-label="Message"
              disabled={sending}
            />
            <button type="submit" disabled={sending || !input.trim()} aria-label="Send message">
              {sending ? <Loader2 size={18} className="pulse-widget-spin" /> : <Send size={18} />}
            </button>
          </form>
        </div>
      )}

      <button
        type="button"
        className="pulse-widget-bubble-btn"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? 'Close chat' : 'Open chat'}
      >
        {open ? <X size={22} /> : <MessageCircle size={22} />}
      </button>
    </div>
  );
}

function IdentifyForm({ onSubmit, initialName }) {
  const [name, setName] = useState(initialName || '');
  const [contact, setContact] = useState('');
  const [submitted, setSubmitted] = useState(false);

  if (submitted) return null;

  return (
    <form
      className="pulse-widget-identify"
      onSubmit={(e) => {
        e.preventDefault();
        if (!contact.trim()) return;
        onSubmit(name.trim() || 'Visitor', contact.trim());
        setSubmitted(true);
      }}
    >
      <div className="pulse-widget-identify-row">
        <input
          type="text"
          placeholder="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          type="text"
          placeholder="Email or phone"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          required
        />
      </div>
      <button type="submit">Start chat</button>
    </form>
  );
}
