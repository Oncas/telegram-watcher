import type { JsonObject, Post } from "../src/model.ts";

export const CHAT_ID = -1000000000000;

export function* listen(post: Post): Iterable<JsonObject> {
  if (post.outgoing) return;
  const keyword = /\b(?<keyword>release|outage)\b/gi;
  for (const match of (post.text ?? "").matchAll(keyword)) {
    yield { keyword: match.groups!.keyword.toLowerCase() };
  }
}
