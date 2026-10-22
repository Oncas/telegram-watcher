import bigInt from "big-integer";
import { Api, Logger, TelegramClient, errors, utils } from "teleproto";
import { StringSession } from "teleproto/sessions";
import { LogLevel } from "teleproto/extensions/Logger";
import type { Credentials } from "./config.ts";
import type {
  ChatClient,
  Image,
  LiveTelegram,
  MediaKind,
  Page,
  Post,
  WatchedChat,
} from "./model.ts";
import {
  AuthorizationFailed,
  ChatUnavailable,
  NetworkUnavailable,
  RateLimited,
} from "./model.ts";
import { readPrivateFile } from "./private-files.ts";

export const MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024;
export const REQUEST_TIMEOUT = 60_000;

export function translateError(error: unknown): unknown {
  if (!(error instanceof Error)) return error;
  const rpc = error as Error & {
    errorMessage?: string;
    code?: string | number;
    seconds?: number;
  };
  const code = rpc.errorMessage ?? "";
  if (
    error instanceof errors.FloodWaitError ||
    /^FLOOD_(?:PREMIUM_)?WAIT(?:_\d+)?$/.test(code)
  )
    return new RateLimited(
      (rpc.seconds ?? Number(code.match(/\d+$/)?.[0] ?? 0)) * 1000,
    );
  if (
    [
      "AUTH_KEY_UNREGISTERED",
      "SESSION_REVOKED",
      "SESSION_EXPIRED",
      "AUTH_KEY_DUPLICATED",
    ].includes(code)
  )
    return new AuthorizationFailed("session_ended");
  if (["USER_DEACTIVATED", "USER_DEACTIVATED_BAN"].includes(code))
    return new AuthorizationFailed("account_deactivated");
  if (rpc.code === 401 || code.startsWith("FROZEN_"))
    return new AuthorizationFailed("authorization_refused");
  if (
    [
      "CHANNEL_PRIVATE",
      "CHANNEL_INVALID",
      "CHAT_ID_INVALID",
      "PEER_ID_INVALID",
    ].includes(code) ||
    rpc.code === 403
  )
    return new ChatUnavailable();
  if (
    error instanceof errors.ServerError ||
    error instanceof errors.TimedOutError ||
    (typeof rpc.code === "number" && rpc.code >= 500) ||
    [
      "ECONNREFUSED",
      "ECONNRESET",
      "ENETUNREACH",
      "EHOSTUNREACH",
      "ENOTFOUND",
      "EAI_AGAIN",
      "ETIMEDOUT",
      "EPIPE",
    ].includes(String(rpc.code)) ||
    /^(TIMEOUT|Not connected|Cannot send requests while disconnected)/i.test(
      rpc.message,
    )
  )
    return new NetworkUnavailable();
  return error;
}

export function inputPeer(chat: WatchedChat): Api.TypeInputPeer {
  if (chat.chat_id < -1_000_000_000_000)
    return new Api.InputPeerChannel({
      channelId: bigInt(-chat.chat_id - 1_000_000_000_000),
      accessHash: bigInt(chat.access_hash!.toString()),
    });
  if (chat.chat_id > 0)
    return new Api.InputPeerUser({
      userId: bigInt(chat.chat_id),
      accessHash: bigInt(chat.access_hash!.toString()),
    });
  return new Api.InputPeerChat({ chatId: bigInt(-chat.chat_id) });
}

export function mediaKind(
  media: Api.TypeMessageMedia | undefined,
): MediaKind | null {
  if (
    !media ||
    media instanceof Api.MessageMediaEmpty ||
    media instanceof Api.MessageMediaWebPage
  )
    return null;
  if (media instanceof Api.MessageMediaPhoto) return "photo";
  if (media instanceof Api.MessageMediaDocument) {
    if (media.document instanceof Api.Document) {
      const major = media.document.mimeType.split("/")[0];
      if (major === "video" || major === "audio") return major;
    }
    return "document";
  }
  return "other";
}

