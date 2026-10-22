import {
  closeSync,
  fstatSync,
  fsyncSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join } from "node:path";

export class PrivateFileError extends Error {}

export function isMissing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException)?.code === "ENOENT";
}

export function readPrivateFile(path: string): string | null {
  let descriptor: number;
  try {
    descriptor = openSync(path, "r");
  } catch (error) {
    if (isMissing(error)) return null;
    throw new PrivateFileError(`cannot read ${path}`);
  }
  try {
    if (fstatSync(descriptor).mode & 0o077) {
      throw new PrivateFileError(
        `${path} is readable by other users; run chmod 600 ${path}`,
      );
    }
    return readFileSync(descriptor, "utf8");
  } finally {
    closeSync(descriptor);
  }
}

export function removeIfPresent(path: string): void {
  try {
    unlinkSync(path);
  } catch (error) {
    if (!isMissing(error)) throw error;
  }
}

export function writePrivateFile(path: string, text: string | Buffer): void {
  const temporary = join(dirname(path), `.${basename(path)}.tmp`);
  removeIfPresent(temporary);
  const descriptor = openSync(temporary, "wx", 0o600);
  try {
    writeFileSync(descriptor, text);
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
  renameSync(temporary, path);
}
