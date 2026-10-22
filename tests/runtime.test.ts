import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:http";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";
import { main } from "../src/cli.ts";
import { saveConfig, saveCredentials } from "../src/config.ts";
import { DesktopNotifier, HttpForwarder } from "../src/delivery.ts";
import { ImageStore } from "../src/image-store.ts";
import { hear, identityJson, loadListeners } from "../src/listeners.ts";
import { ForwardFailed, NotificationFailed } from "../src/model.ts";
import { writePrivateFile } from "../src/private-files.ts";
import { acquireRunLock, AlreadyRunning } from "../src/run-lock.ts";
import { createDatabase, Storage, StorageError } from "../src/storage.ts";
import {
  CHAT,
  CREDENTIALS,
  FakeNotifier,
  directory,
  post,
  setup,
} from "./helpers.ts";

test("database creation and opening never replace or recreate files", (t) => {
  const path = join(directory(t), "watcher.db");
  assert.throws(() => new Storage(path), StorageError);
  assert.equal(existsSync(path), false);
  createDatabase(path);
  const before = readFileSync(path);
  assert.throws(() => createDatabase(path), StorageError);
  assert.deepEqual(readFileSync(path), before);
  assert.equal(statSync(path).mode & 0o777, 0o600);
});
test("foreign files and unknown schema versions are refused", (t) => {
  const root = directory(t);
  const invalid = join(root, "invalid");
  writeFileSync(invalid, "synthetic");
  assert.throws(() => new Storage(invalid), StorageError);
  const path = join(root, "watcher.db");
  createDatabase(path);
  const db = new DatabaseSync(path);
  db.exec("PRAGMA user_version=999");
  db.close();
  assert.throws(() => new Storage(path), StorageError);
});
test("SQLite sidecars retain private permissions", (t) => {
  const s = setup(t);
  s.storage.run("INSERT INTO checkpoints VALUES (?, ?)", CHAT.chat_id, 0);
  for (const suffix of ["", "-wal", "-shm"])
    if (existsSync(s.path + suffix))
      assert.equal(statSync(s.path + suffix).mode & 0o077, 0);
});
test("listener loader supports TypeScript and JavaScript, skips the example, and isolates load failures", async (t) => {
  const root = directory(t);
  writeFileSync(
    join(root, "signals.ts"),
    `export const CHAT_ID = ${CHAT.chat_id};\nexport function* listen(post: { text: string }) { yield { text: post.text }; }\n`,
  );
  writeFileSync(
    join(root, "extra.mjs"),
    `export const CHAT_ID = ${CHAT.chat_id};\nexport const listen = () => [];\n`,
  );
  writeFileSync(
    join(root, "example.ts"),
    "throw new Error('example must not run')",
  );
  writeFileSync(join(root, "broken.ts"), "throw new Error('private content')");
  writeFileSync(join(root, "invalid.ts"), "export const listen = () => [];");
  assert.deepEqual(
    (await loadListeners(root)).map((listener) => listener.name),
    ["extra", "signals"],
  );
  assert.deepEqual(
    hear(await loadListeners(root), post()).map((match) => match.data),
    [{ text: "Synthetic text post" }],
  );
  assert.deepEqual(await loadListeners(join(root, "missing")), []);
});
test("a copy of the example is a working listener", async (t) => {
  const root = directory(t);
  const source = readFileSync(resolve("listeners/example.ts"), "utf8").replace(
    '"../src/model.ts"',
    JSON.stringify(resolve("src/model.ts")),
  );
  writeFileSync(join(root, "signals.ts"), source);
  const [listener] = await loadListeners(root);
  assert.deepEqual(
    [
      ...listener.listen(
        post(101, { text: "Synthetic signal: BUY ABC at 1.25" }),
      ),
    ],
    [{ side: "BUY", symbol: "ABC", price: "1.25" }],
  );
});
test("match identity keeps the original sorted ASCII JSON encoding", () => {
  assert.equal(
    identityJson([
      "synthetic",
      CHAT.chat_id,
      101,
      { second: "\u00e9\u{1f600}", first: [true, null] },
    ]),
    '["synthetic", -1000000000001, 101, {"first": [true, null], "second": "\\u00e9\\ud83d\\ude00"}]',
  );
});
for (const invalid of [NaN, Infinity, undefined, new Date(), 1n, () => {}])
  test("non-JSON listener data is rejected", () => {
    const listener = {
      name: "invalid",
      chat_id: CHAT.chat_id,
      listen: () => [{ value: invalid }] as never,
    };
    assert.deepEqual(hear([listener], post()), []);
  });
