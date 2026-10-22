#!/usr/bin/env node
import { existsSync, mkdirSync, realpathSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  credentials,
  loadConfig,
  loadCredentials,
  loadForwardTarget,
  saveConfig,
  saveCredentials,
} from "./config.ts";
import { DesktopNotifier, HttpForwarder } from "./delivery.ts";
import { ImageStore } from "./image-store.ts";
import { loadListeners } from "./listeners.ts";
import type { Notifier, WatchedChat } from "./model.ts";
import { errorName, NotificationFailed } from "./model.ts";
import { readPrivateFile, writePrivateFile } from "./private-files.ts";
import { InputCancelled, TerminalPrompt } from "./prompt.ts";
import { acquireRunLock, AlreadyRunning } from "./run-lock.ts";
import { createDatabase, Storage } from "./storage.ts";
import { listChats, signIn, TelegramConnection } from "./telegram.ts";
import type { Prompt } from "./telegram.ts";
import { runWatcher, watchLive } from "./watcher.ts";

const commands = {
  init: "create var/ with an empty database",
  login: "save the API credentials and log in to Telegram",
  "pick-chats": "choose the chats to watch, once",
  run: "poll the watched chats once",
  listen: "poll the watched chats whenever a new post arrives",
  "clear-halt": "resume runs after fixing what halted them",
};

export function selectChats(
  chats: WatchedChat[],
  answer: string,
): WatchedChat[] {
  const numbers = answer
    .trim()
    .split(/[\s,]+/)
    .filter(Boolean);
  if (!numbers.length) throw new Error("no chats selected");
  if (
    numbers.some(
      (value) =>
        !/^[0-9]+$/.test(value) ||
        Number(value) < 1 ||
        Number(value) > chats.length,
    )
  )
    throw new Error(`choose numbers from 1 to ${chats.length}`);
  return Array.from(
    new Set(numbers.map(Number)),
    (number) => chats[number - 1],
  );
}
export function terminalSafe(text: string): string {
  return text.replace(/[\p{C}\p{Zl}\p{Zp}]/gu, "?");
}

async function notifyIfPossible(
  notifier: Notifier,
  text: string,
): Promise<void> {
  try {
    await notifier.notify(text);
  } catch (error) {
    if (!(error instanceof NotificationFailed)) throw error;
    console.warn("notification not delivered");
  }
}

