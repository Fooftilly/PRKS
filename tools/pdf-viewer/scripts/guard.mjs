import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const BANNED = ['@embedpdf/snippet', '@embedpdf/react-pdf-viewer'];

function walkNodeModules(dir, acc = []) {
    if (!existsSync(dir)) return acc;
    for (const name of readdirSync(dir)) {
        if (name === '.bin') continue;
        const p = join(dir, name);
        let st;
        try {
            st = statSync(p);
        } catch {
            continue;
        }
        if (!st.isDirectory()) continue;
        if (name.startsWith('@')) {
            walkNodeModules(p, acc);
            continue;
        }
        const pkg = join(p, 'package.json');
        if (existsSync(pkg)) {
            try {
                acc.push(JSON.parse(readFileSync(pkg, 'utf8')).name);
            } catch {
                /* skip */
            }
        }
        const nested = join(p, 'node_modules');
        if (existsSync(nested)) walkNodeModules(nested, acc);
    }
    return acc;
}

export function runGuards(root) {
    const ls = execFileSync('npm', ['ls', 'react', 'react-dom', '--all', '--json'], {
        cwd: root,
        encoding: 'utf8',
    });
    const tree = JSON.parse(ls);
    const reactVersions = new Set();
    const reactDomVersions = new Set();
    function walk(node) {
        if (!node || typeof node !== 'object') return;
        if (node.dependencies) {
            for (const [name, child] of Object.entries(node.dependencies)) {
                if (name === 'react' && child.version) reactVersions.add(child.version);
                if (name === 'react-dom' && child.version) reactDomVersions.add(child.version);
                walk(child);
            }
        }
    }
    walk(tree);
    if (tree.dependencies?.react?.version) reactVersions.add(tree.dependencies.react.version);
    if (tree.dependencies?.['react-dom']?.version) {
        reactDomVersions.add(tree.dependencies['react-dom'].version);
    }
    if (reactVersions.size !== 1) {
        throw new Error('expected one React version, got ' + [...reactVersions].join(','));
    }
    if (reactDomVersions.size !== 1) {
        throw new Error('expected one ReactDOM version, got ' + [...reactDomVersions].join(','));
    }
    const reactV = [...reactVersions][0];
    const reactDomV = [...reactDomVersions][0];
    if (reactV !== reactDomV) {
        throw new Error(`react ${reactV} does not match react-dom ${reactDomV}`);
    }

    const names = walkNodeModules(join(root, 'node_modules'));
    for (const banned of BANNED) {
        if (names.includes(banned)) {
            throw new Error(banned + ' present in installed tree');
        }
    }
    let lsBanned = '';
    try {
        lsBanned = execFileSync('npm', ['ls', ...BANNED, '--all'], {
            cwd: root,
            encoding: 'utf8',
        });
    } catch (err) {
        lsBanned = String(err.stdout || '');
    }
    if (lsBanned.includes('@embedpdf/snippet@') || lsBanned.includes('@embedpdf/react-pdf-viewer@')) {
        throw new Error('banned EmbedPDF package in npm ls');
    }
}

if (import.meta.url === `file://${process.argv[1]}`) {
    runGuards(process.cwd());
    console.log('guards ok');
}
