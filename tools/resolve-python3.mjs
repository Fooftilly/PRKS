/**
 * Resolve a Python interpreter without searching a mutable PATH (Sonar S4036).
 *
 * Prefer PRKS_PYTHON when it is an absolute path. Otherwise try fixed,
 * platform-appropriate directories. On Windows, prefer `python.exe` under
 * common install roots, then the `py` launcher at C:\\Windows\\py.exe
 * (with `-3`), never a bare `python3` name.
 *
 * Returns { executable, args } where `args` are prefixed before the script
 * (e.g. ["-3"] for the Windows py launcher).
 */
import { accessSync, constants } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";

function isAbsolutePath(p) {
  return (
    p.startsWith("/") ||
    /^[A-Za-z]:[\\/]/.test(p) ||
    p.startsWith("\\\\")
  );
}

function tryAccess(candidate) {
  try {
    accessSync(candidate, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function windowsCandidates() {
  const local = join(homedir(), "AppData", "Local", "Programs", "Python");
  return [
    { executable: "C:\\Python312\\python.exe", args: [] },
    { executable: "C:\\Python313\\python.exe", args: [] },
    { executable: "C:\\Program Files\\Python312\\python.exe", args: [] },
    { executable: "C:\\Program Files\\Python313\\python.exe", args: [] },
    { executable: join(local, "Python312", "python.exe"), args: [] },
    { executable: join(local, "Python313", "python.exe"), args: [] },
    // Windows installer py launcher (fixed system path; not PATH lookup).
    { executable: "C:\\Windows\\py.exe", args: ["-3"] },
    { executable: "C:\\Windows\\System32\\py.exe", args: ["-3"] },
  ];
}

function posixCandidates() {
  return [
    { executable: "/usr/bin/python3", args: [] },
    { executable: "/usr/local/bin/python3", args: [] },
    { executable: "/bin/python3", args: [] },
  ];
}

/**
 * @returns {{ executable: string, args: string[] }}
 */
export function resolvePython() {
  const override = process.env.PRKS_PYTHON;
  if (override) {
    if (!isAbsolutePath(override)) {
      throw new Error("PRKS_PYTHON must be an absolute path");
    }
    accessSync(override, constants.X_OK);
    return { executable: override, args: [] };
  }

  const candidates =
    platform() === "win32" ? windowsCandidates() : posixCandidates();
  for (const candidate of candidates) {
    if (tryAccess(candidate.executable)) {
      return {
        executable: candidate.executable,
        args: candidate.args.slice(),
      };
    }
  }

  throw new Error(
    platform() === "win32"
      ? "python not found in fixed Windows paths; set PRKS_PYTHON to an absolute interpreter (python.exe or py.exe)"
      : "python3 not found in fixed paths; set PRKS_PYTHON to an absolute interpreter"
  );
}

/**
 * Back-compat: absolute executable only (no launcher prefix args).
 * Prefer resolvePython() when spawning scripts.
 */
export function resolvePython3() {
  const resolved = resolvePython();
  if (resolved.args.length) {
    // Callers that ignore prefix args cannot use the py launcher safely.
    throw new Error(
      "resolved Windows py launcher requires prefix args; use resolvePython() instead of resolvePython3()"
    );
  }
  return resolved.executable;
}
