import { NavigationTab } from '../types';

export interface RouteState {
  tab: NavigationTab;
  conversationId?: string | null;
  itemId?: string | null;
}

/** Tabs that follow the /<tab>/<optional-id> URL pattern.
 *
 * Audio's "id" is which half of it you are on -- synthesize or transcribe -- so
 * that each is a place you can link to and reload into, rather than two views
 * hiding behind one address.
 */
const ITEM_TABS = new Set<NavigationTab>(['agents', 'skills', 'tools', 'audio']);

/**
 * Parses the current window.location.pathname into structured RouteState.
 */
export function parseCurrentUrl(): RouteState {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  const parts = path.split('/').filter(Boolean);

  // Root or explicit /chat
  if (parts.length === 0 || parts[0] === 'chat') {
    return { tab: 'chat', conversationId: parts[1] || null };
  }

  // Shorthand conversation URL: /c/<id>
  if (parts[0] === 'c') {
    return { tab: 'chat', conversationId: parts[1] || null };
  }

  // Tabs with optional item IDs: /agents, /skills, /tools
  const firstPart = parts[0] as NavigationTab;
  if (ITEM_TABS.has(firstPart)) {
    return { tab: firstPart, itemId: parts[1] || null };
  }

  // Simple tabs without sub-IDs: /settings, /models, /memories, /audio
  const SIMPLE_TABS = new Set<NavigationTab>(['settings', 'models', 'memories']);
  if (SIMPLE_TABS.has(firstPart)) {
    return { tab: firstPart };
  }

  return { tab: 'chat', conversationId: null };
}

/**
 * Updates browser URL and pushes or replaces browser history state.
 */
export function navigateTo(tab: NavigationTab, id?: string | null, replace = false): void {
  let targetPath = '/';

  switch (tab) {
    case 'chat':
      targetPath = id ? `/c/${id}` : '/chat';
      break;
    case 'agents':
    case 'skills':
    case 'tools':
    case 'audio':
      targetPath = id ? `/${tab}/${encodeURIComponent(id)}` : `/${tab}`;
      break;
    case 'settings':
    case 'models':
    case 'memories':
      targetPath = `/${tab}`;
      break;
    default:
      targetPath = '/';
  }

  if (window.location.pathname !== targetPath) {
    if (replace) {
      window.history.replaceState({ tab, id }, '', targetPath);
    } else {
      window.history.pushState({ tab, id }, '', targetPath);
    }
  }
}
