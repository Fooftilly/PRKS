#!/usr/bin/env node
/**
 * Portable Claude Code secrets-scan hook launcher.
 *
 * Invokes `sonar hook <name>` when the SonarQube CLI is on PATH; otherwise
 * exits 0 so contributors without Sonar are not blocked.
 *
 * Usage (Claude settings exec form):
 *   node …/run-hook.mjs claude-pre-tool-use
 *   node …/run-hook.mjs claude-prompt-submit
 */
import { spawnSync } from "node:child_process";

const ALLOWED = new Set(["claude-pre-tool-use", "claude-prompt-submit"]);
const hook = process.argv[2];

if (!ALLOWED.has(hook)) {
  process.exit(0);
}

const result = spawnSync("sonar", ["hook", hook], {
  stdio: "inherit",
  shell: false,
  windowsHide: true,
});

if (result.error) {
  if (result.error.code === "ENOENT") {
    process.exit(0);
  }
  process.stderr.write(
    `sonar-secrets hook: failed to spawn sonar (${result.error.code || "error"})\n`,
  );
  process.exit(1);
}

process.exit(typeof result.status === "number" ? result.status : 1);
