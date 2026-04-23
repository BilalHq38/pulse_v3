import { BACKEND_BASE_URL } from '@/lib/backend-url';

/** Single-character fallback for avatars when no image URL (manual login / missing photo). */
export function displayNameInitial(name, fallback = 'U') {
  const s = String(name ?? '').trim();
  if (!s) return fallback;
  return s.charAt(0).toLocaleUpperCase();
}

export function normalizeAvatarUrl(rawValue) {
  if (!rawValue) return '';

  let value = String(rawValue).trim();
  if (!value) return '';

  if (/%[0-9a-f]{2}/i.test(value)) {
    try {
      value = decodeURIComponent(value);
    } catch {
      // Keep the original value if decoding fails.
    }
  }

  if (value.startsWith('data:image/')) return value;
  if (value.startsWith('blob:')) return value;
  if (value.startsWith('//')) return `${window.location.protocol}${value}`;
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith('/')) return `${BACKEND_BASE_URL}${value}`;

  return value;
}
