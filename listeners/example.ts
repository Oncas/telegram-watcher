import type { JsonObject, Post } from "../src/model.ts";

export const CHAT_ID = -1000000000000;

export function* listen(post: Post): Iterable<JsonObject> {
  const signal =
    /Synthetic signal: (?<side>BUY|SELL) (?<symbol>[A-Z]+) at (?<price>[\d.]+)/g;
  for (const match of (post.text ?? "").matchAll(signal)) {
    yield { ...match.groups! };
  }
}
