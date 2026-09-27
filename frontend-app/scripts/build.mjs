/**
 * Production Vite build for the PRKS Vue bootstrap.
 * Writes frontend/vue/ (committed; served by Python) and refreshes
 * DEPENDENCY-MANIFEST.json plus sw.js DEPENDENCY_REVISION.
 * Node is a maintainer tool. Do not run this from prks_app.py or Docker startup.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePython } from "../../tools/resolve-python3.mjs";

const appRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = join(appRoot, "..");
const outDir = join(repoRoot, "frontend", "vue");
const allowedNames = new Set(["prks-vue.js", "BUILD-MANIFEST.json"]);

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const viteBin = join(appRoot, "node_modules", "vite", "bin", "vite.js");
const built = spawnSync(process.execPath, [viteBin, "build"], {
  cwd: appRoot,
  stdio: "inherit",
});
if (built.status !== 0) {
  process.exit(built.status || 1);
}

const emittedIndex = join(outDir, "index.html");
if (existsSync(emittedIndex)) {
  rmSync(emittedIndex);
}

const emittedCss = readdirSync(outDir).filter((name) => name.endsWith(".css"));
if (emittedCss.length) {
  console.error(
    "Vite left a stylesheet in frontend/vue; CSS must be inlined into prks-vue.js:",
    emittedCss.join(", "),
  );
  process.exit(1);
}

const unexpected = readdirSync(outDir).filter((name) => !allowedNames.has(name));
if (unexpected.length) {
  console.error("unexpected Vite output in frontend/vue:", unexpected.join(", "));
  process.exit(1);
}

const jsPath = join(outDir, "prks-vue.js");
if (!existsSync(jsPath)) {
  console.error("Vite did not emit frontend/vue/prks-vue.js");
  process.exit(1);
}

const pkg = JSON.parse(readFileSync(join(appRoot, "package.json"), "utf8"));
const outputSha256 = {
  "prks-vue.js": sha256(jsPath),
};

const manifest = {
  schema_version: 1,
  vue: pkg.dependencies.vue,
  tanstackVueQuery: pkg.dependencies["@tanstack/vue-query"],
  vueUseCore: pkg.dependencies["@vueuse/core"],
  vite: pkg.devDependencies.vite,
  typescript: pkg.devDependencies.typescript,
  vueTsc: pkg.devDependencies["vue-tsc"],
  vitest: pkg.devDependencies.vitest,
  outputSha256,
};
writeFileSync(join(outDir, "BUILD-MANIFEST.json"), JSON.stringify(manifest, null, 2) + "\n");

const modelBuild = spawnSync(process.execPath, [join(appRoot, "scripts/build-workspace-model.mjs")], {
  cwd: appRoot,
  stdio: "inherit",
});
if (modelBuild.status !== 0) {
  process.exit(modelBuild.status || 1);
}

const py = resolvePython();
const gate = spawnSync(
  py.executable,
  [...py.args, join(repoRoot, "scripts", "dependency_gate.py"), "--write-manifest"],
  { cwd: repoRoot, stdio: "inherit" },
);
if (gate.status !== 0) {
  process.exit(gate.status || 1);
}
