// Fingerprint of every input that shapes the bundle, written into the image as
// /build-info.json at build time. scripts/verify_deployed.py computes the same
// fingerprint from the repo and compares, so a stale web image is caught the
// way stale Python images are. The rule is mirrored there EXACTLY: these
// inputs, dot-files and dot-directories skipped, paths sorted, each file
// contributing "path\0sha256(content)\n".
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const INPUTS = [
  'index.html', 'package-lock.json', 'vite.config.ts',
  'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'src', 'public',
]

function walk(path) {
  if (statSync(path).isDirectory()) return readdirSync(path).flatMap((name) => walk(join(path, name)))
  return [path]
}

const files = INPUTS.flatMap((input) => walk(join(ROOT, input)))
  .map((file) => relative(ROOT, file).split(sep).join('/'))
  .filter((rel) => !rel.split('/').some((part) => part.startsWith('.')))
  .sort()

const digest = createHash('sha256')
for (const rel of files) {
  const fileHash = createHash('sha256').update(readFileSync(join(ROOT, rel))).digest('hex')
  digest.update(`${rel}\0${fileHash}\n`)
}
process.stdout.write(`${JSON.stringify({ source_sha256: digest.digest('hex'), files: files.length })}\n`)
