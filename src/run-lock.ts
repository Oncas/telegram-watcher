import { spawnSync } from "node:child_process";
import { closeSync, openSync } from "node:fs";

export class AlreadyRunning extends Error {}

export function acquireRunLock(path: string): () => void {
  const descriptor = openSync(path, "a", 0o600);
  const result = spawnSync("flock", ["-n", "-E", "75", "3"], {
    stdio: ["ignore", "ignore", "pipe", descriptor],
    timeout: 10_000,
  });
  if (result.status !== 0) {
    closeSync(descriptor);
    if (result.status === 75)
      throw new AlreadyRunning("another run holds the lock");
    throw new Error(
      "cannot acquire run lock; flock from util-linux is required",
    );
  }
  // flock and this process share the open file description. Keeping this
  // descriptor open holds the kernel lock, including across container runs.
  return () => closeSync(descriptor);
}
