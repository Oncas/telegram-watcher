import { Alerts, deliverMatches } from "./delivery.ts";
import { ImageStore } from "./image-store.ts";
import { hear } from "./listeners.ts";
import type { Listener } from "./listeners.ts";
import type {
  ChatClient,
  Forwarder,
  HaltReason,
  LiveTelegram,
  Notifier,
  Post,
  WatchedChat,
} from "./model.ts";
import {
  AuthorizationFailed,
  ChatUnavailable,
  errorName,
  NetworkUnavailable,
  RateLimited,
  rejectOtherChats,
  timestamp,
} from "./model.ts";
import { Storage } from "./storage.ts";

export type RunOutcome =
  "completed" | "halted" | "rate_limited" | "network_unavailable" | "failed";
export interface RunReport {
  started_at: Date;
  finished_at: Date;
  outcome: RunOutcome;
  error_type: string | null;
  halt_reason: HaltReason | null;
  polled_chats: WatchedChat[];
  unavailable_chats: WatchedChat[];
}

export class PostRecorder {
  readonly storage: Storage;
  readonly listeners: Listener[];
  constructor(storage: Storage, listeners: Listener[]) {
    this.storage = storage;
    this.listeners = listeners;
  }
  save(post: Post, fetchedAt: Date): void {
    if (this.storage.savePost(post, fetchedAt)) {
      for (const match of hear(this.listeners, post))
        this.storage.addMatch(match, fetchedAt);
    }
  }
  recordNew(
    chatId: number,
    posts: Post[],
    fetchedAt: Date,
    readThrough: number | null,
  ): void {
    rejectOtherChats(chatId, posts);
    this.storage.transaction(() => {
      for (const post of posts) this.save(post, fetchedAt);
      const ids = posts.map((post) => post.message_id);
      if (readThrough !== null) ids.push(readThrough);
      if (ids.length) this.storage.advance(chatId, Math.max(...ids));
    });
  }
  recordStartingPoint(chatId: number, messageId: number): void {
    this.storage.transaction(() => this.storage.advance(chatId, messageId));
  }
  recordRecheck(
    chatId: number,
    ids: number[],
    posts: Post[],
    fetchedAt: Date,
  ): void {
    rejectOtherChats(chatId, posts);
    const returned = new Set(posts.map((post) => post.message_id));
    this.storage.transaction(() => {
      for (const post of posts) this.save(post, fetchedAt);
      this.storage.markDeleted(
        chatId,
        ids.filter((id) => !returned.has(id)),
        fetchedAt,
      );
    });
  }
}

export class ChatPoller {
  readonly recorder: PostRecorder;
  readonly images: ImageStore;
  readonly clock: () => Date;
  readonly pageSize: number;
  readonly maxPages: number;
  readonly recheckWindow: number;
  constructor(
    recorder: PostRecorder,
    images: ImageStore,
    clock: () => Date,
    { pageSize = 100, maxPages = 20, recheckWindow = 3 * 86_400_000 } = {},
  ) {
    this.recorder = recorder;
    this.images = images;
    this.clock = clock;
    this.pageSize = pageSize;
    this.maxPages = maxPages;
    this.recheckWindow = recheckWindow;
  }
  async poll(client: ChatClient, chat: WatchedChat): Promise<void> {
    let after = this.recorder.storage.checkpoint(chat.chat_id);
    if (after === null) {
      this.recorder.recordStartingPoint(
        chat.chat_id,
        (await client.latestMessageId(chat)) ?? 0,
      );
      return;
    }
    const recent = this.recorder.storage.recentIds(
      chat.chat_id,
      new Date(this.clock().getTime() - this.recheckWindow),
    );
    if (recent.length) {
      const posts = await client.fetchByIds(chat, recent);
      this.recorder.recordRecheck(chat.chat_id, recent, posts, this.clock());
    }
    for (let pageNumber = 0; pageNumber < this.maxPages; pageNumber++) {
      const page = await client.fetchAfter(chat, after, this.pageSize);
      rejectOtherChats(chat.chat_id, page.posts);
      if (chat.download_photos) {
        for (const post of page.posts) {
          if (
            post.outgoing ||
            !["photo", "document"].includes(post.media_kind ?? "") ||
            this.images.contains(chat.chat_id, post.message_id) ||
            (chat.download_photos === "matched" &&
              hear(this.recorder.listeners, post).length === 0)
          )
            continue;
          const image = await client.fetchImage(chat, post.message_id);
          if (image) this.images.add(chat.chat_id, post.message_id, image);
        }
      }
      if (page.last_message_id !== null) {
        this.recorder.recordNew(
          chat.chat_id,
          page.posts,
          this.clock(),
          page.last_message_id,
        );
        after = page.last_message_id;
      }
      if (page.reached_end) return;
    }
  }
}

export interface WatcherOptions {
  chats: WatchedChat[];
  connect: () => Promise<ChatClient>;
  disconnect?: () => Promise<void>;
  listeners: Listener[];
  notifier: Notifier;
  forwarder: Forwarder | null;
  imageStore: ImageStore;
  clock: () => Date;
  signal?: AbortSignal;
}

