/**
 * Browser notifications.
 *
 * Every path degrades: an unsupported browser, a denied permission or a
 * throwing constructor must never break the page. The backend inbox is the
 * source of truth, so a failed browser notification only loses the popup.
 */

import { api } from "./api";
import type { NotificationItem } from "./types";

export function isSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function permission(): NotificationPermission | "unsupported" {
  if (!isSupported()) return "unsupported";
  return Notification.permission;
}

export async function requestPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!isSupported()) return "unsupported";
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

/**
 * Show pending notifications, then tell the backend they were delivered.
 * Only High Attention and Critical events reach this point - the backend
 * applies that threshold, not the client.
 */
export async function deliverPending(
  items: NotificationItem[],
  /** Client-side navigation, supplied by the caller that owns the router. */
  navigate?: (path: string) => void,
): Promise<void> {
  if (!isSupported() || Notification.permission !== "granted") return;

  for (const item of items) {
    try {
      const notification = new Notification(`LUMEN - ${item.title}`, {
        body: item.body.slice(0, 220),
        tag: `lumen-event-${item.event_id ?? item.id}`,
      });
      notification.onclick = () => {
        window.focus();
        navigate?.("/dashboard");
      };
      await api.markNotificationDelivered(item.id);
    } catch {
      // A failed popup is not a failed alert: it stays unread in the inbox.
    }
  }
}
