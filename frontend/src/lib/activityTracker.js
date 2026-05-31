/**
 * Activity tracker for 6-hour inactivity session expiry.
 * Tracks user interactions (click, keydown, mousemove) and updates
 * sessionStorage so the check works across page navigations but NOT
 * across browser restarts (sessionStorage is cleared on browser close).
 *
 * Q4 design: timer only fires when the user has been IDLE (no interaction)
 * for 6 hours. Active users are never signed out by this module.
 */

const LAST_ACTIVITY_KEY = 'pe_last_activity';
const INACTIVITY_MS = 6 * 60 * 60 * 1000; // 6 hours

let _onExpire = null;
let _debounceTimer = null;
let _checkTimer = null;

function _updateActivity() {
  sessionStorage.setItem(LAST_ACTIVITY_KEY, String(Date.now()));
}

function _debounced() {
  if (_debounceTimer) return;
  _debounceTimer = setTimeout(() => {
    _debounceTimer = null;
    _updateActivity();
  }, 1000); // Debounce 1s — update at most once per second
}

/** Returns the last recorded activity timestamp (ms), or null if never set. */
export function getLastActivity() {
  const raw = sessionStorage.getItem(LAST_ACTIVITY_KEY);
  if (!raw) return null;
  const ts = parseInt(raw, 10);
  return isNaN(ts) ? null : ts;
}

/** Manually reset the last activity timestamp to now (extends the session). */
export function resetActivity() {
  _updateActivity();
}

/**
 * Check if the user has been inactive for more than 6 hours.
 * Returns true if expired.
 */
export function isSessionExpiredByInactivity() {
  const raw = sessionStorage.getItem(LAST_ACTIVITY_KEY);
  if (!raw) return false; // No record means brand-new session — not expired
  const last = parseInt(raw, 10);
  if (isNaN(last)) return false;
  return Date.now() - last > INACTIVITY_MS;
}

/**
 * Start tracking user activity. Call once after successful login.
 * @param {function} onExpire - Called when 6 hours of inactivity is detected.
 */
export function startActivityTracking(onExpire) {
  _onExpire = onExpire;
  _updateActivity(); // Record activity immediately on start

  // Track real user interactions — mousemove included so idle detection is accurate
  const events = ['click', 'keydown', 'mousemove', 'touchstart'];
  events.forEach(evt => window.addEventListener(evt, _debounced, { passive: true }));

  // Check every 5 minutes whether the user has been idle for 6 hours
  _checkTimer = setInterval(() => {
    if (isSessionExpiredByInactivity() && typeof _onExpire === 'function') {
      _onExpire();
    }
  }, 5 * 60 * 1000);
}

/**
 * Stop tracking. Call on logout.
 */
export function stopActivityTracking() {
  const events = ['click', 'keydown', 'mousemove', 'touchstart'];
  events.forEach(evt => window.removeEventListener(evt, _debounced));
  if (_checkTimer) clearInterval(_checkTimer);
  if (_debounceTimer) clearTimeout(_debounceTimer);
  _checkTimer = null;
  _debounceTimer = null;
  _onExpire = null;
  sessionStorage.removeItem(LAST_ACTIVITY_KEY);
}