async function pollRun(
  storage: Storage,
  options: WatcherOptions,
): Promise<RunReport> {
  const report: RunReport = {
    started_at: options.clock(),
    finished_at: options.clock(),
    outcome: "completed",
    error_type: null,
    halt_reason: storage.haltReason(),
    polled_chats: [],
    unavailable_chats: [],
  };
  const retryAt = storage.retryAt();
  if (report.halt_reason) report.outcome = "halted";
  else if (retryAt && options.clock() < retryAt)
    report.outcome = "rate_limited";
  else {
    try {
      try {
        const client = await options.connect();
        const poller = new ChatPoller(
          new PostRecorder(storage, options.listeners),
          options.imageStore,
          options.clock,
        );
        for (const chat of options.chats) {
          try {
            await poller.poll(client, chat);
            report.polled_chats.push(chat);
          } catch (error) {
            if (!(error instanceof ChatUnavailable)) throw error;
            console.warn(`chat unavailable: ${chat.name}`);
            report.unavailable_chats.push(chat);
          }
        }
      } finally {
        await options.disconnect?.();
      }
    } catch (error) {
      if (options.signal?.aborted) throw error;
      if (error instanceof NetworkUnavailable)
        report.outcome = "network_unavailable";
      else if (error instanceof RateLimited) {
        storage.transaction(() =>
          storage.deferUntil(new Date(options.clock().getTime() + error.wait)),
        );
        report.outcome = "rate_limited";
      } else if (error instanceof AuthorizationFailed) {
        storage.transaction(() => storage.halt(error.reason));
        report.outcome = "halted";
      } else {
        report.outcome = "failed";
        report.error_type = errorName(error);
        console.error(`run failed: ${report.error_type}`);
      }
    }
  }
  report.finished_at = options.clock();
  report.halt_reason = storage.haltReason();
  return report;
}

const haltNotices: Record<HaltReason, string> = {
  session_ended:
    "Telegram watcher halted: the session ended. Log in again, then clear the halt.",
  account_deactivated:
    "Telegram watcher halted: the account is deactivated or banned. Logging in again will not help.",
  authorization_refused:
    "Telegram watcher halted: authorization was refused. Review the reason in the log before clearing the halt.",
};

async function recordRun(
  storage: Storage,
  chats: WatchedChat[],
  report: RunReport,
  alerts: Alerts,
): Promise<void> {
  storage.transaction(() => {
    storage.run(
      "INSERT INTO runs (started_at, finished_at, outcome, error_type) VALUES (?, ?, ?, ?)",
      timestamp(report.started_at),
      timestamp(report.finished_at),
      report.outcome,
      report.error_type,
    );
    for (const chat of chats)
      storage.run(
        "INSERT INTO chat_polls (chat_id, watched_since) VALUES (?, ?) ON CONFLICT (chat_id) DO NOTHING",
        chat.chat_id,
        timestamp(report.started_at),
      );
    for (const chat of report.polled_chats)
      storage.run(
        "UPDATE chat_polls SET last_success_at = ? WHERE chat_id = ?",
        timestamp(report.finished_at),
        chat.chat_id,
      );
  });
  if (report.halt_reason)
    await alerts.raise(
      "halted",
      report.halt_reason,
      haltNotices[report.halt_reason],
    );
  else alerts.resolve("halted");
  for (const chat of report.polled_chats) {
    alerts.resolve("chat_unavailable", String(chat.chat_id));
    alerts.resolve("chat_stale", String(chat.chat_id));
  }
  for (const chat of report.unavailable_chats)
    await alerts.raise(
      "chat_unavailable",
      String(chat.chat_id),
      `Telegram watcher could not poll ${chat.name}.`,
    );
  if (report.outcome !== "halted") {
    const stale = new Set(
      storage
        .all(
          "SELECT chat_id FROM chat_polls WHERE coalesce(last_success_at, watched_since) < ?",
          timestamp(new Date(report.finished_at.getTime() - 86_400_000)),
        )
        .map((row) => Number(row.chat_id)),
    );
    for (const chat of chats) {
      if (stale.has(chat.chat_id))
        await alerts.raise(
          "chat_stale",
          String(chat.chat_id),
          `Telegram watcher has not polled ${chat.name} for over 24 hours.`,
        );
    }
  }
  if (report.outcome === "failed")
    await alerts.raise(
      "run_failed",
      report.error_type!,
      `Telegram watcher run failed: ${report.error_type}.`,
    );
  else if (report.outcome === "completed") alerts.resolve("run_failed");
}

export async function runWatcher(
  storage: Storage,
  options: WatcherOptions,
): Promise<RunReport> {
  options.imageStore.clearUnfinished();
  const alerts = new Alerts(storage, options.notifier);
  const report = await pollRun(storage, options);
  await deliverMatches(
    storage,
    options.notifier,
    options.forwarder,
    alerts,
    options.clock,
  );
  await recordRun(storage, options.chats, report, alerts);
  return report;
}

export const FALLBACK_POLL_INTERVAL = 15 * 60_000;
export async function watchLive(
  storage: Storage,
  options: Omit<WatcherOptions, "connect" | "disconnect">,
  telegram: LiveTelegram,
  signal?: AbortSignal,
): Promise<RunOutcome | "stopped"> {
  while (!signal?.aborted) {
    let report: RunReport;
    try {
      report = await runWatcher(storage, {
        ...options,
        signal,
        connect: () => telegram.connect(),
      });
    } catch (error) {
      if (signal?.aborted) return "stopped";
      throw error;
    }
    console.info(`run finished: ${report.outcome}`);
    if (report.outcome !== "completed") return report.outcome;
    await telegram.waitForUpdate(FALLBACK_POLL_INTERVAL, signal);
  }
  return "stopped";
}