export function toPost(message: Api.Message): Post {
  return {
    chat_id: markedChatId(message.peerId),
    message_id: message.id,
    posted_at: new Date(message.date * 1000),
    edited_at:
      message.editDate === undefined ? null : new Date(message.editDate * 1000),
    media_kind: mediaKind(message.media),
    album_id: message.groupedId ? BigInt(message.groupedId.toString()) : null,
    text: message.message || null,
    outgoing: Boolean(message.out),
  };
}

export function markedChatId(
  peer: Api.TypePeer | Api.User | Api.Chat | Api.Channel,
): number {
  if (peer instanceof Api.PeerChannel || peer instanceof Api.Channel)
    return (
      -1_000_000_000_000 -
      Number(
        (peer instanceof Api.Channel ? peer.id : peer.channelId).toString(),
      )
    );
  if (peer instanceof Api.PeerChat || peer instanceof Api.Chat)
    return -Number(
      (peer instanceof Api.Chat ? peer.id : peer.chatId).toString(),
    );
  return Number((peer instanceof Api.User ? peer.id : peer.userId).toString());
}

export function sessionFromString(text: string): StringSession {
  const session = text.trim();
  const data = Buffer.from(session.slice(1), "base64");
  if (session[0] !== "1") throw new AuthorizationFailed("session_ended");
  const addressLength = data.length >= 3 ? data.readUInt16BE(1) : 0;
  if (addressLength > 0 && data.length === 261 + addressLength) {
    return new StringSession(session);
  }
  // Convert both Telethon address widths without modifying the saved session.
  if (data.length === 263 || data.length === 275) {
    const width = data.length - 259;
    const address =
      width === 4
        ? Array.from(data.subarray(1, 5)).join(".")
        : Array.from({ length: 8 }, (_, index) =>
            data.readUInt16BE(1 + index * 2).toString(16),
          ).join(":");
    const addressBytes = Buffer.from(address);
    const size = Buffer.alloc(2);
    size.writeUInt16BE(addressBytes.length);
    return new StringSession(
      "1" +
        Buffer.concat([
          data.subarray(0, 1),
          size,
          addressBytes,
          data.subarray(1 + width),
        ]).toString("base64"),
    );
  }
  throw new AuthorizationFailed("session_ended");
}

export function updateChatIds(
  update: Api.TypeUpdate | Api.TypeUpdates,
): number[] {
  if (update instanceof Api.Updates || update instanceof Api.UpdatesCombined)
    return update.updates.flatMap(updateChatIds);
  if (update instanceof Api.UpdateShort) return updateChatIds(update.update);
  if (update instanceof Api.UpdateShortMessage)
    return [Number(update.userId.toString())];
  if (update instanceof Api.UpdateShortChatMessage)
    return [-Number(update.chatId.toString())];
  if (
    update instanceof Api.UpdateNewMessage ||
    update instanceof Api.UpdateEditMessage ||
    update instanceof Api.UpdateNewChannelMessage ||
    update instanceof Api.UpdateEditChannelMessage
  )
    return update.message.peerId ? [markedChatId(update.message.peerId)] : [];
  if (update instanceof Api.UpdateDeleteChannelMessages)
    return [-1_000_000_000_000 - Number(update.channelId.toString())];
  return [];
}

