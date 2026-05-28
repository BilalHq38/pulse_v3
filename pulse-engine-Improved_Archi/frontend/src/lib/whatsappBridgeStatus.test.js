import {
  getWhatsAppBridgeMessage,
  getWhatsAppBridgeProgress,
  isWhatsAppBridgeConnecting,
  normalizeWhatsAppBridgeState,
} from './whatsappBridgeStatus';

test('QR generated state maps to waiting copy and progress 20', () => {
  expect(normalizeWhatsAppBridgeState('need_qr')).toBe('qr_required');
  expect(getWhatsAppBridgeProgress('qr_required')).toBe(20);
  expect(getWhatsAppBridgeMessage('qr_required')).toBe('Waiting for QR scan');
});

test('after QR scan loader states keep progressing until ready', () => {
  expect(getWhatsAppBridgeProgress('qr_scanned')).toBe(45);
  expect(getWhatsAppBridgeProgress('authenticated')).toBe(65);
  expect(getWhatsAppBridgeProgress('initializing')).toBe(80);
  expect(isWhatsAppBridgeConnecting('authenticated')).toBe(true);
});

test('ready state reaches connected progress', () => {
  expect(getWhatsAppBridgeProgress('ready')).toBe(100);
  expect(getWhatsAppBridgeMessage('ready')).toBe('Connected successfully');
});

test('retrying failure shows retrying copy instead of final failure', () => {
  expect(getWhatsAppBridgeMessage('reconnecting', { retrying: true })).toBe('Failed to connect, retrying');
  expect(getWhatsAppBridgeMessage('failed', { retrying: false })).toBe('Failed to connect, please scan again');
});
