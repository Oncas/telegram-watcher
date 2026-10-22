import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import type { JsonObject } from "../src/model.ts";
import {
  AuthorizationFailed,
  ChatUnavailable,
  NetworkUnavailable,
  PostFromOtherChatError,
  RateLimited,
} from "../src/model.ts";
import {
  ChatPoller,
  FALLBACK_POLL_INTERVAL,
  PostRecorder,
  watchLive,
} from "../src/watcher.ts";
import { CHAT, SECOND, T0, post, setup } from "./helpers.ts";

test("first poll starts at the latest ID without importing history or images", async (t) => {
  const s = setup(t);
  s.options.chats = [{ ...CHAT, download_photos: true }];
  s.telegram.posts = [post(100, { media_kind: "photo" })];
  await s.run();
  await s.run();
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 100);
  assert.equal(s.storage.all("SELECT * FROM events").length, 0);
  assert.ok(!s.telegram.requests.some((request) => request.method === "image"));
});
test("empty chats retain their first post, metadata only, with exact album IDs", async (t) => {
  const s = setup(t);
  await s.run();
  s.telegram.posts = [post(101, { album_id: 9223372036854775807n })];
  await s.run();
  await s.run();
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 101);
  assert.equal(s.storage.all("SELECT * FROM post_versions").length, 1);
  const query = s.storage.db.prepare("SELECT album_id FROM posts");
  query.setReadBigInts(true);
  assert.equal(query.get()!.album_id, 9223372036854775807n);
  assert.ok(!readFileSync(s.path).includes(Buffer.from("Synthetic text post")));
});
test("paging is capped, oldest first, and resumes without dropping posts", async (t) => {
  const s = setup(t);
  const recorder = new PostRecorder(s.storage, []);
  const poller = new ChatPoller(
    recorder,
    s.options.imageStore,
    s.options.clock,
    { pageSize: 2, maxPages: 2 },
  );
  await poller.poll(s.telegram, CHAT);
  s.telegram.posts = Array.from({ length: 7 }, (_, index) => post(index + 1));
  await poller.poll(s.telegram, CHAT);
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 4);
  await poller.poll(s.telegram, CHAT);
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 7);
  assert.equal(s.storage.all("SELECT * FROM posts").length, 7);
});
test("service messages advance the checkpoint without storing a post", async (t) => {
  const s = setup(t);
  s.storage.advance(CHAT.chat_id, 0);
  s.telegram.fetchAfter = async () => ({
    posts: [],
    last_message_id: 10,
    reached_end: true,
  });
  await new ChatPoller(
    new PostRecorder(s.storage, []),
    s.options.imageStore,
    s.options.clock,
  ).poll(s.telegram, CHAT);
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 10);
  assert.equal(s.storage.all("SELECT * FROM posts").length, 0);
});
test("rechecks preserve edit versions and first deletion time", async (t) => {
  const s = setup(t);
  await s.run();
  s.telegram.posts = [post(101), post(102)];
  await s.run();
  s.telegram.posts = [
    post(101, { edited_at: new Date(T0.getTime() + 1800_000) }),
  ];
  await s.run();
  const deletedAt = s.storage.one(
    "SELECT deleted_at FROM posts WHERE message_id = 102",
  )!.deleted_at;
  s.advance(3600_000);
  await s.run();
  assert.equal(
    s.storage.one("SELECT deleted_at FROM posts WHERE message_id = 102")!
      .deleted_at,
    deletedAt,
  );
  assert.deepEqual(
    s.storage
      .all("SELECT kind FROM events ORDER BY message_id, kind")
      .map((row) => row.kind),
    ["edited", "new", "deleted", "new"],
  );
});
test("old and deleted posts are excluded from rechecks", async (t) => {
  const s = setup(t);
  await s.run();
  s.telegram.posts = [
    post(101),
    post(102, { posted_at: new Date(T0.getTime() - 4 * 86_400_000) }),
  ];
  await s.run();
  s.telegram.posts = [];
  await s.run();
  const before = s.telegram.requests.filter(
    (request) => request.method === "ids",
  ).length;
  await s.run();
  assert.equal(
    s.telegram.requests.filter((request) => request.method === "ids").length,
    before,
  );
});
test("a failed recheck is never evidence of deletion", async (t) => {
  const s = setup(t);
  await s.run();
  s.telegram.posts = [post()];
  await s.run();
  s.telegram.fetchByIds = async () => {
    throw new NetworkUnavailable();
  };
  assert.equal((await s.run()).outcome, "network_unavailable");
  assert.equal(s.storage.one("SELECT deleted_at FROM posts")!.deleted_at, null);
});
for (const recheck of [false, true])
  test(`allowlist rejects a foreign ${recheck ? "recheck" : "page"} before any write or image download`, async (t) => {
    const s = setup(t);
    const recorder = new PostRecorder(s.storage, []);
    assert.throws(
      () =>
        recheck
          ? recorder.recordRecheck(
              CHAT.chat_id,
              [101],
              [post(101, { chat_id: SECOND.chat_id })],
              T0,
            )
          : recorder.recordNew(
              CHAT.chat_id,
              [post(), post(102, { chat_id: SECOND.chat_id })],
              T0,
              102,
            ),
      PostFromOtherChatError,
    );
    assert.equal(s.storage.all("SELECT * FROM posts").length, 0);
    assert.equal(s.storage.checkpoint(CHAT.chat_id), null);
    s.storage.advance(CHAT.chat_id, 0);
    s.options.chats = [{ ...CHAT, download_photos: true }];
    s.telegram.fetchAfter = async () => ({
      posts: [post(101, { chat_id: SECOND.chat_id, media_kind: "photo" })],
      last_message_id: 101,
      reached_end: true,
    });
    assert.equal((await s.run()).outcome, "failed");
    assert.ok(
      !s.telegram.requests.some((request) => request.method === "image"),
    );
  });
