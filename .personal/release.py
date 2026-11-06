"""Publish one Windows amd64 installer from stable upstream and a pinned personal core."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.personal/work'
SOURCE = WORK / 'source'
DIST = ROOT / '.personal/dist'
REPO = 'Chen017/clash-verge-rev'
UPSTREAM = 'clash-verge-rev/clash-verge-rev'
CORE = 'Chen017/mihomo'


def run(*args, cwd=ROOT, capture=False):
    return subprocess.check_output(args, cwd=cwd, text=True).strip() if capture else subprocess.check_call(args, cwd=cwd)


def api(path, missing=False):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True)
    if result.returncode:
        if missing and '404' in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def read_url(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def outputs(**values):
    if filename := os.environ.get('GITHUB_OUTPUT'):
        with open(filename, 'a', encoding='utf-8') as stream:
            for key, value in values.items():
                stream.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')


def next_build(refs):
    return max([0, *[int(match[1]) for ref in refs
                    if (match := re.fullmatch(r'refs/tags/v\d+\.\d+\.\d+-personal\.(\d+)', ref['ref']))]]) + 1


def prepare():
    WORK.mkdir(parents=True, exist_ok=True)
    upstream = api(f'repos/{UPSTREAM}/releases/latest')
    tag = upstream['tag_name']
    if upstream['draft'] or upstream['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise RuntimeError('Expected an official stable app release')
    run('git', 'fetch', '--no-tags', f'https://github.com/{UPSTREAM}.git', f'refs/tags/{tag}')
    revision = run('git', 'rev-parse', 'FETCH_HEAD^{commit}', capture=True)
    core = api(f'repos/{CORE}/releases/latest')
    if core['draft'] or core['prerelease']:
        raise RuntimeError('Expected a published personal core')
    asset_name = f'mihomo-windows-amd64-{core["tag_name"]}.zip'
    asset = next(a for a in core['assets'] if a['name'] == asset_name)
    checksums = read_url(next(a['browser_download_url'] for a in core['assets'] if a['name'] == 'checksums.txt')).decode()
    checksum = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == asset_name)
    if asset.get('digest') != f'sha256:{checksum}':
        raise RuntimeError('Core release checksum differs from GitHub asset digest')
    lock = dict(tag=core['tag_name'], url=asset['browser_download_url'], sha256=checksum,
                provenance=json.loads(read_url(next(a['browser_download_url'] for a in core['assets'] if a['name'] == 'provenance.json'))))
    files = sorted([*ROOT.glob('.personal/*.py'), *ROOT.glob('.personal/*.mjs'),
                    *ROOT.glob('.personal/*.patch'), *ROOT.glob('.github/workflows/personal*.yml')])
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + path.read_bytes())
    identity = dict(upstream_commit=revision, core_tag=lock['tag'], core_sha256=checksum, inputs_sha256=digest.hexdigest())
    previous = api(f'repos/{REPO}/releases/latest', missing=True)
    if previous and any(a['name'] == 'manifest.json' for a in previous['assets']):
        last = json.loads(read_url(next(a['browser_download_url'] for a in previous['assets'] if a['name'] == 'manifest.json')))
        if all(last.get(key) == value for key, value in identity.items()):
            outputs(needed=False)
            print('Stable app, personal core and maintenance inputs unchanged; no rebuild required.')
            return
    number = next_build(api(f'repos/{REPO}/git/matching-refs/tags/v'))
    if SOURCE.exists():
        raise RuntimeError('Expected a fresh checkout; existing build source is preserved')
    run('git', 'worktree', 'add', '--detach', str(SOURCE), revision)
    patch = str(ROOT / '.personal/app.patch')
    if subprocess.run(['git', 'apply', '--check', patch], cwd=SOURCE, capture_output=True).returncode == 0:
        run('git', 'apply', patch, cwd=SOURCE)
    elif subprocess.run(['git', 'apply', '--reverse', '--check', patch], cwd=SOURCE, capture_output=True).returncode:
        run('git', 'apply', '--3way', patch, cwd=SOURCE)
    shutil.copytree(ROOT / '.personal', SOURCE / '.personal', ignore=shutil.ignore_patterns('work', 'dist', '__pycache__'))
    write_json(SOURCE / '.personal/core-lock.json', lock)
    version = f'{tag[1:]}-personal.{number}'
    for name in ['package.json', 'src-tauri/tauri.conf.json']:
        path = SOURCE / name
        content = path.read_text()
        path.write_text(re.sub(r'"version": "[^"]+"', f'"version": "{version}"', content, count=1), encoding='utf-8')
    path = SOURCE / 'src-tauri/Cargo.toml'
    path.write_text(re.sub(r'^version = "[^"]+"', f'version = "{version}"', path.read_text(), count=1, flags=re.M), encoding='utf-8')
    toolchain = tomllib.loads((SOURCE / 'rust-toolchain.toml').read_text())['toolchain']['channel']
    versions = dict(line.split() for line in (SOURCE / '.tool-versions').read_text().splitlines() if line.strip())
    node = versions['nodejs']
    plan = dict(**identity, version=version, tag=f'v{version}', build_number=number,
                upstream_tag=tag, upstream_repository=UPSTREAM, core=lock,
                maintenance_commit=run('git', 'rev-parse', 'HEAD', capture=True), rust_version=toolchain, node_version=node)
    write_json(WORK / 'plan.json', plan)
    outputs(needed=True, rust=toolchain, node=node)
    print(f'Prepared {plan["tag"]}; Windows amd64 only; core {lock["tag"]}.')


def package():
    plan = json.loads((WORK / 'plan.json').read_text())
    installers = list((SOURCE / 'target/x86_64-pc-windows-msvc/release/bundle/nsis').glob('*-setup.exe'))
    if len(installers) != 1:
        raise RuntimeError(f'Expected exactly one NSIS installer, got {len(installers)}')
    executable = SOURCE / 'target/x86_64-pc-windows-msvc/release/clash-verge.exe'
    with executable.open('rb') as stream:
        stream.seek(0x3c)
        pe = int.from_bytes(stream.read(4), 'little')
        stream.seek(pe)
        header = stream.read(6)
    if header[:4] != b'PE\0\0' or int.from_bytes(header[4:6], 'little') != 0x8664:
        raise RuntimeError('App payload is not Windows amd64')
    DIST.mkdir(exist_ok=True)
    installer = DIST / installers[0].name
    shutil.copy2(installers[0], installer)
    signature = DIST / (installer.name + '.sig')
    shutil.copy2(Path(str(installers[0]) + '.sig'), signature)
    run('git', 'add', '--all', cwd=SOURCE)
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=SOURCE).returncode:
        run('git', '-c', 'user.name=Chen017', '-c', 'user.email=github-actions[bot]@users.noreply.github.com',
            'commit', '-m', f'build: {plan["tag"]} with pinned personal Mihomo', cwd=SOURCE)
    plan['source_commit'] = run('git', 'rev-parse', 'HEAD', cwd=SOURCE, capture=True)
    config = json.loads((SOURCE / 'src-tauri/tauri.conf.json').read_text())
    verify_signature(installer, signature, config['plugins']['updater']['pubkey'], plan['version'])
    url = f'https://github.com/{REPO}/releases/download/{plan["tag"]}/{installer.name}'
    platform = dict(url=url, signature=signature.read_text().strip())
    write_json(DIST / 'update.json', dict(version=plan['version'], notes=f'Stable {plan["upstream_tag"]}; personal split-route core {plan["core_tag"]}.',
               pub_date=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
               platforms={'windows-x86_64': platform, 'windows-x86_64-nsis': platform}))
    write_json(DIST / 'manifest.json', plan)
    (DIST / 'SHA256SUMS').write_text(''.join(f'{sha(path)}  {path.name}\n' for path in sorted(DIST.iterdir()) if path.name != 'SHA256SUMS'), encoding='utf-8')


def verify_signature(installer, signature, public_key, version):
    release = api('repos/jedisct1/minisign/releases/tags/0.12')
    asset = next(a for a in release['assets'] if a['name'] == 'minisign-0.12-win64.zip')
    archive = WORK / asset['name']
    archive.write_bytes(read_url(asset['browser_download_url']))
    if sha(archive) != '37b600344e20c19314b2e82813db2bfdcc408b77b876f7727889dbd46d539479':
        raise RuntimeError('Official minisign verifier archive checksum mismatch')
    with zipfile.ZipFile(archive) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith('/x86_64/minisign.exe'))
        verifier = WORK / 'minisign.exe'
        verifier.write_bytes(bundle.read(name))
    key = WORK / 'updater.pub'
    sig = WORK / 'installer.minisig'
    key.write_bytes(base64.b64decode(public_key))
    decoded_signature = base64.b64decode(signature.read_text().strip())
    if f'version:{version}' not in decoded_signature.decode().splitlines()[2].split('\t'):
        raise RuntimeError('Installer signature is not bound to the published app version')
    sig.write_bytes(decoded_signature)
    run(str(verifier), '-Vm', str(installer), '-p', str(key), '-x', str(sig))
    corrupt = WORK / 'corrupt-installer.exe'
    shutil.copyfile(installer, corrupt)
    with corrupt.open('ab') as stream:
        stream.write(b'corrupt')
    rejected = subprocess.run([str(verifier), '-Vm', str(corrupt), '-p', str(key), '-x', str(sig)], capture_output=True).returncode
    corrupt.unlink()
    if rejected == 0:
        raise RuntimeError('Signature verifier accepted corrupt installer')


def publish():
    plan = json.loads((DIST / 'manifest.json').read_text())
    tag = plan['tag']
    run('git', 'push', 'origin', f'{plan["source_commit"]}:refs/tags/{tag}')
    notes = WORK / 'notes.md'
    notes.write_text(f'Unofficial Windows amd64 personal build, based on upstream {plan["upstream_tag"]}.\n\n'
                     f'Bundled Mihomo: {plan["core_tag"]}. Install this package once, then use the ordinary in-app updater. '
                     'The existing configuration directory is preserved.\n\n'
                     f'[Maintenance and migration](https://github.com/{REPO}/blob/codex/personal-builds/.personal/README.md). '
                     'Update signatures and SHA256SUMS were verified before publication.\n', encoding='utf-8')
    run('gh', 'release', 'create', tag, '--repo', REPO, '--verify-tag', '--draft', '--title', tag, '--notes-file', str(notes),
        *map(str, sorted(DIST.iterdir())))
    assets = json.loads(run('gh', 'release', 'view', tag, '--repo', REPO, '--json', 'assets', capture=True))['assets']
    expected = {path.name: path for path in DIST.iterdir()}
    if {a['name'] for a in assets} != set(expected):
        raise RuntimeError('Draft release assets are incomplete')
    for asset in assets:
        if asset['size'] != expected[asset['name']].stat().st_size or asset.get('digest') != f'sha256:{sha(expected[asset["name"]])}':
            raise RuntimeError(f'Release asset verification failed: {asset["name"]}')
    run('gh', 'release', 'edit', tag, '--repo', REPO, '--draft=false', '--prerelease=false', '--latest')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'package', 'publish'])
    globals()[parser.parse_args().command]()
