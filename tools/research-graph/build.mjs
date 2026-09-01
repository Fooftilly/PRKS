import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { createHash } from "crypto";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const root = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(
  readFileSync(join(root, "node_modules", "cytoscape", "package.json"), "utf8")
);
const srcJs = join(root, "node_modules", "cytoscape", "dist", "cytoscape.min.js");
const srcLicense = join(root, "node_modules", "cytoscape", "LICENSE");
const destDir = join(root, "..", "..", "frontend", "vendor", "cytoscape");

mkdirSync(destDir, { recursive: true });
copyFileSync(srcJs, join(destDir, "cytoscape.min.js"));
copyFileSync(srcLicense, join(destDir, "LICENSE"));

const buf = readFileSync(join(destDir, "cytoscape.min.js"));
const sha256 = createHash("sha256").update(buf).digest("hex");
const fetched = new Date().toISOString().slice(0, 10);
const version = [
  pkg.version,
  "source: https://github.com/cytoscape/cytoscape.js",
  "npm: cytoscape@" + pkg.version,
  "asset: node_modules/cytoscape/dist/cytoscape.min.js",
  "sha256: " + sha256,
  "license: " + (pkg.license || "MIT"),
  "fetched: " + fetched,
].join("\n");
writeFileSync(join(destDir, "VERSION"), version + "\n");

console.log("vendored cytoscape " + pkg.version + " sha256=" + sha256);
