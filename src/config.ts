import { readFileSync } from "node:fs";
import { parse } from "smol-toml";
import type { WatchedChat } from "./model.ts";
import { readPrivateFile, writePrivateFile } from "./private-files.ts";

export class ConfigError extends Error {}
export class CredentialsError extends Error {}
export interface Credentials {
  api_id: number;
  api_hash: string;
}
export interface ForwardTarget {
  url: string;
  token: string;
}

function table(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function rejectUnknown(
  data: Record<string, unknown>,
  keys: string[],
  label: string,
): void {
  const unknown = Object.keys(data).filter((key) => !keys.includes(key));
  if (unknown.length) {
    throw new ConfigError(
      `${label}: unknown keys ${unknown.sort().join(", ")}`,
    );
  }
}

function readToml(text: string, path: string): Record<string, unknown> {
  try {
    return parse(text, { integersAsBigInt: true });
  } catch {
    throw new ConfigError(`${path} is not valid TOML`);
  }
}

export function loadConfig(path: string): WatchedChat[] {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    throw new ConfigError(`cannot read ${path}`);
  }
  const data = readToml(text, path);
  rejectUnknown(data, ["chats"], "config");
  if (!Array.isArray(data.chats) || data.chats.length === 0) {
    throw new ConfigError("config must list chats as [[chats]] tables");
  }
  const seen = new Set<number>();
  return data.chats.map((entry: unknown, index: number) => {
    const label = `chat #${index + 1}`;
    if (!table(entry)) throw new ConfigError(`${label}: expected a table`);
    rejectUnknown(
      entry,
      ["name", "chat_id", "access_hash", "download_photos"],
      label,
    );
    if (typeof entry.name !== "string" || typeof entry.chat_id !== "bigint") {
      throw new ConfigError(`${label}: requires a name and an integer chat_id`);
    }
    const chatId = Number(entry.chat_id);
    if (!Number.isSafeInteger(chatId) || chatId === 0) {
      throw new ConfigError(`${label}: chat_id must be a marked chat ID`);
    }
    const accessHash = entry.access_hash ?? null;
    if (accessHash !== null && typeof accessHash !== "bigint") {
      throw new ConfigError(`${label}: access_hash must be an integer`);
    }
    if ((chatId > 0 || chatId < -1_000_000_000_000) && accessHash === null) {
      throw new ConfigError(
        `${label}: a channel or private chat needs an access_hash`,
      );
    }
    if (chatId < 0 && chatId >= -1_000_000_000_000 && accessHash !== null) {
      throw new ConfigError(
        `${label}: a basic group has no access_hash; is the -100 prefix missing from chat_id?`,
      );
    }
    if (
      entry.download_photos !== undefined &&
      typeof entry.download_photos !== "boolean" &&
      entry.download_photos !== "matched"
    ) {
      throw new ConfigError(
        `${label}: download_photos must be a boolean or "matched"`,
      );
    }
    if (seen.has(chatId))
      throw new ConfigError(`chat_id ${chatId} is listed more than once`);
    seen.add(chatId);
    return {
      name: entry.name,
      chat_id: chatId,
      access_hash: accessHash,
      download_photos:
        entry.download_photos === "matched"
          ? "matched"
          : entry.download_photos === true,
    };
  });
}

export function saveConfig(path: string, chats: WatchedChat[]): void {
  writePrivateFile(
    path,
    chats
      .map((chat) =>
        [
          "[[chats]]",
          `name = ${JSON.stringify(chat.name).replace(/\x7f/g, "\\u007f")}`,
          `chat_id = ${chat.chat_id}`,
          ...(chat.access_hash === null
            ? []
            : [`access_hash = ${chat.access_hash}`]),
          ...(chat.download_photos === "matched"
            ? ['download_photos = "matched"']
            : chat.download_photos
              ? ["download_photos = true"]
              : []),
        ].join("\n"),
      )
      .join("\n\n") + "\n",
  );
}

export function credentials(apiId: unknown, apiHash: unknown): Credentials {
  const id = typeof apiId === "bigint" ? Number(apiId) : apiId;
  if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0) {
    throw new CredentialsError("api_id must be a positive whole number");
  }
  if (typeof apiHash !== "string" || !/^[0-9a-f]{32}$/.test(apiHash)) {
    throw new CredentialsError("api_hash must be 32 hexadecimal characters");
  }
  return { api_id: id, api_hash: apiHash };
}

export function loadCredentials(path: string): Credentials {
  try {
    const text = readPrivateFile(path);
    if (text === null)
      throw new CredentialsError(
        `${path} is missing; run telegram-watcher login`,
      );
    const data = readToml(text, path);
    if (Object.keys(data).sort().join(",") !== "api_hash,api_id") {
      throw new CredentialsError(
        `${path} must contain exactly api_id and api_hash`,
      );
    }
    if (typeof data.api_id !== "bigint")
      throw new CredentialsError("api_id must be a positive whole number");
    return credentials(data.api_id, data.api_hash);
  } catch (error) {
    throw new CredentialsError((error as Error).message);
  }
}

export function saveCredentials(path: string, value: Credentials): void {
  writePrivateFile(
    path,
    `api_id = ${value.api_id}\napi_hash = "${value.api_hash}"\n`,
  );
}

export function loadForwardTarget(path: string): ForwardTarget | null {
  let text: string | null;
  try {
    text = readPrivateFile(path);
  } catch (error) {
    throw new ConfigError((error as Error).message);
  }
  if (text === null) return null;
  const data = readToml(text, path);
  if (
    Object.keys(data).sort().join(",") !== "token,url" ||
    typeof data.url !== "string" ||
    typeof data.token !== "string"
  ) {
    throw new ConfigError(
      `${path} must contain exactly a url and a token, as text`,
    );
  }
  try {
    const url = new URL(data.url);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !url.hostname ||
      url.port === "0"
    )
      throw new Error();
  } catch {
    throw new ConfigError(
      `${path}: url must be an http:// or https:// address`,
    );
  }
  if (!/^[A-Za-z0-9._~+/=-]+$/.test(data.token)) {
    throw new ConfigError(`${path}: token must be letters, digits and ._~+/=-`);
  }
  return { url: data.url, token: data.token };
}