test("a failed page rolls back its posts, matches and checkpoint, keeping earlier pages", (t) => {
  const s = setup(t);
  const recorder = new PostRecorder(s.storage, []);
  recorder.recordNew(CHAT.chat_id, [post()], T0, 101);
  s.storage.db.exec(
    "CREATE TRIGGER reject_second BEFORE INSERT ON post_versions WHEN NEW.message_id = 103 BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END",
  );
  assert.throws(() =>
    recorder.recordNew(CHAT.chat_id, [post(102), post(103)], T0, 103),
  );
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 101);
  assert.deepEqual(
    s.storage.all("SELECT message_id FROM posts").map((row) => row.message_id),
    [101],
  );
});
test("checkpoints never move backwards", (t) => {
  const s = setup(t);
  s.storage.advance(CHAT.chat_id, 101);
  s.storage.advance(CHAT.chat_id, 100);
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 101);
});
for (const folder of ["downloaded", "rejected", "archived"])
  test(`images in ${folder} are not downloaded again`, async (t) => {
    const s = setup(t);
    s.options.chats = [{ ...CHAT, download_photos: true }];
    await s.run();
    s.telegram.posts = [
      post(101, { media_kind: "photo" }),
      post(102, { media_kind: "document" }),
    ];
    await s.run();
    const name = `${CHAT.chat_id}_101.jpg`;
    if (folder !== "downloaded")
      renameSync(
        join(s.root, "media", "downloaded", name),
        join(s.root, "media", folder, name),
      );
    s.storage.run("UPDATE checkpoints SET last_message_id=0");
    await s.run();
    assert.equal(
      s.telegram.requests.filter((request) => request.method === "image")
        .length,
      2,
    );
  });
