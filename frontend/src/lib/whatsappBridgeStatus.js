export const WHATSAPP_BRIDGE_PROGRESS = {
  idle: 0,
  qr_required: 20,
  need_qr: 20,
  qr_scanned: 45,
  authenticated: 65,
  initializing: 80,
  ready: 100,
  reconnecting: 55,
  failed: 0,
  disconnected: 0,
};

export const WHATSAPP_BRIDGE_MESSAGES = {
  idle: 'Starting WhatsApp session',
  qr_required: 'Waiting for QR scan',
  need_qr: 'Waiting for QR scan',
  qr_scanned: 'QR scanned successfully',
  authenticated: 'Authenticating WhatsApp session',
  initializing: 'Finalizing WhatsApp connection',
  ready: 'Connected successfully',
  reconnecting: 'Reconnecting',
  failed: 'Failed to connect, please scan again',
  disconnected: 'Disconnected',
};

export function normalizeWhatsAppBridgeState(value) {
  const state = String(value || '').trim().toLowerCase();
  if (state === 'need_qr') return 'qr_required';
  if (state === 'connected') return 'ready';
  return state || 'idle';
}

export function getWhatsAppBridgeProgress(value, fallbackProgress) {
  const numeric = Number(fallbackProgress);
  if (Number.isFinite(numeric) && numeric >= 0) {
    return Math.max(0, Math.min(100, Math.round(numeric)));
  }
  const state = normalizeWhatsAppBridgeState(value);
  return WHATSAPP_BRIDGE_PROGRESS[state] ?? 0;
}

export function getWhatsAppBridgeMessage(value, options = {}) {
  const state = normalizeWhatsAppBridgeState(value);
  if ((state === 'failed' || state === 'reconnecting') && options.retrying) {
    return 'Failed to connect, retrying';
  }
  return options.message || WHATSAPP_BRIDGE_MESSAGES[state] || WHATSAPP_BRIDGE_MESSAGES.idle;
}

export function isWhatsAppBridgeConnecting(value) {
  const state = normalizeWhatsAppBridgeState(value);
  return ['qr_scanned', 'authenticated', 'initializing', 'reconnecting'].includes(state);
}