test("cyclic JSON is rejected and shared JSON objects are allowed", () => {
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  assert.deepEqual(
    hear(
      [
        {
          name: "cyclic",
          chat_id: CHAT.chat_id,
          listen: () => [cyclic] as never,
        },
      ],
      post(),
    ),
    [],
  );
  const value = { text: "synthetic" };
  assert.equal(
    hear(
      [
        {
          name: "shared",
          chat_id: CHAT.chat_id,
          listen: () => [{ first: value, second: value }],
        },
      ],
      post(),
    ).length,
    1,
  );
});
test("image files and folders are private and names cannot escape their folder", (t) => {
  const root = join(directory(t), "media");
  const images = new ImageStore(root);
  assert.equal(existsSync(root), false);
  images.add(CHAT.chat_id, 101, {
    data: Buffer.from("synthetic"),
    extension: ".jpg",
  });
  assert.equal(statSync(root).mode & 0o777, 0o700);
  for (const folder of ["downloaded", "rejected", "archived"])
    assert.equal(statSync(join(root, folder)).mode & 0o777, 0o700);
  assert.equal(
    statSync(join(root, "downloaded", `${CHAT.chat_id}_101.jpg`)).mode & 0o777,
    0o600,
  );
  assert.equal(images.contains(CHAT.chat_id, 10), false);
  assert.throws(() =>
    images.add(CHAT.chat_id, 102, {
      data: Buffer.alloc(0),
      extension: "/../../outside",
    }),
  );
  images.add(CHAT.chat_id, 103, { data: Buffer.alloc(0), extension: "" });
  assert.equal(images.contains(CHAT.chat_id, 103), true);
});
test("kernel run locks reject concurrent processes and recover after process death", async (t) => {
  const root = directory(t);
  const lock = join(root, "run.lock");
  const module = new URL("../src/run-lock.ts", import.meta.url).href;
  const child = spawn(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `import { acquireRunLock } from ${JSON.stringify(module)}; acquireRunLock(process.argv[1]); console.log('locked'); setInterval(() => {}, 1000);`,
      lock,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  t.after(() => {
    child.kill("SIGKILL");
  });
  await once(child.stdout!, "data");
  assert.throws(() => acquireRunLock(lock), AlreadyRunning);
  const exit = once(child, "exit");
  child.kill("SIGKILL");
  await exit;
  const release = acquireRunLock(lock);
  release();
  assert.equal(statSync(lock).mode & 0o777, 0o600);
});
test("flock locks are compatible with the existing Python process lock", (t) => {
  const path = join(directory(t), "run.lock");
  const release = acquireRunLock(path);
  try {
    assert.equal(
      spawnSync("flock", ["-n", "-E", "75", path, "true"]).status,
      75,
    );
  } finally {
    release();
  }
  assert.equal(spawnSync("flock", ["-n", "-E", "75", path, "true"]).status, 0);
});

test("HTTP forwarding sends JSON and a bearer token", async (t) => {
  let received: unknown;
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    received = {
      method: request.method,
      authorization: request.headers.authorization,
      type: request.headers["content-type"],
      data: JSON.parse(Buffer.concat(chunks).toString()),
    };
    response.writeHead(204);
    response.end();
  });
  server.listen(0);
  await once(server, "listening");
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const port = (server.address() as { port: number }).port;
  await new HttpForwarder({
    url: `http://localhost:${port}`,
    token: "synthetic",
  }).forward({ signal: "synthetic" });
  assert.deepEqual(received, {
    method: "POST",
    authorization: "Bearer synthetic",
    type: "application/json",
    data: { signal: "synthetic" },
  });
});
for (const status of [301, 302, 303, 307, 308, 400, 401, 404, 500])
  test(`HTTP ${status} fails without following redirects`, async (t) => {
    let requests = 0;
    const server = createServer((_, response) => {
      requests++;
      response.writeHead(status, { Location: "/redirected" });
      response.end("private endpoint response");
    });
    server.listen(0);
    await once(server, "listening");
    t.after(() => {
      server.closeAllConnections();
      server.close();
    });
    const port = (server.address() as { port: number }).port;
    await assert.rejects(
      new HttpForwarder({
        url: `http://localhost:${port}`,
        token: "synthetic",
      }).forward({}),
      (error: unknown) =>
        error instanceof ForwardFailed && error.message === `HTTP ${status}`,
    );
    assert.equal(requests, 1);
  });