for (const [kind, outgoing, enabled, expected] of [
  ["photo", false, true, true],
  ["document", false, true, true],
  ["video", false, true, false],
  ["audio", false, true, false],
  ["photo", true, true, false],
  ["photo", false, false, false],
] as const)
  test(`image policy for ${kind}, outgoing=${outgoing}, enabled=${enabled}`, async (t) => {
    const s = setup(t);
    s.options.chats = [{ ...CHAT, download_photos: enabled }];
    await s.run();
    s.telegram.posts = [post(101, { media_kind: kind, outgoing })];
    await s.run();
    assert.equal(
      s.telegram.requests.some((request) => request.method === "image"),
      expected,
    );
  });
test("failed image download leaves the checkpoint unchanged and retries", async (t) => {
  const s = setup(t);
  s.options.chats = [{ ...CHAT, download_photos: true }];
  await s.run();
  s.telegram.posts = [post(101, { media_kind: "photo" })];
  s.telegram.imageFailure = new NetworkUnavailable();
  assert.equal((await s.run()).outcome, "network_unavailable");
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 0);
  s.telegram.imageFailure = null;
  assert.equal((await s.run()).outcome, "completed");
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 101);
});
test("images that Telegram cannot deliver still allow metadata recording", async (t) => {
  const s = setup(t);
  s.options.chats = [{ ...CHAT, download_photos: true }];
  await s.run();
  s.telegram.posts = [post(101, { media_kind: "photo" })];
  s.telegram.image = null;
  await s.run();
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 101);
});
test("unfinished image writes are cleaned each run", async (t) => {
  const s = setup(t);
  mkdirSync(join(s.root, "media"));
  writeFileSync(join(s.root, "media", ".interrupted.part"), "partial");
  writeFileSync(join(s.root, "media", ".interrupted.part.tmp"), "partial");
  await s.run();
  assert.equal(existsSync(join(s.root, "media", ".interrupted.part")), false);
  assert.equal(
    existsSync(join(s.root, "media", ".interrupted.part.tmp")),
    false,
  );
});
test("only configured chats are requested and unavailable chats do not stop others", async (t) => {
  const s = setup(t);
  s.telegram.failures.set(CHAT.chat_id, new ChatUnavailable());
  const report = await s.run();
  assert.deepEqual(report.unavailable_chats, [CHAT]);
  assert.deepEqual(report.polled_chats, [SECOND]);
  assert.ok(
    s.telegram.requests.every((request) =>
      [CHAT.chat_id, SECOND.chat_id].includes(request.chat),
    ),
  );
  await s.run();
  assert.equal(s.notifier.messages.length, 1);
  s.telegram.failures.clear();
  await s.run();
  s.telegram.failures.set(CHAT.chat_id, new ChatUnavailable());
  await s.run();
  assert.equal(s.notifier.messages.length, 2);
});
test("network failures end the run and leave later chats untouched", async (t) => {
  const s = setup(t);
  s.telegram.failures.set(CHAT.chat_id, new NetworkUnavailable());
  assert.equal((await s.run()).outcome, "network_unavailable");
  assert.equal(s.storage.checkpoint(SECOND.chat_id), null);
  assert.equal(s.notifier.messages.length, 0);
});
test("rate-limit deadlines persist across runs and block all connections", async (t) => {
  const s = setup(t);
  s.telegram.failure = new RateLimited(7_200_000);
  assert.equal((await s.run()).outcome, "rate_limited");
  s.telegram.failure = null;
  assert.equal((await s.run()).outcome, "rate_limited");
  assert.equal(s.telegram.connects, 1);
  s.advance(7_200_000);
  assert.equal((await s.run()).outcome, "completed");
  assert.equal(s.telegram.connects, 2);
});
for (const reason of [
  "session_ended",
  "account_deactivated",
  "authorization_refused",
] as const)
  test(`authorization failure ${reason} halts every later run`, async (t) => {
    const s = setup(t);
    s.telegram.failure = new AuthorizationFailed(reason);
    assert.equal((await s.run()).outcome, "halted");
    s.telegram.failure = null;
    s.advance(3 * 86_400_000);
    assert.equal((await s.run()).outcome, "halted");
    assert.equal(s.telegram.connects, 1);
    assert.equal(s.notifier.messages.length, 1);
    assert.equal(s.storage.haltReason(), reason);
    s.storage.clearHalt();
    assert.equal((await s.run()).outcome, "completed");
  });
