import { useEffect, useRef, useCallback } from 'react';
import io from 'socket.io-client';
import { useAuth } from '@/contexts/AuthContext';
import { getAccessToken } from '@/lib/api';

const SOCKET_EVENTS = [
  'new_message',
  'conversation_updated',
  'message_updated',
  'message_deleted',
  'notification',
  'identity_merged',
  'identity_split',
  'identity_resolved',
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

    const token = getAccessToken();
    const socket = io(SOCKET_URL, {
      path: '/socket.io',
      transports: ['polling', 'websocket'],
      auth: token ? { token } : {},
      autoConnect: false,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
    });
    socketRef.current = socket;

    const syncSocketAuth = (event) => {
      const nextToken = event?.detail?.token || getAccessToken();
      socket.auth = nextToken ? { token: nextToken } : {};
    };
    const connectTimer = window.setTimeout(() => {
      socket.connect();
    }, 0);

    socket.on('connect', () => {
      socket.emit('join', { user_id: user.id });
      const convoId = conversationIdRef.current;
      if (convoId) socket.emit('join_conversation', { conversation_id: convoId });
    });

    window.addEventListener('pe-access-token-updated', syncSocketAuth);

    SOCKET_EVENTS.forEach(evt => {
      socket.on(evt, data => onEventRef.current?.(evt, data));
    });

    return () => {
      window.clearTimeout(connectTimer);
      window.removeEventListener('pe-access-token-updated', syncSocketAuth);
      socket.disconnect();
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
