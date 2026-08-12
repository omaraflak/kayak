import { NavigationTab } from '../types';

export interface RouteState {
  tab: NavigationTab;
  conversationId?: string | null;
  itemId?: string | null;
}

/** Tabs that follow the /<tab>/<optional-id> URL pattern. */
const ITEM_TABS = new Set<NavigationTab>(['agents', 'skills', 'tools']);

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

  // Simple tabs without sub-IDs: /tasks, /settings, /models
  const SIMPLE_TABS = new Set<NavigationTab>(['tasks', 'settings', 'models']);
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
      targetPath = id ? `/${tab}/${encodeURIComponent(id)}` : `/${tab}`;
      break;
    case 'tasks':
    case 'settings':
    case 'models':
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