test("stale chats alert once and recover independently", async (t) => {
  const s = setup(t);
  await s.run();
  s.telegram.failure = new NetworkUnavailable();
  s.advance(86_400_000);
  await s.run();
  assert.equal(s.notifier.messages.length, 0);
  s.advance(1000);
  await s.run();
  await s.run();
  assert.equal(s.notifier.messages.length, 2);
  assert.ok(
    s.notifier.messages.every((message) => message.includes("over 24 hours")),
  );
  s.telegram.failure = null;
  await s.run();
  s.telegram.failure = new NetworkUnavailable();
  s.advance(86_401_000);
  await s.run();
  assert.equal(s.notifier.messages.length, 4);
});
test("undelivered halt notices retry without activating alerts", async (t) => {
  const s = setup(t);
  s.telegram.failure = new AuthorizationFailed("session_ended");
  s.notifier.available = false;
  await s.run();
  assert.equal(s.storage.all("SELECT * FROM active_alerts").length, 0);
  s.notifier.available = true;
  await s.run();
  assert.equal(s.notifier.messages.length, 1);
});
test("unexpected failures are sanitized, deduplicated and logged in runs", async (t) => {
  const s = setup(t);
  s.telegram.failure = new TypeError("private post content");
  await s.run();
  await s.run();
  assert.deepEqual(
    s.storage
      .all("SELECT outcome, error_type FROM runs")
      .map((row) => [row.outcome, row.error_type]),
    [
      ["failed", "TypeError"],
      ["failed", "TypeError"],
    ],
  );
  assert.equal(s.notifier.messages.length, 1);
  assert.ok(!s.notifier.messages[0].includes("private post content"));
  s.telegram.failure = null;
  await s.run();
  s.telegram.failure = new TypeError();
  await s.run();
  assert.equal(s.notifier.messages.length, 2);
});

