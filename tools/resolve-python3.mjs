/**
 * Resolve python3 without searching a mutable PATH (Sonar S4036).
 * Prefer PRKS_PYTHON when it is an absolute path; otherwise try fixed dirs.
 */
import { accessSync, constants } from "node:fs";
import { platform } from "node:os";

export function resolvePython3() {
  const override = process.env.PRKS_PYTHON;
  if (override) {
    const abs =
      override.startsWith("/") ||
      /^[A-Za-z]:[\\/]/.test(override) ||
      override.startsWith("\\\\");
    if (!abs) {
      throw new Error("PRKS_PYTHON must be an absolute path");
    }
    accessSync(override, constants.X_OK);
    return override;
  }
  const candidates =
    platform() === "win32"
      ? ["C:\\Python312\\python.exe", "C:\\Python313\\python.exe"]
      : ["/usr/bin/python3", "/usr/local/bin/python3", "/bin/python3"];
  for (const candidate of candidates) {
    try {
      accessSync(candidate, constants.X_OK);
      return candidate;
    } catch {
      /* next */
    }
  }
  throw new Error(
    "python3 not found in fixed paths; set PRKS_PYTHON to an absolute interpreter"
  );
}
