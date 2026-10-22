import { createInterface, emitKeypressEvents } from "node:readline";
import type { Key } from "node:readline";
import type { Prompt } from "./telegram.ts";

export class InputCancelled extends Error {}
export class TerminalPrompt implements Prompt {
  async ask(question: string, hidden = false): Promise<string> {
    if (!hidden) {
      const reader = createInterface({
        input: process.stdin,
        output: process.stdout,
      });
      try {
        return await new Promise<string>((resolve, reject) => {
          reader.once("SIGINT", () => reject(new InputCancelled()));
          reader.once("close", () => reject(new InputCancelled()));
          reader.question(question, resolve);
        });
      } finally {
        reader.close();
      }
    }
    if (!process.stdin.isTTY)
      throw new Error("hidden input requires an interactive terminal");
    const previousRaw = process.stdin.isRaw;
    emitKeypressEvents(process.stdin);
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdout.write(question);
    return new Promise<string>((resolve, reject) => {
      let text = "";
      const cleanup = () => {
        process.stdin.removeListener("keypress", onKey);
        process.stdin.setRawMode(previousRaw);
        process.stdin.pause();
        process.stdout.write("\n");
      };
      const onKey = (value: string, key: Key) => {
        if (key.ctrl && (key.name === "c" || key.name === "d")) {
          cleanup();
          reject(new InputCancelled());
        } else if (key.name === "return" || key.name === "enter") {
          cleanup();
          resolve(text);
        } else if (key.name === "backspace")
          text = Array.from(text).slice(0, -1).join("");
        else if (
          !key.ctrl &&
          !key.meta &&
          value &&
          !/[\x00-\x1f\x7f]/.test(value)
        )
          text += value;
      };
      process.stdin.on("keypress", onKey);
    });
  }
}
