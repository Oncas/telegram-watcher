import { createHash } from "node:crypto";
import { readdirSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { z } from "zod";
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

const listenerModule = z.object({
  CHAT_ID: z.int().refine((id) => id !== 0, "CHAT_ID must not be 0"),
  listen: z.custom<Listener["listen"]>(
    (value) => typeof value === "function",
    "listen must be a function",
  ),
});
const listenerOutput = z.custom<Iterable<unknown>>(
  (value) =>
    typeof (value as Partial<Iterable<unknown>> | null)?.[Symbol.iterator] ===
    "function",
  "listen must return an iterable, such as a generator",
);
const listenerData = z.record(z.string(), z.json());

export async function loadListeners(directory: string): Promise<Listener[]> {
  let files: string[];
  try {
    files = readdirSync(directory);
  } catch (error) {
    if (isMissing(error)) return [];
    throw error;
  }
  const listeners: Listener[] = [];
  for (const file of files.sort()) {
    if (!/\.(ts|js|mjs)$/.test(file) || /^example\./.test(file)) continue;
    const name = basename(file).replace(/\.(ts|js|mjs)$/, "");
    try {
      const module = listenerModule.parse(
        await import(pathToFileURL(resolve(join(directory, file))).href),
      );
      listeners.push({ name, chat_id: module.CHAT_ID, listen: module.listen });
    } catch (error) {
      console.error(`listener not loaded: ${name}, ${errorName(error)}`);
    }
  }
  return listeners;
}

// Match IDs hash this exact form, so changing it changes the ID of every match.
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
        const found = Array.from(
          listenerOutput.parse(listener.listen(post)),
          (data) => {
            listenerData.parse(data);
            // The copy also rejects cycles, which the schema lets through.
            return JSON.parse(JSON.stringify(data)) as JsonObject;
          },
        );
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
