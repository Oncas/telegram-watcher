import assert from "node:assert/strict";
import { chmodSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import {
  ConfigError,
  CredentialsError,
  credentials,
  loadConfig,
  loadCredentials,
  loadForwardTarget,
  saveConfig,
  saveCredentials,
} from "../src/config.ts";
import {
  PrivateFileError,
  readPrivateFile,
  writePrivateFile,
} from "../src/private-files.ts";
import { selectChats, terminalSafe } from "../src/cli.ts";
import { CHAT, CREDENTIALS, SECOND, directory } from "./helpers.ts";

test("existing TOML supports channels, groups, users and exact 64-bit access hashes", (t) => {
  const path = join(directory(t), "config.toml");
  const chats = [
    CHAT,
    { ...CHAT, chat_id: -42, access_hash: null },
    {
      ...CHAT,
      chat_id: 12345,
      access_hash: -9223372036854775808n,
      download_photos: true,
    },
  ];
  saveConfig(path, chats);
  assert.deepEqual(loadConfig(path), chats);
  assert.equal(statSync(path).mode & 0o777, 0o600);
  assert.match(
    readFileSync(path, "utf8"),
    /access_hash = -9223372036854775808/,
  );
});

const invalidConfigs = [
  "[[chats]",
  "",
  "chats=[]",
  'chats="Synthetic"',
  '[[chats]]\nname="Synthetic"',
  "[[chats]]\nchat_id=-42",
  "[[chats]]\nname=42\nchat_id=-42",
  '[[chats]]\nname="Synthetic"\nchat_id="-42"',
  '[[chats]]\nname="Synthetic"\nchat_id=0',
  '[[chats]]\nname="Synthetic"\nchat_id=12345',
  '[[chats]]\nname="Synthetic"\nchat_id=-1000000000001',
  '[[chats]]\nname="Synthetic"\nchat_id=-42\naccess_hash=123',
  '[[chats]]\nname="Synthetic"\nchat_id=-42\ndownload_photos="yes"',
  '[[chats]]\nname="Synthetic"\nchat_id=-42\ndownload_photos=1',
  '[[chats]]\nname="Synthetic"\nchat_id=-42\nacess_hash=123',
  'poll_interval=3600\n[[chats]]\nname="Synthetic"\nchat_id=-42',
  '[[chats]]\nname="Synthetic"\nchat_id=-42\n[[chats]]\nname="Duplicate"\nchat_id=-42',
  '[[chats]]\nname="Synthetic"\nchat_id=-42.0',
  '[[chats]]\nname="Synthetic"\nchat_id=true',
  '[[chats]]\nname="Synthetic"\nchat_id=9223372036854775807\naccess_hash=1',
];
for (const [index, text] of invalidConfigs.entries())
  test(`invalid config ${index + 1} is refused`, (t) => {
    const path = join(directory(t), "config.toml");
    writeFileSync(path, text);
    assert.throws(() => loadConfig(path), ConfigError);
  });
test("missing config is refused", (t) => {
  assert.throws(() => loadConfig(join(directory(t), "absent")), ConfigError);
});
test("config strings escape controls and round-trip", (t) => {
  const path = join(directory(t), "config.toml");
  const chats = [
    {
      ...CHAT,
      name: 'Quote " backslash \\ tab \t delete \x7f unicode \u{1f600}',
    },
  ];
  saveConfig(path, chats);
  assert.deepEqual(loadConfig(path), chats);
});
test("private writes replace content, stale temporaries and broad permissions", (t) => {
  const path = join(directory(t), "secret");
  writeFileSync(path, "old", { mode: 0o644 });
  writeFileSync(join(directory(t), "unrelated"), "untouched");
  writeFileSync(join(path, "..", ".secret.tmp"), "interrupted");
  assert.throws(() => readPrivateFile(path), PrivateFileError);
  writePrivateFile(path, "new");
  assert.equal(readPrivateFile(path), "new");
  assert.equal(statSync(path).mode & 0o777, 0o600);
});
test("missing private file returns null", (t) => {
  assert.equal(readPrivateFile(join(directory(t), "missing")), null);
});
test("credentials round-trip without number coercion", (t) => {
  const path = join(directory(t), "credentials.toml");
  saveCredentials(path, CREDENTIALS);
  assert.deepEqual(loadCredentials(path), CREDENTIALS);
});
for (const id of [0, -1, true, "12345", 1.5, null])
  test(`invalid credential ID ${id} is refused`, () => {
    assert.throws(
      () => credentials(id, CREDENTIALS.api_hash),
      CredentialsError,
    );
  });
for (const hash of ["", "private-value", "a".repeat(31), "A".repeat(32), 123])
  test("malformed credential hash is refused without echoing it", () => {
    assert.throws(
      () => credentials(12345, hash),
      (error: unknown) =>
        error instanceof CredentialsError &&
        !error.message.includes("private-value"),
    );
  });
test("credential TOML errors never echo content", (t) => {
  const path = join(directory(t), "credentials.toml");
  writePrivateFile(path, 'api_hash = "private-value');
  assert.throws(
    () => loadCredentials(path),
    (error: unknown) =>
      error instanceof CredentialsError &&
      !error.message.includes("private-value"),
  );
  writePrivateFile(
    path,
    'api_id=12345.0\napi_hash="0123456789abcdef0123456789abcdef"',
  );
  assert.throws(() => loadCredentials(path), CredentialsError);
});
test("forwarding is optional and settings remain private", (t) => {
  const path = join(directory(t), "forward.toml");
  assert.equal(loadForwardTarget(path), null);
  writePrivateFile(
    path,
    'url="https://example.invalid/matches"\ntoken="synthetic"',
  );
  assert.equal(loadForwardTarget(path)?.token, "synthetic");
  chmodSync(path, 0o644);
  assert.throws(() => loadForwardTarget(path), ConfigError);
});
for (const text of [
  'url="file:///tmp/matches"\ntoken="synthetic"',
  'url="http://example.invalid:0"\ntoken="synthetic"',
  'url="http://example.invalid"\ntoken="line\\nbreak"',
  'url="http://example.invalid"',
  'url="http://example.invalid"\ntoken=123',
  'invalid="private-value',
])
  test("invalid forward settings are refused without echoing values", (t) => {
    const path = join(directory(t), "forward.toml");
    writePrivateFile(path, text);
    assert.throws(
      () => loadForwardTarget(path),
      (error: unknown) =>
        error instanceof ConfigError &&
        !error.message.includes("private-value"),
    );
  });
test("chat selection accepts ordered numbers, commas and duplicates", () => {
  assert.deepEqual(selectChats([CHAT, SECOND], "2, 1 2"), [SECOND, CHAT]);
});
for (const answer of ["", "0", "3", "-1", "1.0", "a", "\uff11"])
  test(`invalid chat selection ${JSON.stringify(answer)} is refused`, () => {
    assert.throws(() => selectChats([CHAT, SECOND], answer));
  });
test("terminal output strips escape sequences and controls", () => {
  assert.equal(terminalSafe("Synthetic\x1b[31m\n\t\x7f"), "Synthetic?[31m???");
});
