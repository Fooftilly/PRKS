import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const patchesDir = join(root, 'patches');
const EXPECTED = '2.15.0';

function sha256(buf) {
    return createHash('sha256').update(buf).digest('hex');
}

function parsePatch(text) {
    const lines = text.replace(/\r\n/g, '\n').split('\n');
    let pkg = '';
    let file = '';
    let version = '';
    const hunks = [];
    let i = 0;
    while (i < lines.length) {
        const line = lines[i];
        if (line.startsWith('# package:')) pkg = line.slice('# package:'.length).trim();
        if (line.startsWith('# file:')) file = line.slice('# file:'.length).trim();
        if (line.startsWith('# version:')) version = line.slice('# version:'.length).trim();
        if (line.startsWith('@@')) {
            const hunk = { old: [], neu: [] };
            i += 1;
            while (
                i < lines.length &&
                !lines[i].startsWith('@@') &&
                !lines[i].startsWith('diff ')
            ) {
                const l = lines[i];
                if (l.startsWith('+')) hunk.neu.push(l.slice(1));
                else if (l.startsWith('-')) hunk.old.push(l.slice(1));
                else if (l.startsWith('\\')) {
                    i += 1;
                    continue;
                } else if (l === '') {
                    break;
                } else {
                    const ctx = l.startsWith(' ') ? l.slice(1) : l;
                    hunk.old.push(ctx);
                    hunk.neu.push(ctx);
                }
                i += 1;
            }
            while (hunk.old.length && hunk.old[hunk.old.length - 1] === '') hunk.old.pop();
            while (hunk.neu.length && hunk.neu[hunk.neu.length - 1] === '') hunk.neu.pop();
            if (!hunk.old.length && !hunk.neu.length) {
                throw new Error('malformed patch: empty hunk');
            }
            hunks.push(hunk);
            continue;
        }
        i += 1;
    }
    if (!pkg || !file || !hunks.length) {
        throw new Error('malformed patch: need # package, # file, and at least one hunk');
    }
    if (version && version !== EXPECTED) {
        throw new Error(`patch targets ${version}, expected ${EXPECTED}`);
    }
    return { pkg, file, hunks };
}

function applyHunks(source, hunks) {
    let text = source.replace(/\r\n/g, '\n');
    if (!text.endsWith('\n')) text += '\n';
    let applied = 0;
    let already = 0;
    for (const hunk of hunks) {
        const oldBlock = hunk.old.join('\n');
        const newBlock = hunk.neu.join('\n');
        if (text.includes(newBlock) && !text.includes(oldBlock)) {
            already += 1;
            continue;
        }
        if (!text.includes(oldBlock)) {
            throw new Error(
                'hunk does not match file (not vanilla 2.15.0 and not already applied)',
            );
        }
        text = text.replace(oldBlock, newBlock);
        applied += 1;
    }
    return { text, applied, already };
}

export function applyEmbedpdfPatches() {
    if (!existsSync(patchesDir)) {
        throw new Error('missing tools/pdf-viewer/patches');
    }
    const names = readdirSync(patchesDir)
        .filter((n) => n.endsWith('.patch'))
        .sort();
    if (!names.length) {
        throw new Error('no .patch files in tools/pdf-viewer/patches');
    }
    const records = [];
    for (const name of names) {
        const raw = readFileSync(join(patchesDir, name));
        const parsed = parsePatch(raw.toString('utf8'));
        const pkgDir = join(root, 'node_modules', ...parsed.pkg.split('/'));
        const pkgJsonPath = join(pkgDir, 'package.json');
        if (!existsSync(pkgJsonPath)) {
            throw new Error(`package not installed: ${parsed.pkg}`);
        }
        const ver = JSON.parse(readFileSync(pkgJsonPath, 'utf8')).version;
        if (ver !== EXPECTED) {
            throw new Error(`${parsed.pkg} is ${ver}, expected ${EXPECTED}`);
        }
        const target = join(pkgDir, parsed.file);
        if (!existsSync(target)) {
            throw new Error(`missing ${parsed.pkg}/${parsed.file}`);
        }
        const before = readFileSync(target, 'utf8');
        const { text, applied, already } = applyHunks(before, parsed.hunks);
        if (applied) writeFileSync(target, text);
        records.push({
            package: parsed.pkg,
            version: EXPECTED,
            patch: name,
            sha256: sha256(raw),
            applied,
            already,
        });
    }
    return records;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
    const records = applyEmbedpdfPatches();
    for (const r of records) {
        console.log(
            `${r.package}: applied ${r.applied} hunk(s), already ${r.already} (${r.patch})`,
        );
    }
}
