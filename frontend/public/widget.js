/**
 * Pulse Engine Web Chat Widget
 */

(function () {
  'use strict';

  const PULSE_API = window.PULSE_ENGINE_API || 'http://localhost:8000/api';
  const CHAT_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
  const MAX_CHAT_IMAGES = 4;
  const MAX_CHAT_IMAGE_SIZE_MB = 8;

  const styles = `
    .pulse-chat-widget { position: fixed; bottom: 24px; right: 24px; z-index: 999999; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .pulse-chat-button { width: 60px; height: 60px; border-radius: 50%; background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%); border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 20px rgba(59,130,246,0.4); transition: transform 0.2s, box-shadow 0.2s; }
    .pulse-chat-button:hover { transform: scale(1.05); box-shadow: 0 6px 25px rgba(59,130,246,0.5); }
    .pulse-chat-button svg { width: 28px; height: 28px; fill: white; }
    .pulse-chat-window { position: absolute; bottom: 70px; right: 0; width: 380px; height: 560px; background: white; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.15); display: none; flex-direction: column; overflow: hidden; }
    .pulse-chat-window.open { display: flex; }
    .pulse-chat-header { padding: 16px 20px; background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%); color: white; display: flex; align-items: center; gap: 12px; }
    .pulse-chat-header-avatar { width: 40px; height: 40px; border-radius: 50%; background: rgba(255,255,255,0.2); display: flex; align-items: center; justify-content: center; }
    .pulse-chat-header-info h3 { margin: 0; font-size: 15px; font-weight: 600; }
    .pulse-chat-header-info p { margin: 2px 0 0; font-size: 12px; opacity: 0.85; }
    .pulse-chat-close { margin-left: auto; background: none; border: none; color: white; cursor: pointer; padding: 4px; }
    .pulse-chat-messages { flex: 1; overflow-y: auto; padding: 16px; background: #f8fafc; }
    .pulse-chat-message { margin-bottom: 12px; display: flex; flex-direction: column; }
    .pulse-chat-message.customer { align-items: flex-end; }
    .pulse-chat-message.agent, .pulse-chat-message.ai { align-items: flex-start; }
    .pulse-chat-bubble { max-width: 82%; padding: 10px 14px; border-radius: 16px; font-size: 14px; line-height: 1.4; overflow: hidden; white-space: pre-line; }
    .pulse-chat-message.customer .pulse-chat-bubble { background: #3b82f6; color: white; border-bottom-right-radius: 4px; }
    .pulse-chat-message.agent .pulse-chat-bubble, .pulse-chat-message.ai .pulse-chat-bubble { background: white; color: #1e293b; border: 1px solid #e2e8f0; border-bottom-left-radius: 4px; }
    .pulse-chat-message.ai .pulse-chat-bubble { background: #f5f3ff; border-color: #ddd6fe; }
    .pulse-chat-ai-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: #7c3aed; margin-bottom: 4px; }
    .pulse-chat-time { font-size: 10px; color: #94a3b8; margin-top: 4px; }
    .pulse-chat-input-wrap { padding: 12px 16px; border-top: 1px solid #e2e8f0; background: white; }
    .pulse-chat-attachment-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
    .pulse-chat-attachment-preview { position: relative; width: 56px; height: 56px; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; background: #f8fafc; }
    .pulse-chat-attachment-preview img { width: 100%; height: 100%; object-fit: cover; }
    .pulse-chat-attachment-remove { position: absolute; top: 4px; right: 4px; width: 18px; height: 18px; border-radius: 50%; border: none; background: rgba(15,23,42,0.65); color: white; cursor: pointer; font-size: 11px; }
    .pulse-chat-input-area { display: flex; gap: 8px; align-items: center; }
    .pulse-chat-input { flex: 1; padding: 10px 14px; border: 1px solid #e2e8f0; border-radius: 24px; font-size: 14px; outline: none; transition: border-color 0.2s; }
    .pulse-chat-input:focus { border-color: #3b82f6; }
    .pulse-chat-attach, .pulse-chat-send { width: 40px; height: 40px; border-radius: 50%; border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.2s; }
    .pulse-chat-attach { background: #eff6ff; color: #2563eb; }
    .pulse-chat-attach:hover { background: #dbeafe; }
    .pulse-chat-send { background: #3b82f6; }
    .pulse-chat-send:hover { background: #2563eb; }
    .pulse-chat-send:disabled { background: #94a3b8; cursor: not-allowed; }
    .pulse-chat-send svg, .pulse-chat-attach svg { width: 18px; height: 18px; }
    .pulse-chat-send svg { fill: white; }
    .pulse-chat-welcome { text-align: center; padding: 40px 20px; }
    .pulse-chat-welcome h4 { margin: 0 0 8px; font-size: 16px; color: #1e293b; }
    .pulse-chat-welcome p { margin: 0; font-size: 13px; color: #64748b; }
    .pulse-chat-typing-indicator { display: flex; gap: 4px; padding: 12px 16px; }
    .pulse-chat-typing-indicator span { width: 8px; height: 8px; background: #94a3b8; border-radius: 50%; animation: pulse-typing 1.4s infinite ease-in-out; }
    .pulse-chat-typing-indicator span:nth-child(1) { animation-delay: 0s; }
    .pulse-chat-typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
    .pulse-chat-typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
    .pulse-chat-images { display: grid; gap: 4px; margin-bottom: 8px; }
    .pulse-chat-images.multi { grid-template-columns: repeat(2, 1fr); }
    .pulse-chat-images a { display: block; overflow: hidden; border-radius: 12px; }
    .pulse-chat-images img { width: 100%; max-height: 180px; object-fit: cover; display: block; }
    .pulse-chat-error { margin-bottom: 10px; padding: 8px 10px; border-radius: 10px; background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; font-size: 11px; }
    @keyframes pulse-typing { 0%,60%,100% { transform: translateY(0); } 30% { transform: translateY(-6px); } }
  `;

  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
      reader.readAsDataURL(file);
    });
  }

  function normalizeAttachments(attachments) {
    if (!Array.isArray(attachments)) return [];
    return attachments
      .map((attachment, index) => ({
        id: attachment.id || `att-${index}`,
        type: attachment.type || attachment.file_type || 'unknown',
        url: attachment.url || attachment.file_url || '',
        name: attachment.name || attachment.file_name || '',
        size: Number(attachment.size || attachment.file_size || 0),
      }))
      .filter((attachment) => attachment.url);
  }

  class PulseChat {
    constructor(config) {
      this.config = {
        companyId: config.companyId || 'default',
        primaryColor: config.primaryColor || '#3b82f6',
        welcomeMessage: config.welcomeMessage || 'Hi! How can we help you today?',
        agentName: config.agentName || 'Support Team',
        widgetKey: config.widgetKey || window.PULSE_WIDGET_KEY || '',
        ...config,
      };
      this.sessionId = this._getSessionId();
      this.conversationId = null;
      this.isOpen = false;
      this.isTyping = false;
      this.pendingAttachments = [];

      this._injectStyles();
      this._createWidget();
      this._bindEvents();
    }

    _getSessionId() {
      let sessionId = localStorage.getItem('pulse_chat_session');
      if (!sessionId) {
        sessionId = `web_${Math.random().toString(36).slice(2, 11)}`;
        localStorage.setItem('pulse_chat_session', sessionId);
      }
      return sessionId;
    }

    _injectStyles() {
      const styleEl = document.createElement('style');
      styleEl.textContent = styles;
      document.head.appendChild(styleEl);
    }

    _createWidget() {
      const widget = document.createElement('div');
      widget.className = 'pulse-chat-widget';
      widget.innerHTML = `
        <div class="pulse-chat-window">
          <div class="pulse-chat-header">
            <div class="pulse-chat-header-avatar">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="white">
                <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/>
              </svg>
            </div>
            <div class="pulse-chat-header-info">
              <h3>${this.config.agentName}</h3>
              <p>AI-powered. Usually replies instantly.</p>
            </div>
            <button class="pulse-chat-close">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="white">
                <path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
              </svg>
            </button>
          </div>
          <div class="pulse-chat-messages">
            <div class="pulse-chat-welcome">
              <h4>${this.config.welcomeMessage}</h4>
              <p>Send a message or image and we will respond as soon as possible.</p>
            </div>
          </div>
          <div class="pulse-chat-input-wrap">
            <div class="pulse-chat-error" style="display:none;"></div>
            <div class="pulse-chat-attachment-row" style="display:none;"></div>
            <div class="pulse-chat-input-area">
              <input type="file" class="pulse-chat-file" accept="${CHAT_IMAGE_TYPES.join(',')}" multiple style="display:none;">
              <button class="pulse-chat-attach" type="button" title="Attach image">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
              </button>
              <input type="text" class="pulse-chat-input" placeholder="Type your message...">
              <button class="pulse-chat-send" disabled>
                <svg viewBox="0 0 24 24"><path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z"/></svg>
              </button>
            </div>
          </div>
        </div>
        <button class="pulse-chat-button">
          <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/></svg>
        </button>
      `;

      document.body.appendChild(widget);
      this.widget = widget;
      this.window = widget.querySelector('.pulse-chat-window');
      this.messagesContainer = widget.querySelector('.pulse-chat-messages');
      this.input = widget.querySelector('.pulse-chat-input');
      this.sendBtn = widget.querySelector('.pulse-chat-send');
      this.attachBtn = widget.querySelector('.pulse-chat-attach');
      this.fileInput = widget.querySelector('.pulse-chat-file');
      this.errorEl = widget.querySelector('.pulse-chat-error');
      this.previewRow = widget.querySelector('.pulse-chat-attachment-row');
    }

    _bindEvents() {
      this.widget.querySelector('.pulse-chat-button').addEventListener('click', () => this.toggle());
      this.widget.querySelector('.pulse-chat-close').addEventListener('click', () => this.close());
      this.input.addEventListener('input', () => this._syncSendState());
      this.input.addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && !event.shiftKey && (this.input.value.trim() || this.pendingAttachments.length)) {
          event.preventDefault();
          this.sendMessage();
        }
      });
      this.sendBtn.addEventListener('click', () => {
        if (this.input.value.trim() || this.pendingAttachments.length) this.sendMessage();
      });
      this.attachBtn.addEventListener('click', () => this.fileInput.click());
      this.fileInput.addEventListener('change', async (event) => {
        await this._handleFiles(event.target.files);
        this.fileInput.value = '';
      });
    }

    _syncSendState() {
      this.sendBtn.disabled = !this.input.value.trim() && this.pendingAttachments.length === 0;
    }

    _showError(message) {
      if (!message) {
        this.errorEl.style.display = 'none';
        this.errorEl.textContent = '';
        return;
      }
      this.errorEl.style.display = 'block';
      this.errorEl.textContent = message;
    }

    async _handleFiles(fileList) {
      const files = Array.from(fileList || []);
      if (!files.length) return;
      this._showError('');
      const remainingSlots = Math.max(0, MAX_CHAT_IMAGES - this.pendingAttachments.length);
      const selectedFiles = files.slice(0, remainingSlots);
      if (selectedFiles.length < files.length) {
        this._showError(`You can send up to ${MAX_CHAT_IMAGES} images at once.`);
      }
      for (const file of selectedFiles) {
        if (!CHAT_IMAGE_TYPES.includes(file.type)) {
          this._showError('Only JPG, PNG, WEBP, and GIF images are supported.');
          continue;
        }
        if (file.size > MAX_CHAT_IMAGE_SIZE_MB * 1024 * 1024) {
          this._showError(`Each image must be ${MAX_CHAT_IMAGE_SIZE_MB}MB or smaller.`);
          continue;
        }
        try {
          const url = await fileToDataUrl(file);
          this.pendingAttachments.push({ type: 'image', url, name: file.name, size: file.size });
        } catch (_error) {
          this._showError(`Failed to read ${file.name}.`);
        }
      }
      this._renderAttachmentPreviews();
      this._syncSendState();
    }

    _renderAttachmentPreviews() {
      if (!this.pendingAttachments.length) {
        this.previewRow.style.display = 'none';
        this.previewRow.innerHTML = '';
        return;
      }
      this.previewRow.style.display = 'flex';
      this.previewRow.innerHTML = this.pendingAttachments.map((attachment, index) => `
        <div class="pulse-chat-attachment-preview">
          <img src="${attachment.url}" alt="${this._escapeHtml(attachment.name || 'attachment')}">
          <button class="pulse-chat-attachment-remove" type="button" data-index="${index}">x</button>
        </div>
      `).join('');
      this.previewRow.querySelectorAll('.pulse-chat-attachment-remove').forEach((button) => {
        button.addEventListener('click', () => {
          const index = Number(button.getAttribute('data-index'));
          this.pendingAttachments.splice(index, 1);
          this._renderAttachmentPreviews();
          this._syncSendState();
        });
      });
    }

    toggle() {
      this.isOpen ? this.close() : this.open();
    }

    open() {
      this.isOpen = true;
      this.window.classList.add('open');
      this.input.focus();
    }

    close() {
      this.isOpen = false;
      this.window.classList.remove('open');
    }

    async sendMessage() {
      const content = this.input.value.trim();
      const attachments = this.pendingAttachments.slice();
      if (!content && !attachments.length) return;

      this.input.value = '';
      this.pendingAttachments = [];
      this._renderAttachmentPreviews();
      this._syncSendState();
      this._showError('');

      this._addMessage({ content, attachments, sender_type: 'customer', created_at: new Date().toISOString() });
      this._showTyping();

      try {
        const clientMessageId = `msg_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
        const headers = {
          'Content-Type': 'application/json',
          'X-Client-Message-Id': clientMessageId,
        };
        if (this.config.widgetKey) {
          headers['X-Pulse-Widget-Key'] = this.config.widgetKey;
        }
        const response = await fetch(`${PULSE_API}/webhooks/web-chat`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            client_message_id: clientMessageId,
            session_id: this.sessionId,
            content,
            attachments,
            customer_name: localStorage.getItem('pulse_chat_name') || 'Website Visitor',
            customer_email: localStorage.getItem('pulse_chat_email') || '',
            company_id: this.config.companyId,
            page_url: window.location.href,
          }),
        });

        const data = await response.json();
        this._hideTyping();

        if (data.conversation_id) this.conversationId = data.conversation_id;

        if (data.ai_message) {
          this._addMessage(data.ai_message);
        } else if (data.response) {
          this._addMessage({
            content: data.response,
            sender_type: data.is_ai ? 'ai' : 'agent',
            sender_name: data.is_ai ? 'AI Assistant' : this.config.agentName,
            created_at: new Date().toISOString(),
          });
        }
      } catch (error) {
        this._hideTyping();
        this._addMessage({
          content: 'Sorry, there was an error sending your message. Please try again.',
          sender_type: 'agent',
          created_at: new Date().toISOString(),
        });
        console.error('Pulse Chat error:', error);
      }
    }

    _addMessage(message) {
      const welcome = this.messagesContainer.querySelector('.pulse-chat-welcome');
      if (welcome) welcome.remove();

      const msg = {
        sender_type: message.sender_type || 'agent',
        sender_name: message.sender_name || (message.sender_type === 'ai' ? 'AI Assistant' : this.config.agentName),
        content: message.content || '',
        created_at: message.created_at || new Date().toISOString(),
        attachments: normalizeAttachments(message.attachments),
      };

      const msgEl = document.createElement('div');
      msgEl.className = `pulse-chat-message ${msg.sender_type}`;
      const time = new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const badge = msg.sender_type === 'ai'
        ? '<div class="pulse-chat-ai-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>AI</div>'
        : '';
      const images = msg.attachments.filter((attachment) => attachment.type === 'image');
      const imageHtml = images.length ? `
        <div class="pulse-chat-images ${images.length > 1 ? 'multi' : ''}">
          ${images.slice(0, 4).map((attachment) => `
            <a href="${attachment.url}" target="_blank" rel="noreferrer">
              <img src="${attachment.url}" alt="${this._escapeHtml(attachment.name || 'attachment')}">
            </a>
          `).join('')}
        </div>
      ` : '';
      const textHtml = msg.content ? `<div style="white-space:pre-line">${this._escapeHtml(msg.content)}</div>` : '';

      msgEl.innerHTML = `
        ${badge}
        <div class="pulse-chat-bubble">
          ${imageHtml}
          ${textHtml}
        </div>
        <span class="pulse-chat-time">${time}</span>
      `;
      this.messagesContainer.appendChild(msgEl);
      this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    _showTyping() {
      this.isTyping = true;
      const indicator = document.createElement('div');
      indicator.className = 'pulse-chat-typing-indicator';
      indicator.innerHTML = '<span></span><span></span><span></span>';
      this.messagesContainer.appendChild(indicator);
      this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    _hideTyping() {
      this.isTyping = false;
      const indicator = this.messagesContainer.querySelector('.pulse-chat-typing-indicator');
      if (indicator) indicator.remove();
    }

    _escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }
  }

  const script = document.currentScript || document.querySelector('script[data-pulse-company]');
  if (script && script.dataset.pulseCompany) {
    window.PulseChat = new PulseChat({
      companyId: script.dataset.pulseCompany,
      widgetKey: script.dataset.pulseWidgetKey || window.PULSE_WIDGET_KEY || '',
    });
  }

  window.PulseChat = window.PulseChat || { init: (config) => new PulseChat(config) };
})();
