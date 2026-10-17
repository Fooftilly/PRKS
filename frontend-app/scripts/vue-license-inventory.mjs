/** Lockfile-driven package inventory for the legal notice distributed with prks-vue.js.
 *
 * The upstream copyright/license texts below the inventory remain hand-reviewed.
 * This function never tries to infer rights from the package metadata.
 */
export const INVENTORY_START = "<!-- BEGIN GENERATED PRODUCTION DEPENDENCY INVENTORY -->";
export const INVENTORY_END = "<!-- END GENERATED PRODUCTION DEPENDENCY INVENTORY -->";

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
      return { name: path.slice("node_modules/".length), version: entry.version, license: entry.license };
    })
    .sort((a, b) => a.name.localeCompare(b.name));

  const versions = new Map(packages.map(({ name, version }) => [name, version]));
  for (const [name, version] of Object.entries(packageJson.dependencies || {})) {
    if (versions.get(name) !== version) {
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
