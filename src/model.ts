export interface WatchedChat {
  name: string;
  chat_id: number;
  access_hash: bigint | null;
  download_photos: boolean | "matched";
}

export type MediaKind = "photo" | "video" | "audio" | "document" | "other";

export interface Post {
  chat_id: number;
  message_id: number;
  posted_at: Date;
  edited_at: Date | null;
  media_kind: MediaKind | null;
  album_id: bigint | null;
  text: string | null;
  outgoing: boolean;
}

export interface Page {
  posts: Post[];
  last_message_id: number | null;
  reached_end: boolean;
}

export interface Image {
  data: Buffer;
  extension: string;
}

export interface ChatClient {
  latestMessageId(chat: WatchedChat): Promise<number | null>;
  fetchAfter(
    chat: WatchedChat,
    messageId: number,
    limit: number,
  ): Promise<Page>;
  fetchByIds(chat: WatchedChat, messageIds: number[]): Promise<Post[]>;
  fetchImage(chat: WatchedChat, messageId: number): Promise<Image | null>;
}

export interface Connection {
  connect(): Promise<ChatClient>;
  close(): Promise<void>;
}

export interface LiveTelegram extends Connection {
  waitForUpdate(timeout: number, signal?: AbortSignal): Promise<void>;
}

export type HaltReason =
  "session_ended" | "account_deactivated" | "authorization_refused";

export class NetworkUnavailable extends Error {}
export class ChatUnavailable extends Error {}
export class AuthorizationFailed extends Error {
  readonly reason: HaltReason;
  constructor(reason: HaltReason) {
    super(reason);
    this.reason = reason;
  }
}
export class RateLimited extends Error {
  readonly wait: number;
  constructor(wait: number) {
    super("rate limited");
    this.wait = wait;
  }
}

export type Json = null | boolean | number | string | Json[] | JsonObject;
export interface JsonObject {
  [key: string]: Json;
}

export interface Notifier {
  notify(message: string): Promise<void>;
}
export interface Forwarder {
  forward(payload: JsonObject): Promise<void>;
}
export class NotificationFailed extends Error {}
export class ForwardFailed extends Error {}

export function errorName(error: unknown): string {
  return error instanceof Error ? error.constructor.name : "UnknownError";
}

export function timestamp(value: Date | null): string | null {
  return value === null
    ? null
    : value.toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

export class PostFromOtherChatError extends Error {}
export function rejectOtherChats(chatId: number, posts: Post[]): void {
  if (posts.some((post) => post.chat_id !== chatId)) {
    throw new PostFromOtherChatError(
      `the client returned a post that is not from chat ${chatId}`,
    );
  }
}
