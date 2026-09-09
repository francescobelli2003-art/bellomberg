import { externalWebUrl } from '../../electron/security';
/**
 * BELLOMBERG - News Alert Hook
 *
 * Polla /news/alerts/unnotified ogni 60s.
 * Per ogni news con relevance >= 8, mostra una Notification desktop
 * (Web Notification API che Electron supporta nativamente).
 * Dopo la notifica, marca le news come notified=1 nel backend
 * cosi' non vengono mostrate di nuovo.
 *
 * Configurazione:
 * - localStorage 'bellomberg_alerts_enabled' (default 'true')
 * - localStorage 'bellomberg_alerts_min_relevance' (default '8')
 *
 * USO (in App.tsx o Layout.tsx):
 *   useNewsAlerts();
 */
import { useEffect, useRef } from 'react';
import { Bellomberg } from '@/lib/api';

const POLL_INTERVAL_MS = 60_000; // 1 minuto
const ENABLED_KEY = 'bellomberg_alerts_enabled';
const MIN_REL_KEY = 'bellomberg_alerts_min_relevance';
const SHOWN_IDS_KEY = 'bellomberg_alerts_shown_ids_v1';

function getEnabled(): boolean {
  const v = localStorage.getItem(ENABLED_KEY);
  return v === null ? true : v === 'true';
}

function getMinRelevance(): number {
  const v = localStorage.getItem(MIN_REL_KEY);
  const n = v ? parseInt(v, 10) : 8;
  return Number.isFinite(n) ? n : 8;
}

function getShownIds(): Set<number> {
  try {
    const v = localStorage.getItem(SHOWN_IDS_KEY);
    if (!v) return new Set();
    return new Set(JSON.parse(v));
  } catch {
    return new Set();
  }
}

function addShownIds(ids: number[]) {
  const cur = getShownIds();
  ids.forEach(id => cur.add(id));
  // Keep last 500 only (rolling)
  const arr = Array.from(cur);
  const capped = arr.slice(-500);
  localStorage.setItem(SHOWN_IDS_KEY, JSON.stringify(capped));
}

export function selectNotificationBatch<T extends { id: number }>(
  items: T[], shownIds: Set<number>, limit = 3,
): T[] {
  return items.filter(item => !shownIds.has(item.id)).slice(0, limit);
}

async function requestPermission(): Promise<boolean> {
  if (!('Notification' in window)) return false;
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  try {
    const p = await Notification.requestPermission();
    return p === 'granted';
  } catch {
    return false;
  }
}

function showNotification(item: {
  id: number; title: string; ticker: string | null;
  sentiment: string | null; provider: string | null;
  url: string | null; relevance: number | null;
}) {
  if (!('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  const sentimentEmoji = item.sentiment === 'bullish' ? 'BULL'
                       : item.sentiment === 'bearish' ? 'BEAR'
                       : 'NEUTRAL';
  const tickerTag = item.ticker ? `[${item.ticker}] ` : '';
  const relTag = item.relevance ? `(rel ${item.relevance}) ` : '';
  const body = `${relTag}${sentimentEmoji} · ${item.provider || 'news'}`;
  try {
    const n = new Notification(`BELLOMBERG · ${tickerTag}${item.title.slice(0, 90)}`, {
      body,
      icon: '/icon.png',
      tag: `bellomberg-news-${item.id}`,
      requireInteraction: false,
      silent: false,
    });
    n.onclick = () => {
      const safe = externalWebUrl(item.url || '');
      if (safe) window.open(safe, '_blank');
      n.close();
    };
    // Auto-close after 8s
    setTimeout(() => { try { n.close(); } catch {} }, 8000);
  } catch (e) {
    console.warn('[alerts] Notification failed:', e);
  }
}

export function useNewsAlerts() {
  const stopped = useRef(false);
  useEffect(() => {
    stopped.current = false;
    let timer: any = null;

    // Request permission upfront (once)
    if (getEnabled()) {
      requestPermission().catch(() => {});
    }

    const poll = async () => {
      if (stopped.current) return;
      if (!getEnabled()) return;
      try {
        const minRel = getMinRelevance();
        const res = await Bellomberg.newsAlertsUnnotified(minRel, 10);
        if (!res?.items?.length) return;
        const shownIds = getShownIds();
        const toShow = selectNotificationBatch(res.items, shownIds);
        if (toShow.length === 0) {
          // mark as notified backend-side anyway (cleanup)
          await Bellomberg.newsAlertsMarkNotified(res.items.map(i => i.id)).catch(() => {});
          return;
        }
        // Show up to 3 notifications per cycle (anti-spam). Gli altri restano
        // non notificati sul backend e verranno ripresi dal ciclo successivo.
        for (const it of toShow) {
          showNotification(it);
        }
        addShownIds(toShow.map(i => i.id));
        await Bellomberg.newsAlertsMarkNotified(toShow.map(i => i.id)).catch(() => {});
      } catch (e) {
        // silent - polling errors shouldn't spam console
      }
    };

    // First poll after 5s (give app time to settle)
    timer = setTimeout(() => {
      poll();
      timer = setInterval(poll, POLL_INTERVAL_MS);
    }, 5000);

    return () => {
      stopped.current = true;
      if (timer) {
        clearTimeout(timer);
        clearInterval(timer);
      }
    };
  }, []);
}

// Settings helpers (esposti per Settings page)
export const NewsAlertsSettings = {
  isEnabled: getEnabled,
  setEnabled(v: boolean) { localStorage.setItem(ENABLED_KEY, String(v)); },
  getMinRelevance,
  setMinRelevance(n: number) { localStorage.setItem(MIN_REL_KEY, String(n)); },
  permissionState(): NotificationPermission | 'unsupported' {
    if (!('Notification' in window)) return 'unsupported';
    return Notification.permission;
  },
  requestPermission,
};
