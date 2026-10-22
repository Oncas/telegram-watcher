import assert from "node:assert/strict";
import { test } from "node:test";
import bigInt from "big-integer";
import { Api, TelegramClient, errors } from "teleproto";
import { StringSession } from "teleproto/sessions";
import { TotalList } from "teleproto/Helpers";
import {
  AuthorizationFailed,
  ChatUnavailable,
  NetworkUnavailable,
  RateLimited,
} from "../src/model.ts";
import {
  MAX_IMAGE_FILE_SIZE,
  TelegramChatClient,
  TelegramConnection,
  UpdateSignal,
  WatcherTelegramClient,
  inputPeer,
  mediaKind,
  sessionFromString,
  toPost,
  translateError,
  updateChatIds,
  watchedChats,
} from "../src/telegram.ts";
import { CHAT, CREDENTIALS, directory } from "./helpers.ts";
import { join } from "node:path";

const peer = () => new Api.PeerChannel({ channelId: bigInt(1) });
function message(id = 101, media?: Api.TypeMessageMedia): Api.Message {
  return new Api.Message({
    id,
    peerId: peer(),
    date: 1767603600,
    message: "Synthetic text",
    media,
  });
}
function photo(): Api.MessageMediaPhoto {
  return new Api.MessageMediaPhoto({
    photo: new Api.Photo({
      id: bigInt(1),
      accessHash: bigInt(2),
      fileReference: Buffer.alloc(0),
      date: 1767603600,
      sizes: [],
      dcId: 1,
    }),
  });
}
function document(
  mimeType = "image/png",
  size = MAX_IMAGE_FILE_SIZE,
  attributes: Api.TypeDocumentAttribute[] = [],
): Api.MessageMediaDocument {
  return new Api.MessageMediaDocument({
    document: new Api.Document({
      id: bigInt(1),
      accessHash: bigInt(2),
      fileReference: Buffer.alloc(0),
      date: 1767603600,
      mimeType,
      size: bigInt(size),
      dcId: 1,
      attributes,
    }),
  });
}
function adapter() {
  const client = new TelegramClient(
    new StringSession(""),
    CREDENTIALS.api_id,
    CREDENTIALS.api_hash,
    {},
  );
  let messages: Api.TypeMessage[] = [];
  const calls: { entity: unknown; options: unknown }[] = [];
  let downloads = 0;
  client.getMessages = async (entity, options) => {
    calls.push({ entity, options });
    return Object.assign(
      new TotalList<Api.Message>(),
      messages,
    ) as TotalList<Api.Message>;
  };
  client.downloadMedia = async () => {
    downloads++;
    return Buffer.from("synthetic image");
  };
  return {
    client,
    adapter: new TelegramChatClient(client),
    calls,
    setMessages: (value: Api.TypeMessage[]) => {
      messages = value;
    },
    downloads: () => downloads,
  };
}
test("adapter reads oldest first and moves past service messages", async () => {
  const s = adapter();
  s.setMessages([
    message(),
    new Api.MessageService({
      id: 102,
      peerId: peer(),
      date: 1767603600,
      action: new Api.MessageActionEmpty(),
    }),
  ]);
  const page = await s.adapter.fetchAfter(CHAT, 100, 2);
  assert.equal(page.posts.length, 1);
  assert.equal(page.last_message_id, 102);
  assert.equal(page.reached_end, false);
  assert.deepEqual(s.calls[0].options, { limit: 2, minId: 100, reverse: true });
});
test("adapter handles short and empty pages", async () => {
  const s = adapter();
  s.setMessages([message()]);
  assert.equal((await s.adapter.fetchAfter(CHAT, 100, 100)).reached_end, true);
  assert.equal(await s.adapter.latestMessageId(CHAT), 101);
  s.setMessages([]);
  assert.equal(await s.adapter.latestMessageId(CHAT), null);
  assert.equal(
    (await s.adapter.fetchAfter(CHAT, 100, 100)).last_message_id,
    null,
  );
});
test("rechecks discard empty and service messages", async () => {
  const s = adapter();
  s.setMessages([message(), new Api.MessageEmpty({ id: 102 })]);
  assert.equal((await s.adapter.fetchByIds(CHAT, [101, 102])).length, 1);
});
test("post conversion keeps Telegram fields without sender identity", () => {
  const value = message(101, photo());
  value.editDate = 1767605400;
  value.groupedId = bigInt("9223372036854775807");
  value.out = true;
  assert.deepEqual(toPost(value), {
    chat_id: CHAT.chat_id,
    message_id: 101,
    posted_at: new Date(1767603600_000),
    edited_at: new Date(1767605400_000),
    media_kind: "photo",
    album_id: 9223372036854775807n,
    text: "Synthetic text",
    outgoing: true,
  });
});
for (const [media, kind] of [
  [undefined, null],
  [new Api.MessageMediaEmpty(), null],
  [photo(), "photo"],
  [document("video/mp4"), "video"],
  [document("audio/ogg"), "audio"],
  [document("image/png"), "document"],
  [new Api.MessageMediaUnsupported(), "other"],
] as const)
  test(`media kind maps to ${kind}`, () => {
    assert.equal(mediaKind(media), kind);
  });