const wholeText = {
  name: "whole_text",
  chat_id: CHAT.chat_id,
  listen: (value: ReturnType<typeof post>) => [{ text: value.text }],
};
test("listeners receive their chat's new posts, changed edits and no prior history", async (t) => {
  const s = setup(t);
  s.options.listeners = [wholeText];
  s.telegram.posts = [post(100)];
  await s.run();
  s.telegram.posts.push(post(101), post(102, { chat_id: SECOND.chat_id }));
  await s.run();
  assert.deepEqual(
    s.forwarder.payloads.map((payload) => payload.data),
    [{ text: "Synthetic text post" }],
  );
  s.telegram.posts[1] = post(101, {
    text: "Synthetic edited",
    edited_at: new Date(T0.getTime() + 1000),
  });
  await s.run();
  await s.run();
  assert.equal(s.forwarder.payloads.length, 2);
  assert.deepEqual(Object.keys(s.forwarder.payloads[0]), [
    "id",
    "listener",
    "message_id",
    "posted_at",
    "edited_at",
    "data",
  ]);
});
test("edits with identical reordered match data do not redeliver", async (t) => {
  const s = setup(t);
  s.options.listeners = [
    {
      ...wholeText,
      listen: (value) => [
        value.edited_at
          ? { second: "2", first: "1" }
          : { first: "1", second: "2" },
      ],
    },
  ];
  await s.run();
  s.telegram.posts = [post()];
  await s.run();
  s.telegram.posts = [post(101, { edited_at: new Date(T0.getTime() + 1000) })];
  await s.run();
  assert.equal(s.forwarder.payloads.length, 1);
});
test("a listener added later receives only new versions", async (t) => {
  const s = setup(t);
  await s.run();
  s.telegram.posts = [post()];
  await s.run();
  s.options.listeners = [wholeText];
  await s.run();
  assert.equal(s.forwarder.payloads.length, 0);
});
test("a broken listener cannot prevent other listeners or metadata recording", async (t) => {
  const s = setup(t);
  s.options.listeners = [
    {
      ...wholeText,
      name: "broken",
      listen: () => {
        throw new Error("private post content");
      },
    },
    wholeText,
  ];
  await s.run();
  s.telegram.posts = [post()];
  await s.run();
  assert.equal(s.forwarder.payloads.length, 1);
  assert.equal(s.storage.all("SELECT * FROM posts").length, 1);
});
test("partial invalid listener output is discarded atomically", async (t) => {
  const s = setup(t);
  s.options.listeners = [
    {
      ...wholeText,
      *listen() {
        yield { valid: true };
        yield { invalid: NaN } as JsonObject;
      },
    },
  ];
  await s.run();
  s.telegram.posts = [post()];
  await s.run();
  assert.equal(s.storage.pendingMatches("forwarded_at").length, 0);
  assert.equal(s.forwarder.payloads.length, 0);
});
test("failed forwarding retries in order, alerts once, and survives Telegram failure", async (t) => {
  const s = setup(t);
  s.options.listeners = [wholeText];
  await s.run();
  s.forwarder.failure = "SyntheticFailure";
  s.telegram.posts = [post(101), post(102)];
  await s.run();
  await s.run();
  assert.equal(
    s.notifier.messages.filter((message) =>
      message.includes("could not forward"),
    ).length,
    1,
  );
  s.forwarder.failure = null;
  s.telegram.failure = new NetworkUnavailable();
  await s.run();
  await s.run();
  assert.deepEqual(
    s.forwarder.payloads.map((payload) => payload.message_id),
    [101, 102],
  );
});
test("notification retries independently of successful forwarding, even while halted", async (t) => {
  const s = setup(t);
  s.options.listeners = [wholeText];
  await s.run();
  s.notifier.available = false;
  s.telegram.posts = [post()];
  await s.run();
  s.notifier.available = true;
  s.storage.halt("session_ended");
  await s.run();
  await s.run();
  assert.equal(s.forwarder.payloads.length, 1);
  assert.equal(
    s.notifier.messages.filter((message) => message.startsWith("whole_text"))
      .length,
    1,
  );
});
test("forwarding can be disabled while notifications continue", async (t) => {
  const s = setup(t);
  s.options.forwarder = null;
  s.options.listeners = [wholeText];
  await s.run();
  s.telegram.posts = [post()];
  await s.run();
  assert.equal(s.notifier.messages.length, 1);
  assert.equal(s.forwarder.payloads.length, 0);
});
test("the live loop polls immediately and wakes with a 15-minute fallback", async (t) => {
  const s = setup(t);
  const controller = new AbortController();
  s.telegram.onWait = () => {
    if (s.telegram.waits.length === 2) controller.abort();
    else s.telegram.posts = [post()];
  };
  assert.equal(
    await watchLive(s.storage, s.options, s.telegram, controller.signal),
    "stopped",
  );
  assert.equal(s.storage.checkpoint(CHAT.chat_id), 101);
  assert.deepEqual(s.telegram.waits, [
    FALLBACK_POLL_INTERVAL,
    FALLBACK_POLL_INTERVAL,
  ]);
});
test("a halted live loop exits without connecting", async (t) => {
  const s = setup(t);
  s.storage.halt("session_ended");
  assert.equal(await watchLive(s.storage, s.options, s.telegram), "halted");
  assert.equal(s.telegram.connects, 0);
});

test("shutdown during a Telegram request stops without recording a failure", async (t) => {
  const s = setup(t);
  const controller = new AbortController();
  s.telegram.latestMessageId = async () => {
    controller.abort();
    throw new NetworkUnavailable();
  };
  assert.equal(
    await watchLive(s.storage, s.options, s.telegram, controller.signal),
    "stopped",
  );
  assert.equal(s.storage.all("SELECT * FROM runs").length, 0);
  assert.equal(s.notifier.messages.length, 0);
});
