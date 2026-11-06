import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import AdmZip from 'adm-zip'

export async function stagePersonalCore(root, target) {
  if (target !== 'x86_64-pc-windows-msvc') {
    throw new Error('Personal builds support Windows amd64 only')
  }
  const lock = JSON.parse(await readFile(path.join(root, '.personal/core-lock.json'), 'utf8'))
  const response = await fetch(lock.url)
  if (!response.ok) throw new Error(`Core download failed: ${response.status}`)
  const archive = Buffer.from(await response.arrayBuffer())
  if (createHash('sha256').update(archive).digest('hex') !== lock.sha256) {
    throw new Error('Pinned core archive checksum mismatch')
  }
  const entries = new AdmZip(archive).getEntries().filter((entry) => !entry.isDirectory)
  if (entries.length !== 1 || path.basename(entries[0].entryName) !== 'mihomo.exe') {
    throw new Error('Expected one mihomo.exe in the pinned core archive')
  }
  const binary = entries[0].getData()
  const pe = binary.readUInt32LE(0x3c)
  if (binary.toString('ascii', pe, pe + 4) !== 'PE\0\0' || binary.readUInt16LE(pe + 4) !== 0x8664) {
    throw new Error('Core is not a Windows amd64 executable')
  }
  const directory = path.join(root, 'src-tauri/sidecar')
  await mkdir(directory, { recursive: true })
  for (const name of ['verge-mihomo', 'verge-mihomo-alpha']) {
    const file = path.join(directory, `${name}-${target}.exe`)
    await writeFile(file, binary)
    if (!execFileSync(file, ['-v'], { encoding: 'utf8' }).includes(`Mihomo Meta ${lock.tag} `)) {
      throw new Error('Bundled core does not report the pinned version')
    }
  }
}