for (const [media, expected] of [
  [photo(), ".jpg"],
  [document("image/png"), ".png"],
  [document("image/jpeg"), ".jpg"],
] as const)
  test(`downloadable images retain ${expected} extensions`, async () => {
    const s = adapter();
    s.setMessages([message(101, media)]);
    const image = await s.adapter.fetchImage(CHAT, 101);
    assert.equal(image?.extension, expected);
    assert.equal(image?.data.toString(), "synthetic image");
  });
for (const media of [
  undefined,
  document("video/mp4"),
  document("image/png", MAX_IMAGE_FILE_SIZE + 1),
  document("image/webp", 100, [
    new Api.DocumentAttributeSticker({
      alt: "synthetic",
      stickerset: new Api.InputStickerSetEmpty(),
    }),
  ]),
])
  test("non-images, stickers and oversized files are not downloaded", async () => {
    const s = adapter();
    s.setMessages([message(101, media)]);
    assert.equal(await s.adapter.fetchImage(CHAT, 101), null);
    assert.equal(s.downloads(), 0);
  });
test("images that cannot be delivered return null", async () => {
  const s = adapter();
  s.setMessages([message(101, photo())]);
  s.client.downloadMedia = async () => undefined as never;
  assert.equal(await s.adapter.fetchImage(CHAT, 101), null);
});
test("channel, group and user peers retain exact IDs and access hashes", () => {
  const channel = inputPeer(CHAT);
  assert.ok(channel instanceof Api.InputPeerChannel);
  assert.equal(channel.channelId.toString(), "1");
  assert.equal(channel.accessHash.toString(), CHAT.access_hash!.toString());
  const group = inputPeer({ ...CHAT, chat_id: -42, access_hash: null });
  assert.ok(group instanceof Api.InputPeerChat);
  assert.equal(group.chatId.toString(), "42");
  const user = inputPeer({ ...CHAT, chat_id: 12345 });
  assert.ok(user instanceof Api.InputPeerUser);
  assert.equal(user.userId.toString(), "12345");
});
for (const [code, expected] of [
  ["AUTH_KEY_UNREGISTERED", "session_ended"],
  ["AUTH_KEY_DUPLICATED", "session_ended"],
  ["SESSION_EXPIRED", "session_ended"],
  ["SESSION_REVOKED", "session_ended"],
  ["USER_DEACTIVATED", "account_deactivated"],
  ["USER_DEACTIVATED_BAN", "account_deactivated"],
  ["FROZEN_METHOD_INVALID", "authorization_refused"],
] as const)
  test(`Telegram error ${code} maps to ${expected}`, () => {
    const error = translateError(
      Object.assign(new Error("private payload"), { errorMessage: code }),
    );
    assert.ok(error instanceof AuthorizationFailed);
    assert.equal(error.reason, expected);
  });
for (const code of [
  "CHANNEL_PRIVATE",
  "CHANNEL_INVALID",
  "CHAT_ID_INVALID",
  "PEER_ID_INVALID",
])
  test(`Telegram error ${code} makes one chat unavailable`, () => {
    assert.ok(
      translateError(
        Object.assign(new Error(), { errorMessage: code }),
      ) instanceof ChatUnavailable,
    );
  });
for (const code of ["FLOOD_WAIT", "FLOOD_PREMIUM_WAIT"])
  test(`${code} preserves the wait duration`, () => {
    const error = translateError(
      Object.assign(new Error(), { errorMessage: code, seconds: 7200 }),
    );
    assert.ok(error instanceof RateLimited);
    assert.equal(error.wait, 7_200_000);
  });
test("actual teleproto RPC errors are translated", () => {
  const request = new Api.help.GetConfig();
  assert.ok(
    translateError(
      new errors.FloodWaitError({ capture: 120, request }),
    ) instanceof RateLimited,
  );
  assert.ok(
    translateError(new errors.SessionRevokedError({ request })) instanceof
      AuthorizationFailed,
  );
});
for (const code of [
  500,
  503,
  "ENETUNREACH",
  "EAI_AGAIN",
  "ECONNRESET",
  "ETIMEDOUT",
])
  test(`network error ${code} is retryable`, () => {
    assert.ok(
      translateError(Object.assign(new Error(), { code })) instanceof
        NetworkUnavailable,
    );
  });
