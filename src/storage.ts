import { closeSync, openSync, readFileSync, statSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import type { SQLInputValue, SQLOutputValue } from "node:sqlite";
import type { HaltReason, Post } from "./model.ts";
import { timestamp } from "./model.ts";
import type { Match } from "./listeners.ts";

export class StorageError extends Error {}
type Row = Record<string, SQLOutputValue>;

export function createDatabase(path: string): void {
  try {
    closeSync(openSync(path, "wx", 0o600));
  } catch {
    throw new StorageError(
      `cannot create ${path}: file exists or directory is unavailable`,
    );
  }
  const db = new DatabaseSync(path);
  try {
    db.exec("PRAGMA journal_mode = WAL");
    db.exec(readFileSync(new URL("./schema.sql", import.meta.url), "utf8"));
  } finally {
    db.close();
  }
}

export class Storage {
  readonly db: DatabaseSync;
  constructor(path: string) {
    try {
      statSync(path);
      this.db = new DatabaseSync(path);
    } catch {
      throw new StorageError(
        `cannot open ${path}; run telegram-watcher init first`,
      );
    }
    try {
      if (this.one("PRAGMA user_version")?.user_version !== 2) {
        throw new StorageError(
          `${path} has an unsupported schema version, expected 2`,
        );
      }
      this.db.exec("PRAGMA busy_timeout = 5000");
    } catch (error) {
      this.db.close();
      if (error instanceof StorageError) throw error;
      throw new StorageError(`${path} is not a watcher database`);
    }
  }

  close(): void {
    this.db.close();
  }
  run(sql: string, ...values: SQLInputValue[]): void {
    this.db.prepare(sql).run(...values);
  }
  one(sql: string, ...values: SQLInputValue[]): Row | undefined {
    return this.db.prepare(sql).get(...values);
  }
  all(sql: string, ...values: SQLInputValue[]): Row[] {
    return this.db.prepare(sql).all(...values);
  }

  transaction<T>(action: () => T): T {
    this.db.exec("BEGIN");
    try {
      const result = action();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  checkpoint(chatId: number): number | null {
    return (
      (this.one(
        "SELECT last_message_id FROM checkpoints WHERE chat_id = ?",
        chatId,
      )?.last_message_id as number | undefined) ?? null
    );
  }
  advance(chatId: number, messageId: number): void {
    this.run(
      "INSERT INTO checkpoints (chat_id, last_message_id) VALUES (?, ?) ON CONFLICT (chat_id) DO UPDATE SET last_message_id = max(last_message_id, excluded.last_message_id)",
      chatId,
      messageId,
    );
  }

  savePost(post: Post, fetchedAt: Date): boolean {
    this.run(
      "INSERT INTO posts (chat_id, message_id, posted_at, album_id) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
      post.chat_id,
      post.message_id,
      timestamp(post.posted_at),
      post.album_id,
    );
    const latest = this.one(
      "SELECT version, edited_at FROM post_versions WHERE chat_id = ? AND message_id = ? ORDER BY version DESC LIMIT 1",
      post.chat_id,
      post.message_id,
    );
    if (latest && latest.edited_at === timestamp(post.edited_at)) return false;
    this.run(
      "INSERT INTO post_versions (chat_id, message_id, version, edited_at, media_kind, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
      post.chat_id,
      post.message_id,
      latest ? Number(latest.version) + 1 : 1,
      timestamp(post.edited_at),
      post.media_kind,
      timestamp(fetchedAt),
    );
    return true;
  }
  recentIds(chatId: number, since: Date): number[] {
    return this.all(
      "SELECT message_id FROM posts WHERE chat_id = ? AND posted_at >= ? AND deleted_at IS NULL ORDER BY message_id",
      chatId,
      timestamp(since),
    ).map((row) => Number(row.message_id));
  }
  markDeleted(chatId: number, messageIds: number[], noticedAt: Date): void {
    for (const id of messageIds) {
      this.run(
        "UPDATE posts SET deleted_at = ? WHERE chat_id = ? AND message_id = ? AND deleted_at IS NULL",
        timestamp(noticedAt),
        chatId,
        id,
      );
    }
  }

  haltReason(): HaltReason | null {
    return this.one("SELECT halt_reason FROM account_state")!
      .halt_reason as HaltReason | null;
  }
  halt(reason: HaltReason): void {
    this.run("UPDATE account_state SET halt_reason = ?", reason);
  }
  clearHalt(): void {
    this.run("UPDATE account_state SET halt_reason = NULL");
  }
  retryAt(): Date | null {
    const value = this.one("SELECT retry_at FROM account_state")!.retry_at;
    return value === null ? null : new Date(value as string);
  }
  deferUntil(value: Date): void {
    this.run("UPDATE account_state SET retry_at = ?", timestamp(value));
  }

  addMatch(match: Match, foundAt: Date): void {
    this.run(
      "INSERT INTO listener_matches (match_id, listener, chat_id, message_id, posted_at, edited_at, data, found_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
      match.match_id,
      match.listener,
      match.chat_id,
      match.message_id,
      timestamp(match.posted_at),
      timestamp(match.edited_at),
      JSON.stringify(match.data),
      timestamp(foundAt),
    );
  }
  pendingMatches(column: "notified_at" | "forwarded_at"): Match[] {
    return this.all(
      `SELECT match_id, listener, chat_id, message_id, posted_at, edited_at, data FROM listener_matches WHERE ${column} IS NULL ORDER BY rowid`,
    ).map((row) => ({
      match_id: row.match_id as string,
      listener: row.listener as string,
      chat_id: row.chat_id as number,
      message_id: row.message_id as number,
      posted_at: new Date(row.posted_at as string),
      edited_at:
        row.edited_at === null ? null : new Date(row.edited_at as string),
      data: JSON.parse(row.data as string),
    }));
  }
  markDelivered(
    match: Match,
    column: "notified_at" | "forwarded_at",
    at: Date,
  ): void {
    this.run(
      `UPDATE listener_matches SET ${column} = ? WHERE match_id = ?`,
      timestamp(at),
      match.match_id,
    );
  }

  alertActive(kind: string, subject: string): boolean {
    return (
      this.one(
        "SELECT 1 FROM active_alerts WHERE kind = ? AND subject = ?",
        kind,
        subject,
      ) !== undefined
    );
  }
  activateAlert(kind: string, subject: string): void {
    this.run(
      "INSERT INTO active_alerts (kind, subject) VALUES (?, ?)",
      kind,
      subject,
    );
  }
  resolveAlert(kind: string, subject?: string): void {
    if (subject === undefined)
      this.run("DELETE FROM active_alerts WHERE kind = ?", kind);
    else
      this.run(
        "DELETE FROM active_alerts WHERE kind = ? AND subject = ?",
        kind,
        subject,
      );
  }
}