test("HTTP requests that never answer are bounded by a timeout", async (t) => {
  let received = false;
  const server = createServer(() => {
    received = true;
  });
  server.listen(0);
  await once(server, "listening");
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const port = (server.address() as { port: number }).port;
  await assert.rejects(
    new HttpForwarder(
      { url: `http://localhost:${port}`, token: "synthetic" },
      30,
    ).forward({}),
    ForwardFailed,
  );
  assert.equal(received, true);
});
test("unreachable HTTP endpoints fail with a sanitized error", async () => {
  await assert.rejects(
    new HttpForwarder({
      url: "http://localhost:1",
      token: "synthetic",
    }).forward({}),
    ForwardFailed,
  );
});
test("desktop notifications use notify-send without shell evaluation", async (t) => {
  const root = directory(t);
  writeFileSync(
    join(root, "notify-send"),
    '#!/bin/sh\nprintf "%s\\n" "$@" > "$WATCHER_TEST_LOG"\n',
    { mode: 0o700 },
  );
  const originalPath = process.env.PATH;
  const originalLog = process.env.WATCHER_TEST_LOG;
  process.env.PATH = root;
  process.env.WATCHER_TEST_LOG = join(root, "notification");
  t.after(() => {
    if (originalPath === undefined) delete process.env.PATH;
    else process.env.PATH = originalPath;
    if (originalLog === undefined) delete process.env.WATCHER_TEST_LOG;
    else process.env.WATCHER_TEST_LOG = originalLog;
  });
  await new DesktopNotifier().notify("Synthetic $(touch unwanted) message");
  assert.match(
    readFileSync(join(root, "notification"), "utf8"),
    /Synthetic \$\(touch unwanted\) message/,
  );
  chmodSync(join(root, "notify-send"), 0o600);
  await assert.rejects(
    new DesktopNotifier().notify("Synthetic"),
    NotificationFailed,
  );
});

test("CLI preserves commands, refuses overwrites and runs safely without a session", async (t) => {
  const root = directory(t);
  const previous = process.cwd();
  process.chdir(root);
  t.after(() => process.chdir(previous));
  const notifier = new FakeNotifier();
  assert.equal(await main(["--help"], { notifier }), 0);
  assert.equal(await main(["unknown"], { notifier }), 2);
  assert.equal(await main(["init"], { notifier }), 0);
  assert.equal(statSync("var").mode & 0o777, 0o700);
  const before = readFileSync("var/watcher.db");
  assert.equal(await main(["init"], { notifier }), 1);
  assert.deepEqual(readFileSync("var/watcher.db"), before);
  saveConfig("var/config.toml", [CHAT]);
  saveCredentials("var/credentials.toml", CREDENTIALS);
  assert.equal(await main(["run"], { notifier }), 0);
  assert.equal(notifier.messages.length, 1);
  assert.match(notifier.messages[0], /session ended/);
  assert.equal(await main(["listen"], { notifier }), 0);
  assert.equal(notifier.messages.length, 1);
  assert.equal(await main(["pick-chats"], { notifier }), 1);
  assert.equal(await main(["clear-halt"], { notifier }), 0);
  const storage = new Storage("var/watcher.db");
  assert.equal(storage.haltReason(), null);
  storage.close();
});
test("CLI validates configuration before opening the database and respects run locks", async (t) => {
  const root = directory(t);
  const previous = process.cwd();
  process.chdir(root);
  t.after(() => process.chdir(previous));
  const notifier = new FakeNotifier();
  mkdirSync("var");
  createDatabase("var/watcher.db");
  const before = readFileSync("var/watcher.db");
  writePrivateFile("var/config.toml", "chats=[]");
  assert.equal(await main(["run"], { notifier }), 1);
  assert.deepEqual(readFileSync("var/watcher.db"), before);
  const release = acquireRunLock("var/run.lock");
  try {
    assert.equal(await main(["run"], { notifier }), 0);
    assert.equal(await main(["listen"], { notifier }), 1);
  } finally {
    release();
  }
});
test("CLI rejects malformed credentials without saving them or contacting Telegram", async (t) => {
  const root = directory(t);
  const previous = process.cwd();
  process.chdir(root);
  t.after(() => process.chdir(previous));
  mkdirSync("var");
  const answers = ["12345", "invalid"];
  assert.equal(
    await main(["login"], { prompt: { ask: async () => answers.shift()! } }),
    1,
  );
  assert.equal(existsSync("var/credentials.toml"), false);
  assert.equal(existsSync("var/telegram.session"), false);
});
