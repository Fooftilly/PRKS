/**
 * Copy approved browser dist + licenses into frontend/vendor/.
 * Writes deterministic VERSION files (no fetched dates) and refreshes
 * DEPENDENCY-MANIFEST.json via scripts/dependency_gate.py --write-manifest.
 */
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { createHash } from "crypto";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import { spawnSync } from "child_process";
import { resolvePython } from "../resolve-python3.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(root, "..", "..");
const vendorRoot = join(repoRoot, "frontend", "vendor");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function copyOne(src, dest) {
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(src, dest);
}

function writeVersion(dir, lines) {
  writeFileSync(join(dir, "VERSION"), lines.filter(Boolean).join("\n") + "\n");
}

function tryCopyLicense(pkgName, destDir) {
  const dir = join(root, "node_modules", ...pkgName.split("/"));
  for (const name of ["LICENSE", "LICENSE.md", "LICENSE.txt", "license"]) {
    try {
      copyFileSync(join(dir, name), join(destDir, "LICENSE"));
      return;
    } catch {
      /* next */
    }
  }
}

// --- DOMPurify ---
{
  const version = pkg.dependencies.dompurify;
  const dest = join(vendorRoot, "dompurify");
  const srcJs = join(root, "node_modules", "dompurify", "dist", "purify.min.js");
  copyOne(srcJs, join(dest, "purify.min.js"));
  tryCopyLicense("dompurify", dest);
  const hash = sha256(readFileSync(join(dest, "purify.min.js")));
  writeVersion(dest, [
    version,
    "source: https://github.com/cure53/DOMPurify",
    "npm: dompurify@" + version,
    "asset: node_modules/dompurify/dist/purify.min.js",
    "sha256: " + hash,
    "license: MPL-2.0 OR Apache-2.0",
  ]);
  console.log("vendored dompurify", version, hash);
}

// --- EasyMDE ---
{
  const version = pkg.dependencies.easymde;
  const dest = join(vendorRoot, "easymde");
  const js = join(root, "node_modules", "easymde", "dist", "easymde.min.js");
  const css = join(root, "node_modules", "easymde", "dist", "easymde.min.css");
  copyOne(js, join(dest, "easymde.min.js"));
  copyOne(css, join(dest, "easymde.min.css"));
  tryCopyLicense("easymde", dest);
  const jsHash = sha256(readFileSync(join(dest, "easymde.min.js")));
  const cssHash = sha256(readFileSync(join(dest, "easymde.min.css")));
  writeVersion(dest, [
    version,
    "source: https://github.com/Ionaru/easy-markdown-editor",
    "npm: easymde@" + version,
    "asset: node_modules/easymde/dist/easymde.min.js",
    "css: node_modules/easymde/dist/easymde.min.css",
    "sha256-js: " + jsHash,
    "sha256-css: " + cssHash,
    "license: MIT",
  ]);
  console.log("vendored easymde", version);
}

// --- CodeMirror 5 ---
{
  const version = pkg.dependencies.codemirror;
  const dest = join(vendorRoot, "codemirror");
  const lib = join(root, "node_modules", "codemirror", "lib", "codemirror.js");
  const hintJs = join(root, "node_modules", "codemirror", "addon", "hint", "show-hint.js");
  const hintCss = join(root, "node_modules", "codemirror", "addon", "hint", "show-hint.css");
  copyOne(lib, join(dest, "codemirror.js"));
  copyOne(hintJs, join(dest, "show-hint.js"));
  copyOne(hintCss, join(dest, "show-hint.css"));
  tryCopyLicense("codemirror", dest);
  writeVersion(dest, [
    version,
    "source: https://github.com/codemirror/codemirror5",
    "npm: codemirror@" + version,
    "lib: node_modules/codemirror/lib/codemirror.js",
    "show-hint-js: node_modules/codemirror/addon/hint/show-hint.js",
    "show-hint-css: node_modules/codemirror/addon/hint/show-hint.css",
    "sha256-lib: " + sha256(readFileSync(join(dest, "codemirror.js"))),
    "sha256-show-hint-js: " + sha256(readFileSync(join(dest, "show-hint.js"))),
    "sha256-show-hint-css: " + sha256(readFileSync(join(dest, "show-hint.css"))),
    "license: MIT",
  ]);
  console.log("vendored codemirror", version);
}

// --- Lucide ---
{
  const version = pkg.dependencies.lucide;
  const dest = join(vendorRoot, "lucide");
  const srcJs = join(root, "node_modules", "lucide", "dist", "umd", "lucide.min.js");
  copyOne(srcJs, join(dest, "lucide.min.js"));
  tryCopyLicense("lucide", dest);
  const hash = sha256(readFileSync(join(dest, "lucide.min.js")));
  writeVersion(dest, [
    version,
    "source: https://github.com/lucide-icons/lucide",
    "npm: lucide@" + version,
    "asset: node_modules/lucide/dist/umd/lucide.min.js",
    "sha256: " + hash,
    "license: ISC",
  ]);
  console.log("vendored lucide", version, hash);
}

const py = resolvePython();
const gate = spawnSync(
  py.executable,
  [
    ...py.args,
    join(repoRoot, "scripts", "dependency_gate.py"),
    "--write-manifest",
  ],
  { cwd: repoRoot, stdio: "inherit" }
);
if (gate.status !== 0) {
  process.exit(gate.status || 1);
}
