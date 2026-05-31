import { useEffect, useRef, useCallback } from 'react';
import io from 'socket.io-client';
import { useAuth } from '@/contexts/AuthContext';
import { getAccessToken } from '@/lib/api';

const SOCKET_EVENTS = [
  // Conversation & messaging
  'new_message',
  'conversation_updated',
  'message_updated',
  'message_deleted',
  'message_reaction_updated',
  // Notifications
  'notification',
  // Identity
  'identity_merged',
  'identity_split',
  'identity_resolved',
  // CRM — Lead events (backend now emits these)
  'lead_created',
  'lead_updated',
  'lead_deleted',
  // CRM — Customer events (backend now emits these)
  'customer_created',
  'customer_updated',
  'customer_deleted',
  // Billing — plan confirmation
  'plan_updated',
];

const SOCKET_URL = (
  process.env.REACT_APP_SOCKET_URL
  || `${window.location.protocol}//${window.location.hostname}:8003`
).replace(/\/$/, '');

/**
 * Lightweight socket.io hook for real-time updates.
 * @param {function} onEvent - Called with (eventName, data) for every registered event.
 *   Stable reference: wrap the callback in useCallback before passing.
 * @param {object} [options]
 * @param {string} [options.conversationId] - If provided, emits join_conversation on connect/change.
 * @returns {{ joinConversation: function }} Helpers to imperatively join a room.
 */
export function useSocket(onEvent, { conversationId = '' } = {}) {
  const { user } = useAuth();
  const onEventRef = useRef(onEvent);
  const socketRef = useRef(null);
  const conversationIdRef = useRef(conversationId);
  onEventRef.current = onEvent;
  conversationIdRef.current = conversationId;

  const joinConversation = useCallback((convoId) => {
    if (socketRef.current?.connected && convoId) {
      socketRef.current.emit('join_conversation', { conversation_id: convoId });
    }
  }, []);

  useEffect(() => {
    if (!user?.id) return;

    let socket;
    let connectTimer;

    const initSocket = (token) => {
      if (socket) {
        socket.disconnect();
      }
      socket = io(SOCKET_URL, {
        path: '/socket.io',
        // Prefer WebSocket for lower latency; fall back to polling
        transports: ['websocket', 'polling'],
        auth: token ? { token } : {},
        autoConnect: false,
        reconnectionAttempts: 10,
        reconnectionDelay: 1000,
      });
      socketRef.current = socket;

      socket.on('connect', () => {
        socket.emit('join', { user_id: user.id });
        const convoId = conversationIdRef.current;
        if (convoId) socket.emit('join_conversation', { conversation_id: convoId });
      });

      SOCKET_EVENTS.forEach(evt => {
        socket.on(evt, data => onEventRef.current?.(evt, data));
      });

      // Reconnect on tab becoming visible — catch missed events
      const onVisibilityChange = () => {
        if (document.visibilityState === 'visible' && !socket.connected) {
          socket.connect();
        }
      };
      document.addEventListener('visibilitychange', onVisibilityChange);
      socket._visibilityHandler = onVisibilityChange;

      socket.connect();
    };

    // Defer slightly to allow the access token to be set by AuthContext
    const existingToken = getAccessToken();
    if (existingToken) {
      connectTimer = window.setTimeout(() => initSocket(existingToken), 0);
    } else {
      // Wait for the token to be set via the pe-access-token-updated event
      const onTokenReady = (event) => {
        const token = event?.detail?.token || getAccessToken();
        if (token) {
          window.removeEventListener('pe-access-token-updated', onTokenReady);
          initSocket(token);
        }
      };
      window.addEventListener('pe-access-token-updated', onTokenReady);
    }

    const syncSocketAuth = (event) => {
      const nextToken = event?.detail?.token || getAccessToken();
      if (socketRef.current) {
        socketRef.current.auth = nextToken ? { token: nextToken } : {};
      }
    };
    window.addEventListener('pe-access-token-updated', syncSocketAuth);

    return () => {
      if (connectTimer) window.clearTimeout(connectTimer);
      window.removeEventListener('pe-access-token-updated', syncSocketAuth);
      if (socket) {
        if (socket._visibilityHandler) {
          document.removeEventListener('visibilitychange', socket._visibilityHandler);
        }
        socket.disconnect();
      }
      socketRef.current = null;
    };
  }, [user?.id]); // eslint-disable-line

  // Re-join conversation room when conversationId changes
  useEffect(() => {
    if (conversationId && socketRef.current?.connected) {
      socketRef.current.emit('join_conversation', { conversation_id: conversationId });
    }
  }, [conversationId]);

  return { joinConversation };
}
