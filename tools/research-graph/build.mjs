import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { createHash } from "crypto";
import { spawnSync } from "child_process";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import { resolvePython3 } from "../resolve-python3.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(root, "..", "..");
const pkg = JSON.parse(
  readFileSync(join(root, "node_modules", "cytoscape", "package.json"), "utf8")
);
const srcJs = join(root, "node_modules", "cytoscape", "dist", "cytoscape.min.js");
const srcLicense = join(root, "node_modules", "cytoscape", "LICENSE");
const destDir = join(repoRoot, "frontend", "vendor", "cytoscape");

mkdirSync(destDir, { recursive: true });
copyFileSync(srcJs, join(destDir, "cytoscape.min.js"));
copyFileSync(srcLicense, join(destDir, "LICENSE"));

const buf = readFileSync(join(destDir, "cytoscape.min.js"));
const sha256 = createHash("sha256").update(buf).digest("hex");
const version = [
  pkg.version,
  "source: https://github.com/cytoscape/cytoscape.js",
  "npm: cytoscape@" + pkg.version,
  "asset: node_modules/cytoscape/dist/cytoscape.min.js",
  "sha256: " + sha256,
  "license: " + (pkg.license || "MIT"),
].join("\n");
writeFileSync(join(destDir, "VERSION"), version + "\n");

console.log("vendored cytoscape " + pkg.version + " sha256=" + sha256);

const gate = spawnSync(
  resolvePython3(),
  [join(repoRoot, "scripts", "dependency_gate.py"), "--write-manifest"],
  { cwd: repoRoot, stdio: "inherit" }
);
if (gate.status !== 0) {
  process.exit(gate.status || 1);
}