export class WatcherTelegramClient extends TelegramClient {
  readonly receiveUpdates: boolean;
  constructor(
    credentials: Credentials,
    session: StringSession,
    receiveUpdates = false,
    onUpdate: (update: Api.TypeUpdate | Api.TypeUpdates) => void = () => {},
  ) {
    super(session, credentials.api_id, credentials.api_hash, {
      deviceModel: "Telegram watcher",
      systemVersion: "Linux",
      appVersion: "0.1.0",
      connectionRetries: 1,
      requestRetries: 2,
      reconnectRetries: 0,
      downloadRetries: 1,
      retryDelay: 1000,
      timeout: 10,
      autoReconnect: false,
      floodSleepThreshold: 60,
      baseLogger: new Logger(LogLevel.NONE),
    });
    this.receiveUpdates = receiveUpdates;
    // Updates are wake-up hints. The poller owns checkpoints and recovery;
    // library catch-up would request account-wide history outside our allowlist.
    this.updateManager.start = () => {};
    this.updateManager.catchUp = async () => {};
    this.updateManager.recoverIfStale = async () => {};
    this.updateManager.onUpdates = (update) => {
      if (receiveUpdates) onUpdate(update);
    };
    this.onError = async () => {
      console.warn("telegram connection error");
    };
  }
  override invoke<R extends Api.AnyRequest>(
    request: R,
    dcId?: number,
    options?: Api.ApiCallOptions,
  ): Promise<R["__response"]> {
    return super.invoke(
      this.receiveUpdates
        ? request
        : new Api.InvokeWithoutUpdates({ query: request }),
      dcId,
      options,
    );
  }
}

type TelegramApi = Pick<TelegramClient, "getMessages" | "downloadMedia">;
export class TelegramChatClient implements ChatClient {
  readonly client: TelegramApi;
  readonly timeout: number;
  readonly cancel: () => Promise<void>;
  constructor(
    client: TelegramApi,
    cancel: () => Promise<void> = async () => {},
    timeout = REQUEST_TIMEOUT,
  ) {
    this.client = client;
    this.cancel = cancel;
    this.timeout = timeout;
  }
  async request<T>(action: () => Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        action(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            void this.cancel().catch(() => {});
            reject(new NetworkUnavailable());
          }, this.timeout);
        }),
      ]);
    } catch (error) {
      throw translateError(error);
    } finally {
      clearTimeout(timer);
    }
  }
  async latestMessageId(chat: WatchedChat): Promise<number | null> {
    const messages = await this.request(() =>
      this.client.getMessages(inputPeer(chat), { limit: 1 }),
    );
    return messages[0]?.id ?? null;
  }
  async fetchAfter(
    chat: WatchedChat,
    messageId: number,
    limit: number,
  ): Promise<Page> {
    const messages = await this.request(() =>
      this.client.getMessages(inputPeer(chat), {
        limit,
        minId: messageId,
        reverse: true,
      }),
    );
    return {
      posts: messages
        .filter((message) => message instanceof Api.Message)
        .map(toPost),
      last_message_id: messages.length
        ? Math.max(...messages.map((message) => message.id))
        : null,
      reached_end: messages.length < limit,
    };
  }
  async fetchByIds(chat: WatchedChat, ids: number[]): Promise<Post[]> {
    const messages = await this.request(() =>
      this.client.getMessages(inputPeer(chat), { ids }),
    );
    return messages
      .filter((message) => message instanceof Api.Message)
      .map(toPost);
  }
  async fetchImage(chat: WatchedChat, id: number): Promise<Image | null> {
    const [message] = await this.request(() =>
      this.client.getMessages(inputPeer(chat), { ids: [id] }),
    );
    if (!(message instanceof Api.Message)) return null;
    const media = message.media;
    const photo =
      media instanceof Api.MessageMediaPhoto &&
      media.photo instanceof Api.Photo;
    const document =
      media instanceof Api.MessageMediaDocument &&
      media.document instanceof Api.Document
        ? media.document
        : null;
    if (
      !photo &&
      !(
        document?.mimeType.startsWith("image/") &&
        !document.attributes.some(
          (attribute) => attribute instanceof Api.DocumentAttributeSticker,
        )
      )
    )
      return null;
    if (document && document.size.greater(MAX_IMAGE_FILE_SIZE)) {
      console.warn("image too large to download");
      return null;
    }
    const data = await this.request(() => this.client.downloadMedia(message));
    const extension = utils.getExtension(media).replace(/^\./, "");
    return Buffer.isBuffer(data)
      ? {
          data,
          extension: extension
            ? `.${extension === "jpeg" ? "jpg" : extension}`
            : "",
        }
      : null;
  }
}

