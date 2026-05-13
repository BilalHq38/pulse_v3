/**
 * channelUtils.js — Shared channel/contact-method utilities.
 *
 * Extracted from LeadsPage.js and CustomersPage.js to eliminate duplication.
 * Import from here whenever you need channel normalization or contact-method
 * building logic.
 */

import { Facebook, Globe, Instagram, Mail, MessageSquare } from 'lucide-react';

export const CHANNEL_META = {
  whatsapp: { label: 'WhatsApp', icon: MessageSquare, color: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  instagram: { label: 'Instagram', icon: Instagram, color: 'text-pink-600', bg: 'bg-pink-50', border: 'border-pink-200' },
  facebook: { label: 'Facebook', icon: Facebook, color: 'text-blue-600', bg: 'bg-blue-50', border: 'border-blue-200' },
  email: { label: 'Email', icon: Mail, color: 'text-sky-600', bg: 'bg-sky-50', border: 'border-sky-200' },
  web_chat: { label: 'Website', icon: Globe, color: 'text-violet-600', bg: 'bg-violet-50', border: 'border-violet-200' },
};

/**
 * Normalizes a raw channel key string to a lowercase canonical form.
 * Maps legacy aliases (e.g. "website", "web") to "web_chat".
 */
export function normalizeChannelKey(raw) {
  const key = String(raw || '').trim().toLowerCase();
  if (key === 'website' || key === 'web') return 'web_chat';
  return key;
}

/**
 * Deduplicates a list of contact methods by channel type.
 * Preserves the isPrimaryContact flag if any duplicate has it.
 */
export function dedupeMethods(methods) {
  const merged = new Map();
  methods.forEach((m) => {
    const key = m.channel;
    if (!merged.has(key)) {
      merged.set(key, m);
      return;
    }
    const existing = merged.get(key);
    merged.set(key, {
      ...existing,
      profileUrl: existing.profileUrl || m.profileUrl || '',
      source: existing.source || m.source || '',
      isPrimaryContact: Boolean(existing.isPrimaryContact || m.isPrimaryContact),
    });
  });
  return Array.from(merged.values());
}

/**
 * Builds a sorted, deduplicated list of contact methods for a lead record.
 */
export function buildLeadMethods(lead) {
  const methods = [];
  const primaryChannel = normalizeChannelKey(lead?.source);
  const addMethod = (channel, profileUrl = '', source = 'lead_profile') => {
    const normalized = normalizeChannelKey(channel);
    if (!CHANNEL_META[normalized]) return;
    methods.push({ channel: normalized, profileUrl: profileUrl || '', source, isPrimaryContact: normalized === primaryChannel });
  };

  addMethod(lead?.source, lead?.profile_url || '', `lead_${lead?.source || 'profile'}`);

  if (Array.isArray(lead?.channels)) {
    lead.channels.forEach((entry) => {
      if (typeof entry === 'string') addMethod(entry);
      if (entry && typeof entry === 'object') {
        addMethod(entry.channel || entry.platform || entry.type, entry.url || entry.profile_url || entry.profileUrl || '', 'lead_channel');
      }
    });
  }

  const social = lead?.social_profiles || lead?.socialProfiles;
  if (social && typeof social === 'object') {
    Object.entries(social).forEach(([key, value]) => {
      if (key.toLowerCase() === 'email') return;
      if (typeof value === 'string' && value.trim()) addMethod(key, value.trim(), 'lead_social_profile');
    });
  }

  addMethod('instagram', lead?.instagram_profile_url || '', 'lead_instagram');
  addMethod('facebook', lead?.facebook_profile_url || '', 'lead_facebook');
  addMethod('whatsapp', lead?.whatsapp_profile_url || '', 'lead_whatsapp');
  if (lead?.email) addMethod('email', lead.email, 'lead_email');

  const cleaned = dedupeMethods(methods);
  return cleaned.sort((a, b) => {
    if (a.channel === 'whatsapp' && b.channel !== 'whatsapp') return -1;
    if (b.channel === 'whatsapp' && a.channel !== 'whatsapp') return 1;
    if (a.isPrimaryContact && !b.isPrimaryContact) return -1;
    if (!a.isPrimaryContact && b.isPrimaryContact) return 1;
    return 0;
  });
}

/**
 * Infers the primary channel for a customer record.
 */
export function inferPrimaryCustomerChannel(customer) {
  const fromSource = normalizeChannelKey(customer?.source || customer?.lead_source || customer?.primary_channel || '');
  if (CHANNEL_META[fromSource]) return fromSource;
  if (Array.isArray(customer?.channels)) {
    for (const entry of customer.channels) {
      const raw = typeof entry === 'string' ? entry : (entry?.channel || entry?.platform || entry?.type);
      const normalized = normalizeChannelKey(raw);
      if (CHANNEL_META[normalized]) return normalized;
    }
  }
  return '';
}

/**
 * Builds a sorted, deduplicated list of contact methods for a customer record.
 */
export function buildCustomerMethods(customer) {
  const methods = [];
  const primaryChannel = inferPrimaryCustomerChannel(customer);
  const addMethod = (channel, profileUrl = '', source = 'customer_profile') => {
    const normalized = normalizeChannelKey(channel);
    if (normalized === 'email') return;
    if (!CHANNEL_META[normalized]) return;
    methods.push({ channel: normalized, profileUrl: profileUrl || '', source, isPrimaryContact: normalized === primaryChannel });
  };

  if (Array.isArray(customer?.channels)) {
    customer.channels.forEach((entry) => {
      if (typeof entry === 'string') addMethod(entry, '', 'customer_channel');
      if (entry && typeof entry === 'object') {
        addMethod(entry.channel || entry.platform || entry.type, entry.url || entry.profile_url || entry.profileUrl || '', 'customer_channel');
      }
    });
  }

  const social = customer?.social_profiles || customer?.socialProfiles;
  if (social && typeof social === 'object') {
    Object.entries(social).forEach(([key, value]) => {
      if (key.toLowerCase() === 'email') return;
      if (typeof value === 'string' && value.trim()) addMethod(key, value.trim(), 'customer_social_profile');
    });
  }

  if (customer?.instagram_profile_url) addMethod('instagram', customer.instagram_profile_url, 'customer_instagram');
  if (customer?.facebook_profile_url) addMethod('facebook', customer.facebook_profile_url, 'customer_facebook');
  if (customer?.whatsapp_profile_url || customer?.phone) addMethod('whatsapp', customer?.whatsapp_profile_url || '', 'customer_whatsapp');

  return dedupeMethods(methods).sort((a, b) => {
    if (a.isPrimaryContact && !b.isPrimaryContact) return -1;
    if (!a.isPrimaryContact && b.isPrimaryContact) return 1;
    if (a.channel === 'whatsapp' && b.channel !== 'whatsapp') return -1;
    if (b.channel === 'whatsapp' && a.channel !== 'whatsapp') return 1;
    return 0;
  });
}
