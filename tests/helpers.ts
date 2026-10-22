import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve, join } from "node:path";
import type { TestContext } from "node:test";
import type {
  ChatClient,
  Forwarder,
  Image,
  JsonObject,
  LiveTelegram,
  Notifier,
  Page,
  Post,
  WatchedChat,
} from "../src/model.ts";
import { ForwardFailed, NotificationFailed } from "../src/model.ts";
import { createDatabase, Storage } from "../src/storage.ts";
import { ImageStore } from "../src/image-store.ts";
import { runWatcher } from "../src/watcher.ts";
import type { WatcherOptions } from "../src/watcher.ts";

export const T0 = new Date("2026-01-05T09:00:00Z");
export const CHAT: WatchedChat = {
  name: "Synthetic channel",
  chat_id: -1000000000001,
  access_hash: 1234567890123456789n,
  download_photos: false,
};
export const SECOND: WatchedChat = {
  ...CHAT,
  name: "Synthetic second channel",
  chat_id: -1000000000002,
};
export const CREDENTIALS = {
  api_id: 12345,
  api_hash: "0123456789abcdef0123456789abcdef",
};
export function post(id = 101, overrides: Partial<Post> = {}): Post {
  return {
    chat_id: CHAT.chat_id,
    message_id: id,
    posted_at: T0,
    edited_at: null,
    media_kind: null,
    album_id: null,
    text: "Synthetic text post",
    outgoing: false,
    ...overrides,
  };
}
export function directory(t: TestContext): string {
  const root = resolve(".test-tmp");
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const path = mkdtempSync(join(root, "case-"));
  t.after(() => rmSync(path, { recursive: true, force: true }));
  return path;
}

export class FakeTelegram implements LiveTelegram, ChatClient {
  posts: Post[] = [];
  latest = new Map<number, number>();
  failure: unknown = null;
  failures = new Map<number, unknown>();
  requests: {
    method: string;
    chat: number;
    after?: number;
    ids?: number[];
    limit?: number;
  }[] = [];
  connects = 0;
  closes = 0;
  waits: number[] = [];
  image: Image | null = {
    data: Buffer.from("synthetic image"),
    extension: ".jpg",
  };
  imageFailure: unknown = null;
  onWait?: () => void;
  async connect(): Promise<ChatClient> {
    this.connects++;
    if (this.failure) throw this.failure;
    return this;
  }
  async close(): Promise<void> {
    this.closes++;
  }
  check(chat: WatchedChat): void {
    if (this.failures.has(chat.chat_id)) throw this.failures.get(chat.chat_id);
  }
  async latestMessageId(chat: WatchedChat): Promise<number | null> {
    this.requests.push({ method: "latest", chat: chat.chat_id });
    this.check(chat);
    return (
      this.latest.get(chat.chat_id) ??
      Math.max(
        0,
        ...this.posts
          .filter((post) => post.chat_id === chat.chat_id)
          .map((post) => post.message_id),
      )
    );
  }
  async fetchAfter(
    chat: WatchedChat,
    after: number,
    limit: number,
  ): Promise<Page> {
    this.requests.push({ method: "after", chat: chat.chat_id, after, limit });
    this.check(chat);
    const posts = this.posts
      .filter(
        (post) => post.chat_id === chat.chat_id && post.message_id > after,
      )
      .sort((a, b) => a.message_id - b.message_id)
      .slice(0, limit);
    return {
      posts,
      last_message_id: posts.at(-1)?.message_id ?? null,
      reached_end: posts.length < limit,
    };
  }
  async fetchByIds(chat: WatchedChat, ids: number[]): Promise<Post[]> {
    this.requests.push({ method: "ids", chat: chat.chat_id, ids });
    this.check(chat);
    return this.posts.filter(
      (post) => post.chat_id === chat.chat_id && ids.includes(post.message_id),
    );
  }
  async fetchImage(chat: WatchedChat, id: number): Promise<Image | null> {
    this.requests.push({ method: "image", chat: chat.chat_id, ids: [id] });
    if (this.imageFailure) throw this.imageFailure;
    return this.image;
  }
  async waitForUpdate(timeout: number): Promise<void> {
    this.waits.push(timeout);
    this.onWait?.();
  }
}
export class FakeNotifier implements Notifier {
  messages: string[] = [];
  available = true;
  async notify(message: string): Promise<void> {
    if (!this.available) throw new NotificationFailed();
    this.messages.push(message);
  }
}
export class FakeForwarder implements Forwarder {
  payloads: JsonObject[] = [];
  failure: string | null = null;
  attempts = 0;
  async forward(payload: JsonObject): Promise<void> {
    this.attempts++;
    if (this.failure) throw new ForwardFailed(this.failure);
    this.payloads.push(payload);
  }
}

export function setup(t: TestContext) {
  const root = directory(t);
  const path = join(root, "watcher.db");
  createDatabase(path);
  const storage = new Storage(path);
  t.after(() => storage.close());
  const telegram = new FakeTelegram();
  const notifier = new FakeNotifier();
  const forwarder = new FakeForwarder();
  let now = new Date(T0.getTime() + 3_600_000);
  const options: WatcherOptions = {
    chats: [CHAT, SECOND],
    connect: () => telegram.connect(),
    disconnect: () => telegram.close(),
    listeners: [],
    notifier,
    forwarder,
    imageStore: new ImageStore(join(root, "media")),
    clock: () => now,
  };
  return {
    root,
    path,
    storage,
    telegram,
    notifier,
    forwarder,
    options,
    advance: (milliseconds: number) => {
      now = new Date(now.getTime() + milliseconds);
    },
    run: () => runWatcher(storage, options),
  };
}