test("unknown exceptions remain failures", () => {
  const error = new TypeError("synthetic");
  assert.equal(translateError(error), error);
});
test("request timeouts cancel the connection", async () => {
  const s = adapter();
  s.client.getMessages = async () => new Promise(() => {});
  let cancelled = 0;
  const client = new TelegramChatClient(
    s.client,
    async () => {
      cancelled++;
    },
    10,
  );
  await assert.rejects(client.latestMessageId(CHAT), NetworkUnavailable);
  assert.equal(cancelled, 1);
});
test("missing session stops before opening a connection", async (t) => {
  const client = new TelegramConnection(
    CREDENTIALS,
    join(directory(t), "missing"),
  );
  await assert.rejects(
    client.connect(),
    (error: unknown) =>
      error instanceof AuthorizationFailed && error.reason === "session_ended",
  );
  await client.close();
});
for (const width of [4, 16])
  test(`legacy Telethon session with ${width}-byte address loads without reauthentication`, async () => {
    const data = Buffer.alloc(259 + width, 0);
    data[0] = 2;
    data[1] = 127;
    data.writeUInt16BE(443, 1 + width);
    data.fill(42, 3 + width);
    const session = sessionFromString("1" + data.toString("base64url"));
    await session.load();
    assert.equal(session.dcId, 2);
    assert.equal(session.port, 443);
    assert.deepEqual(session.authKey?.getKey(), Buffer.alloc(256, 42));
    const reloaded = sessionFromString(session.save());
    await reloaded.load();
    assert.deepEqual(reloaded.authKey?.getKey(), session.authKey?.getKey());
  });
test("malformed sessions fail without exposing their content", () => {
  assert.throws(() => sessionFromString("private-value"), AuthorizationFailed);
});

test("native sessions with 14-byte addresses are not mistaken for legacy IPv6", async () => {
  const address = Buffer.from("synthetic.test");
  const data = Buffer.concat([
    Buffer.from([2, 0, address.length]),
    address,
    Buffer.from([1, 187]),
    Buffer.alloc(256, 42),
  ]);
  const session = sessionFromString("1" + data.toString("base64"));
  await session.load();
  assert.equal(session.serverAddress, "synthetic.test");
  assert.equal(session.port, 443);
  assert.deepEqual(session.authKey?.getKey(), Buffer.alloc(256, 42));
});
test("polling wraps requests without updates and disables automatic account catch-up", async (t) => {
  let invoked: Api.AnyRequest | undefined;
  t.mock.method(
    TelegramClient.prototype,
    "invoke",
    async (request: Api.AnyRequest) => {
      invoked = request;
      return true;
    },
  );
  const client = new WatcherTelegramClient(CREDENTIALS, new StringSession(""));
  await client.invoke(new Api.help.GetConfig());
  assert.ok(invoked instanceof Api.InvokeWithoutUpdates);
  client.updateManager.start();
  await client.updateManager.catchUp();
  await client.updateManager.recoverIfStale();
  assert.equal(client.updateManager.state, undefined);
  assert.equal(client.updateManager.isRunning, false);
  await client.destroy();
});
test("live mode delivers update hints and leaves requests unwrapped", async (t) => {
  let invoked: Api.AnyRequest | undefined;
  let hints = 0;
  t.mock.method(
    TelegramClient.prototype,
    "invoke",
    async (request: Api.AnyRequest) => {
      invoked = request;
      return true;
    },
  );
  const client = new WatcherTelegramClient(
    CREDENTIALS,
    new StringSession(""),
    true,
    () => {
      hints++;
    },
  );
  const request = new Api.help.GetConfig();
  await client.invoke(request);
  assert.equal(invoked, request);
  client.updateManager.onUpdates(
    new Api.UpdateShort({
      update: new Api.UpdateNewChannelMessage({
        message: message(),
        pts: 1,
        ptsCount: 1,
      }),
      date: 1767603600,
    }),
  );
  assert.equal(hints, 1);
  await client.destroy();
});
test("update containers identify chats without requesting entities", () => {
  const update = new Api.UpdateNewChannelMessage({
    message: message(),
    pts: 1,
    ptsCount: 1,
  });
  assert.deepEqual(
    updateChatIds(
      new Api.Updates({
        updates: [update],
        users: [],
        chats: [],
        date: 1767603600,
        seq: 1,
      }),
    ),
    [CHAT.chat_id],
  );
  assert.deepEqual(
    updateChatIds(
      new Api.UpdateDeleteChannelMessages({
        channelId: bigInt(1),
        messages: [101],
        pts: 1,
        ptsCount: 1,
      }),
    ),
    [CHAT.chat_id],
  );
  assert.deepEqual(
    updateChatIds(
      new Api.UpdateShortMessage({
        id: 101,
        userId: bigInt(12345),
        message: "Synthetic",
        pts: 1,
        ptsCount: 1,
        date: 1767603600,
      }),
    ),
    [12345],
  );
});
test("update signal retains hints during polling and supports fallback and shutdown", async () => {
  const signal = new UpdateSignal();
  signal.set();
  await signal.wait(60_000);
  assert.equal(signal.pending, false);
  const wait = signal.wait(60_000);
  signal.set();
  await wait;
  await signal.wait(1);
  const controller = new AbortController();
  const stopping = signal.wait(60_000, controller.signal);
  controller.abort();
  await stopping;
  assert.equal(signal.wake, null);
});
test("picker offers usable users, channels and groups only", () => {
  const users = [
    new Api.User({
      id: bigInt(12345),
      accessHash: bigInt(5),
      firstName: "Synthetic",
    }),
    new Api.User({ id: bigInt(12346), deleted: true }),
    new Api.UserEmpty({ id: bigInt(12347) }),
  ];
  assert.equal(watchedChats(users).length, 1);
  assert.equal(watchedChats(users)[0].access_hash, 5n);
});
