import { createHash } from "node:crypto";
import { readdirSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import type { Json, JsonObject, Post } from "./model.ts";
import { errorName } from "./model.ts";
import { isMissing } from "./private-files.ts";

export interface Listener {
  name: string;
  chat_id: number;
  listen(post: Post): Iterable<JsonObject>;
}
export interface Match {
  match_id: string;
  listener: string;
  chat_id: number;
  message_id: number;
  posted_at: Date;
  edited_at: Date | null;
  data: JsonObject;
}

export async function loadListeners(directory: string): Promise<Listener[]> {
  let files: string[];
  try {
    files = readdirSync(directory);
  } catch (error) {
    if (isMissing(error)) return [];
    throw error;
  }
  const listeners: Listener[] = [];
  for (const file of files) {
    if (
      file.endsWith(".py") &&
      file !== "example.py" &&
      !files.some(
        (other) => other.replace(/\.(ts|js|mjs)$/, "") === file.slice(0, -3),
      )
    ) {
      console.warn(
        `Python listener needs a TypeScript port: ${file.slice(0, -3)}`,
      );
    }
  }
  for (const file of files.sort()) {
    if (!/\.(ts|js|mjs)$/.test(file) || /^example\./.test(file)) continue;
    const name = basename(file).replace(/\.(ts|js|mjs)$/, "");
    try {
      const module = await import(
        pathToFileURL(resolve(join(directory, file))).href
      );
      if (
        !Number.isSafeInteger(module.CHAT_ID) ||
        module.CHAT_ID === 0 ||
        typeof module.listen !== "function"
      )
        throw new TypeError("requires CHAT_ID and listen");
      listeners.push({ name, chat_id: module.CHAT_ID, listen: module.listen });
    } catch (error) {
      console.error(`listener not loaded: ${name}, ${errorName(error)}`);
    }
  }
  return listeners;
}

function validateJson(
  value: unknown,
  parents = new Set<object>(),
): asserts value is Json {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (
    typeof value !== "object" ||
    value === null ||
    parents.has(value) ||
    (!Array.isArray(value) &&
      Object.getPrototypeOf(value) !== Object.prototype &&
      Object.getPrototypeOf(value) !== null)
  )
    throw new TypeError("listeners must yield JSON objects");
  parents.add(value);
  for (const entry of Object.values(value)) validateJson(entry, parents);
  parents.delete(value);
}

// Match identities retain the Python implementation's sorted, ASCII JSON form.
export function identityJson(value: Json): string {
  if (Array.isArray(value)) return `[${value.map(identityJson).join(", ")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${identityJson(key)}: ${identityJson(value[key])}`)
      .join(", ")}}`;
  }
  return JSON.stringify(value).replace(
    /[\u007f-\uffff]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

export function hear(listeners: Listener[], post: Post): Match[] {
  return listeners
    .filter((listener) => listener.chat_id === post.chat_id)
    .flatMap((listener) => {
      try {
        const found = Array.from(listener.listen(post), (data) => {
          if (data === null || typeof data !== "object" || Array.isArray(data))
            throw new TypeError("listeners must yield objects");
          validateJson(data);
          return JSON.parse(JSON.stringify(data)) as JsonObject;
        });
        return found.map((data) => ({
          match_id: createHash("sha256")
            .update(
              identityJson([
                listener.name,
                post.chat_id,
                post.message_id,
                data,
              ]),
            )
            .digest("hex"),
          listener: listener.name,
          chat_id: post.chat_id,
          message_id: post.message_id,
          posted_at: post.posted_at,
          edited_at: post.edited_at,
          data,
        }));
      } catch (error) {
        console.error(`listener failed: ${listener.name}, ${errorName(error)}`);
        return [];
      }
    });
}
