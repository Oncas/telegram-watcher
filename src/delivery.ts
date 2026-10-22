import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { ForwardTarget } from "./config.ts";
import type { Match } from "./listeners.ts";
import type { Forwarder, JsonObject, Notifier } from "./model.ts";
import {
  errorName,
  ForwardFailed,
  NotificationFailed,
  timestamp,
} from "./model.ts";
import { Storage } from "./storage.ts";

const execute = promisify(execFile);

export class DesktopNotifier implements Notifier {
  async notify(message: string): Promise<void> {
    try {
      await execute(
        "notify-send",
        ["--app-name=Telegram watcher", "--", "Telegram watcher", message],
        { timeout: 10_000 },
      );
    } catch (error) {
      throw new NotificationFailed(errorName(error));
    }
  }
}

export class HttpForwarder implements Forwarder {
  readonly target: ForwardTarget;
  readonly timeout: number;
  constructor(target: ForwardTarget, timeout = 10_000) {
    this.target = target;
    this.timeout = timeout;
  }
  async forward(payload: JsonObject): Promise<void> {
    try {
      const response = await fetch(this.target.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${this.target.token}`,
        },
        body: JSON.stringify(payload),
        redirect: "manual",
        signal: AbortSignal.timeout(this.timeout),
      });
      await response.body?.cancel();
      if (!response.ok) throw new ForwardFailed(`HTTP ${response.status}`);
    } catch (error) {
      if (error instanceof ForwardFailed) throw error;
      throw new ForwardFailed(errorName(error));
    }
  }
}

export class Alerts {
  readonly storage: Storage;
  readonly notifier: Notifier;
  constructor(storage: Storage, notifier: Notifier) {
    this.storage = storage;
    this.notifier = notifier;
  }
  async raise(kind: string, subject: string, message: string): Promise<void> {
    if (this.storage.alertActive(kind, subject)) return;
    try {
      await this.notifier.notify(message);
    } catch (error) {
      if (!(error instanceof NotificationFailed)) throw error;
      console.warn(`notification not delivered, retrying next run: ${kind}`);
      return;
    }
    this.storage.transaction(() => this.storage.activateAlert(kind, subject));
  }
  resolve(kind: string, subject?: string): void {
    this.storage.transaction(() => this.storage.resolveAlert(kind, subject));
  }
}

export function forwardPayload(match: Match): JsonObject {
  return {
    id: match.match_id,
    listener: match.listener,
    message_id: match.message_id,
    posted_at: timestamp(match.posted_at),
    edited_at: timestamp(match.edited_at),
    data: match.data,
  };
}

export async function deliverMatches(
  storage: Storage,
  notifier: Notifier,
  forwarder: Forwarder | null,
  alerts: Alerts,
  clock: () => Date,
): Promise<void> {
  if (forwarder) {
    for (const match of storage.pendingMatches("forwarded_at")) {
      try {
        await forwarder.forward(forwardPayload(match));
      } catch (error) {
        if (!(error instanceof ForwardFailed)) throw error;
        console.warn(
          `match not forwarded, retrying next run: ${error.message}`,
        );
        await alerts.raise(
          "forward_failed",
          error.message,
          `Telegram watcher could not forward listener matches: ${error.message}.`,
        );
        break;
      }
      storage.transaction(() =>
        storage.markDelivered(match, "forwarded_at", clock()),
      );
      alerts.resolve("forward_failed");
    }
  }
  for (const match of storage.pendingMatches("notified_at")) {
    try {
      await notifier.notify(
        [
          match.listener,
          ...Object.entries(match.data).map(
            ([key, value]) =>
              `${key}: ${typeof value === "object" && value !== null ? JSON.stringify(value) : value}`,
          ),
        ].join("\n"),
      );
    } catch (error) {
      if (!(error instanceof NotificationFailed)) throw error;
      console.warn(
        `match notification not delivered, retrying next run: ${match.listener}`,
      );
      break;
    }
    storage.transaction(() =>
      storage.markDelivered(match, "notified_at", clock()),
    );
  }
}
