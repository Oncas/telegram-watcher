import {
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readdirSync,
  renameSync,
} from "node:fs";
import { join } from "node:path";
import type { Image } from "./model.ts";
import {
  isMissing,
  removeIfPresent,
  writePrivateFile,
} from "./private-files.ts";

const folders = ["downloaded", "rejected", "archived"];
function entries(path: string): string[] {
  try {
    return readdirSync(path);
  } catch (error) {
    if (isMissing(error)) return [];
    throw error;
  }
}

export class ImageStore {
  readonly root: string;
  constructor(root: string) {
    this.root = root;
  }
  clearUnfinished(): void {
    for (const entry of entries(this.root)) {
      if (
        entry.startsWith(".") &&
        (entry.endsWith(".part") || entry.endsWith(".tmp"))
      )
        removeIfPresent(join(this.root, entry));
    }
  }
  contains(chatId: number, messageId: number): boolean {
    const name = `${chatId}_${messageId}`;
    return folders.some((folder) =>
      entries(join(this.root, folder)).some(
        (entry) => entry === name || entry.startsWith(`${name}.`),
      ),
    );
  }
  add(chatId: number, messageId: number, image: Image): void {
    if (!/^(\.[a-zA-Z0-9]+)?$/.test(image.extension))
      throw new Error("invalid image extension");
    mkdirSync(this.root, { mode: 0o700, recursive: true });
    for (const folder of folders)
      mkdirSync(join(this.root, folder), { mode: 0o700, recursive: true });
    const name = `${chatId}_${messageId}${image.extension}`;
    const temporary = join(this.root, `.${name}.part`);
    writePrivateFile(temporary, image.data);
    renameSync(temporary, join(this.root, "downloaded", name));
    const descriptor = openSync(join(this.root, "downloaded"), "r");
    try {
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
  }
}