export class UpdateSignal {
  pending = false;
  wake: (() => void) | null = null;
  set(): void {
    this.pending = true;
    this.wake?.();
  }
  async wait(timeout: number, signal?: AbortSignal): Promise<void> {
    if (!this.pending && !signal?.aborted) {
      await new Promise<void>((resolve) => {
        const finish = () => {
          clearTimeout(timer);
          signal?.removeEventListener("abort", finish);
          this.wake = null;
          resolve();
        };
        const timer = setTimeout(finish, timeout);
        this.wake = finish;
        signal?.addEventListener("abort", finish, { once: true });
      });
    }
    this.pending = false;
  }
}

export class TelegramConnection implements LiveTelegram {
  readonly credentials: Credentials;
  readonly sessionPath: string;
  readonly chats: WatchedChat[] | null;
  readonly updates = new UpdateSignal();
  client: WatcherTelegramClient | null = null;
  adapter: TelegramChatClient | null = null;
  constructor(
    credentials: Credentials,
    sessionPath: string,
    chats: WatchedChat[] | null = null,
  ) {
    this.credentials = credentials;
    this.sessionPath = sessionPath;
    this.chats = chats;
  }
  async connect(): Promise<ChatClient> {
    if (this.adapter) {
      if (!this.client?.connected) throw new NetworkUnavailable();
      return this.adapter;
    }
    const text = readPrivateFile(this.sessionPath);
    if (text === null) throw new AuthorizationFailed("session_ended");
    const watched = new Set(this.chats?.map((chat) => chat.chat_id));
    this.client = new WatcherTelegramClient(
      this.credentials,
      sessionFromString(text),
      this.chats !== null,
      (update) => {
        if (updateChatIds(update).some((id) => watched.has(id)))
          this.updates.set();
      },
    );
    this.client.onDisconnect(() => this.updates.set());
    const adapter = new TelegramChatClient(this.client, () => this.close());
    await adapter.request(() => this.client!.connect());
    this.adapter = adapter;
    return adapter;
  }
  async close(): Promise<void> {
    await this.client?.destroy();
    this.adapter = null;
    this.client = null;
  }
  waitForUpdate(timeout: number, signal?: AbortSignal): Promise<void> {
    return this.updates.wait(timeout, signal);
  }
}

export function watchedChats(entities: unknown[]): WatchedChat[] {
  return entities.flatMap((entity) => {
    if (!(
      entity instanceof Api.Channel ||
      entity instanceof Api.Chat ||
      (entity instanceof Api.User && !entity.deleted)
    ))
      return [];
    return [
      {
        name: utils.getDisplayName(entity),
        chat_id: markedChatId(entity),
        access_hash:
          entity instanceof Api.Chat
            ? null
            : entity.accessHash === undefined
              ? null
              : BigInt(entity.accessHash.toString()),
        download_photos: false,
      },
    ];
  });
}

export async function listChats(
  credentials: Credentials,
  session: string,
): Promise<WatchedChat[]> {
  const client = new WatcherTelegramClient(
    credentials,
    sessionFromString(session),
  );
  try {
    await client.connect();
    const dialogs = await client.getDialogs({});
    return watchedChats(dialogs.map((dialog) => dialog.entity));
  } finally {
    await client.destroy();
  }
}

export interface Prompt {
  ask(question: string, hidden?: boolean): Promise<string>;
}
export async function signIn(
  credentials: Credentials,
  prompt: Prompt,
): Promise<string> {
  const session = new StringSession("");
  const client = new WatcherTelegramClient(credentials, session);
  try {
    await client.start({
      phoneNumber: () => prompt.ask("Phone number, with country code: "),
      phoneCode: () => prompt.ask("Login code sent by Telegram: ", true),
      password: () => prompt.ask("Two-step verification password: ", true),
      onError: (error) => {
        throw error;
      },
    });
    return session.save();
  } finally {
    await client.destroy();
  }
}
