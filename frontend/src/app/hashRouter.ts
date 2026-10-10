import { useCallback, useEffect, useRef, useState } from 'react';

export const modelSections = ['connections', 'models', 'profiles', 'playground', 'logs', 'monitor', 'access_keys', 'sensitive_words', 'alert_rules', 'quota'] as const;
export type ModelSection = typeof modelSections[number];
const pages = ['overview', 'conversations', 'agents', 'knowledge', 'tools', 'handoff', 'tickets', 'open-platform', 'evaluation', 'observability', 'access'];
export function normalizeRoute(value: string) {
  const [base, query] = value.split('?');
  const path = normalizePath(base);
  return path.startsWith('/agents') && query ? `${path}?${new URLSearchParams(query)}` : path;
}
function normalizePath(value: string) {
  const path = value.replace(/^#/, '').replace(/\/+$/, '') || '/overview';
  if (path === '/login') return path;
  if (path === '/channels') return '/open-platform/channels';
  if (path === '/open-platform/channels') return path;
  if (pages.some(page => path === `/${page}`)) return path;
  if (/^\/conversations\/[0-9a-zA-Z_-]+$/.test(path)) return path;
  if (/^\/tools\/[0-9a-f-]+\/(apis|versions|calls)$/.test(path)) return path;
  if (/^\/open-platform\/[0-9a-f-]+\/(settings|integrations|embed|webhook|keys|docs|debug|logs)$/.test(path)) return path;
  if (path === '/open-platform/identity') return path;
  if (path === '/models') return '/models/models';
  if (path === '/agents/new') return '/agents/new/config';
  if (path === '/agents/new/conversations') return '/agents/new/config';
  if (/^\/agents\/[^/?#]+\/(config|conversations)$/.test(path)) return path;
  if (modelSections.some(section => path === `/models/${section}`)) return path;
  return '/overview';
}

// Keep an index on our history entries so cancelling Back/Forward restores the
// actual entry instead of adding a new entry or destroying the forward stack.
export function useHashRouter(guard: (destination: string) => boolean) {
  const [path, setPath] = useState(() => normalizeRoute(window.location.hash));
  const current = useRef(path);
  const index = useRef(Number(window.history.state?.agentRouteIndex) || 0);
  const check = useRef(guard); check.current = guard;
  const restoring = useRef(false);
  useEffect(() => {
    window.history.replaceState({ ...window.history.state, agentRouteIndex: index.current }, '', `#${current.current}`);
    function changed() {
      if (restoring.current) {
        if (window.location.hash === `#${current.current}`) restoring.current = false;
        return;
      }
      const next = normalizeRoute(window.location.hash);
      const targetIndex = window.history.state?.agentRouteIndex;
      if (next === current.current) {
        index.current = typeof targetIndex === 'number' ? targetIndex : index.current + 1;
        window.history.replaceState({ ...window.history.state, agentRouteIndex: index.current }, '', `#${next}`);
        return;
      }
      if (!check.current(next)) {
        if (typeof targetIndex === 'number' && targetIndex !== index.current) {
          restoring.current = true;
          window.history.go(index.current - targetIndex);
        } else {
          index.current += 1;
          window.history.replaceState({ ...window.history.state, agentRouteIndex: index.current }, '', `#${current.current}`);
        }
        return;
      }
      index.current = typeof targetIndex === 'number' ? targetIndex : index.current + 1;
      window.history.replaceState({ ...window.history.state, agentRouteIndex: index.current }, '', `#${next}`);
      current.current = next;
      setPath(next);
    }
    window.addEventListener('popstate', changed);
    window.addEventListener('hashchange', changed);
    return () => { window.removeEventListener('popstate', changed); window.removeEventListener('hashchange', changed); };
  }, []);
  const navigate = useCallback((destination: string, replace = false) => {
    const next = normalizeRoute(destination);
    if (restoring.current || next === current.current || (!replace && !check.current(next))) return;
    if (!replace) index.current += 1;
    window.history[replace ? 'replaceState' : 'pushState']({ ...window.history.state, agentRouteIndex: index.current }, '', `#${next}`);
    current.current = next;
    setPath(next);
  }, []);
  return { path, navigate };
}
