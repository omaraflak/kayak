import { NavigationTab } from '../types';

export interface RouteState {
  tab: NavigationTab;
  conversationId?: string | null;
  itemId?: string | null;
}

/**
 * Parses the current window.location.pathname into structured RouteState.
 */
export function parseCurrentUrl(): RouteState {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  const parts = path.split('/').filter(Boolean);

  if (parts.length === 0 || parts[0] === 'chat') {
    return {
      tab: 'chat',
      conversationId: parts[1] || null,
    };
  }

  if (parts[0] === 'c') {
    return {
      tab: 'chat',
      conversationId: parts[1] || null,
    };
  }

  if (parts[0] === 'agents') {
    return {
      tab: 'agents',
      itemId: parts[1] || null,
    };
  }

  if (parts[0] === 'skills') {
    return {
      tab: 'skills',
      itemId: parts[1] || null,
    };
  }

  if (parts[0] === 'tools') {
    return {
      tab: 'tools',
      itemId: parts[1] || null,
    };
  }

  if (parts[0] === 'tasks') {
    return {
      tab: 'tasks',
    };
  }

  if (parts[0] === 'settings') {
    return {
      tab: 'settings',
    };
  }

  return {
    tab: 'chat',
    conversationId: null,
  };
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
      targetPath = id ? `/agents/${encodeURIComponent(id)}` : '/agents';
      break;
    case 'skills':
      targetPath = id ? `/skills/${encodeURIComponent(id)}` : '/skills';
      break;
    case 'tools':
      targetPath = id ? `/tools/${encodeURIComponent(id)}` : '/tools';
      break;
    case 'tasks':
      targetPath = '/tasks';
      break;
    case 'settings':
      targetPath = '/settings';
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