export async function main(
  argv = process.argv.slice(2),
  {
    notifier = new DesktopNotifier(),
    prompt = new TerminalPrompt(),
  }: { notifier?: Notifier; prompt?: Prompt } = {},
): Promise<number> {
  if (argv.length === 1 && ["--help", "-h"].includes(argv[0])) {
    console.log(
      "Usage: telegram-watcher <command>\n\n" +
        Object.entries(commands)
          .map(([name, help]) => `  ${name.padEnd(12)} ${help}`)
          .join("\n"),
    );
    return 0;
  }
  if (
    argv.length === 2 &&
    Object.hasOwn(commands, argv[0]) &&
    ["--help", "-h"].includes(argv[1])
  ) {
    console.log(commands[argv[0] as keyof typeof commands]);
    return 0;
  }
  if (argv.length !== 1 || !Object.hasOwn(commands, argv[0])) {
    console.error(
      "Usage: telegram-watcher <init|login|pick-chats|run|listen|clear-halt>",
    );
    return 2;
  }
  const command = argv[0];
  try {
    if (command === "init") {
      mkdirSync("var", { mode: 0o700, recursive: true });
      createDatabase("var/watcher.db");
      console.log("Created var/watcher.db.");
      return 0;
    }
    if (command === "login") {
      if (!existsSync("var"))
        throw new Error("run telegram-watcher init first");
      let value;
      if (existsSync("var/credentials.toml"))
        value = loadCredentials("var/credentials.toml");
      else {
        console.log(
          "Enter the app credentials from my.telegram.org -> API development tools.",
        );
        const id = (await prompt.ask("api_id: ")).trim();
        if (!/^[0-9]+$/.test(id))
          throw new Error("api_id must be a positive whole number");
        value = credentials(
          Number(id),
          (await prompt.ask("api_hash (input hidden): ", true)).trim(),
        );
      }
      let session: string;
      try {
        session = await signIn(value, prompt);
      } catch (error) {
        if (error instanceof InputCancelled) throw error;
        throw new Error(`login failed: ${errorName(error)}`);
      }
      const replaced = existsSync("var/telegram.session");
      saveCredentials("var/credentials.toml", value);
      writePrivateFile("var/telegram.session", session);
      console.log("Logged in and saved var/telegram.session.");
      if (replaced)
        console.log(
          "The previous session was replaced. If it is still listed in Telegram under Settings -> Devices, terminate it there.",
        );
      console.log("If runs were halted, run telegram-watcher clear-halt.");
      return 0;
    }
    if (command === "pick-chats") {
      if (existsSync("var/config.toml"))
        throw new Error(
          "var/config.toml exists; edit it, or delete it to pick again",
        );
      const value = loadCredentials("var/credentials.toml");
      const session = readPrivateFile("var/telegram.session");
      if (session === null)
        throw new Error("not logged in; run telegram-watcher login");
      let chats: WatchedChat[];
      try {
        chats = await listChats(value, session);
      } catch (error) {
        throw new Error(`cannot list chats: ${errorName(error)}`);
      }
      if (!chats.length)
        throw new Error("the account has no chats that can be watched");
      chats.forEach((chat, index) =>
        console.log(
          `${String(index + 1).padStart(4)}. ${terminalSafe(chat.name)}`,
        ),
      );
      while (true) {
        const answer = await prompt.ask("Numbers of the chats to watch: ");
        let selected;
        try {
          selected = selectChats(chats, answer);
        } catch (error) {
          console.log((error as Error).message);
          continue;
        }
        saveConfig("var/config.toml", selected);
        console.log(`Saved ${selected.length} chats to var/config.toml.`);
        return 0;
      }
    }
    if (command === "clear-halt") {
      const storage = new Storage("var/watcher.db");
      try {
        const reason = storage.haltReason();
        storage.transaction(() => storage.clearHalt());
        console.log(
          reason ? `Cleared the halt: ${reason}.` : "Runs were not halted.",
        );
      } finally {
        storage.close();
      }
      return 0;
    }
    let release: () => void;
    try {
      release = acquireRunLock("var/run.lock");
    } catch (error) {
      if (!(error instanceof AlreadyRunning)) throw error;
      console.info(
        command === "run"
          ? "run skipped: another run is still active"
          : "cannot listen: another run or listener is active",
      );
      return command === "run" ? 0 : 1;
    }
    try {
      const chats = loadConfig("var/config.toml");
      const value = loadCredentials("var/credentials.toml");
      const target = loadForwardTarget("var/forward.toml");
      const storage = new Storage("var/watcher.db");
      const telegram = new TelegramConnection(
        value,
        "var/telegram.session",
        command === "listen" ? chats : null,
      );
      const controller = new AbortController();
      const stop = () => {
        controller.abort();
        void telegram.close().catch(() => {});
      };
      if (command === "listen") {
        process.on("SIGINT", stop);
        process.on("SIGTERM", stop);
      }
      try {
        const options = {
          chats,
          listeners: await loadListeners("listeners"),
          notifier,
          forwarder: target ? new HttpForwarder(target) : null,
          imageStore: new ImageStore("var/media"),
          clock: () => new Date(),
        };
        if (command === "run") {
          const report = await runWatcher(storage, {
            ...options,
            connect: () => telegram.connect(),
            disconnect: () => telegram.close(),
          });
          console.info(`run finished: ${report.outcome}`);
          return report.outcome === "failed" ? 1 : 0;
        }
        const outcome = await watchLive(
          storage,
          options,
          telegram,
          controller.signal,
        );
        console.info(`listener stopped: ${outcome}`);
        return controller.signal.aborted || outcome === "halted" ? 0 : 1;
      } finally {
        process.removeListener("SIGINT", stop);
        process.removeListener("SIGTERM", stop);
        await telegram.close();
        storage.close();
      }
    } finally {
      release();
    }
  } catch (error) {
    if (error instanceof InputCancelled) return 130;
    console.error(`error: ${(error as Error).message}`);
    if (command === "run" || command === "listen")
      await notifyIfPossible(
        notifier,
        `Telegram watcher cannot ${command}: ${errorName(error)}. See the log.`,
      );
    return 1;
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(realpathSync(process.argv[1]))).href
) {
  process.exitCode = await main();
}
