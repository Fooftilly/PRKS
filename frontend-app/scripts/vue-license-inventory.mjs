/** Lockfile-driven package inventory for the legal notice distributed with prks-vue.js.
 *
 * The upstream copyright/license texts below the inventory remain hand-reviewed.
 * This function never tries to infer rights from the package metadata.
 */
export const INVENTORY_START = "<!-- BEGIN GENERATED PRODUCTION DEPENDENCY INVENTORY -->";
export const INVENTORY_END = "<!-- END GENERATED PRODUCTION DEPENDENCY INVENTORY -->";

// Conservatively accept only the SPDX identifiers reviewed in PRKS's current
// production dependency graph. New licenses and compound expressions require
// human review and an explicit policy update; never treat arbitrary metadata
// (e.g. "SEE LICENSE IN LICENSE") as a verified SPDX grant.
const REVIEWED_SPDX_LICENSE_IDS = new Set([
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "ISC",
  "MIT",
]);

export function renderLicenseInventory(document, packageJson, lockfile) {
  const first = document.indexOf(INVENTORY_START);
  const last = document.indexOf(INVENTORY_END);
  if (first < 0 || last <= first ||
      document.indexOf(INVENTORY_START, first + 1) !== -1 ||
      document.indexOf(INVENTORY_END, last + 1) !== -1) {
    throw new Error("Vue legal notices must contain exactly one ordered inventory marker pair");
  }
  if (!lockfile.packages || typeof lockfile.packages !== "object") {
    throw new Error("Vue package-lock.json is missing its package inventory");
  }

  const packages = Object.entries(lockfile.packages)
    .filter(([path, entry]) =>
      path.startsWith("node_modules/") && !entry.dev && !entry.optional)
    .map(([path, entry]) => {
      if (!entry.version || !entry.license) {
        throw new Error(`Cannot generate license notice for ${path}: missing version/license metadata`);
      }
      if (typeof entry.license !== "string" || !REVIEWED_SPDX_LICENSE_IDS.has(entry.license)) {
        throw new Error(`Unreviewed SPDX license metadata for ${path}: ${String(entry.license)}. Review the upstream terms and notices before accepting a new license identifier.`);
      }
      // npm lock paths can include nested node_modules installation locations.
      // Report the actual package name; the lockfile remains the provenance.
      return {
        name: path.slice(path.lastIndexOf("node_modules/") + "node_modules/".length),
        version: entry.version,
        license: entry.license,
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));

  // Direct dependencies must resolve to the root install, not a nested copy
  // which might share a name but differ in version.
  for (const [name, version] of Object.entries(packageJson.dependencies || {})) {
    if (lockfile.packages[`node_modules/${name}`]?.version !== version) {
      throw new Error(`Vue dependency ${name}@${version} is missing or mismatched in production lockfile`);
    }
  }

  const table = [
    "| Package | Version | SPDX license |",
    "| --- | --- | --- |",
    ...packages.map(({ name, version, license }) =>
      `| \`${name}\` | \`${version}\` | \`${license}\` |`),
  ].join("\n");

  return document.slice(0, first + INVENTORY_START.length) +
    "\n" + table + "\n" + document.slice(last);
}
