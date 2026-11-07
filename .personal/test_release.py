import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

import release


class ReleaseRetryTest(unittest.TestCase):
    def test_failed_publication_does_not_reuse_an_existing_version(self):
        refs = [{'ref': 'refs/tags/v2.5.10-personal.9'},
                {'ref': 'refs/tags/v2.5.11-personal.10'},
                {'ref': 'refs/tags/v2.5.11'}]
        self.assertEqual(release.next_build(refs), 11)
        self.assertEqual(release.next_build([]), 1)

    def test_upstream_revision_peels_annotated_tag(self):
        revision = 'a' * 40
        tag_object = 'b' * 40
        ref = 'refs/tags/v2.5.10'
        repository = 'https://github.com/example/app.git'
        output = f'{tag_object}\t{ref}\n{revision}\t{ref}^{{}}'
        with patch.object(release, 'run', return_value=output) as git:
            self.assertEqual(release.upstream_revision(repository, 'v2.5.10'), revision)
            git.assert_called_once_with('git', 'ls-remote', '--exit-code', '--tags', repository, ref, f'{ref}^{{}}', capture=True)
            git.return_value = f'{revision}\t{ref}'
            self.assertEqual(release.upstream_revision(repository, 'v2.5.10'), revision)

    def test_checksum_list_accepts_binary_markers_and_rejects_ambiguity(self):
        checksum = 'ab' * 32
        name = 'mihomo-windows-amd64-v1.19.33.zip'
        line = f'{checksum.upper()} *{name}\n'
        self.assertEqual(release.core_archive_checksum(f'\n{line}\n', name), checksum)
        for invalid in ('', f'abcd  {name}\n', line + line):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(RuntimeError, 'Expected one SHA-256 entry'):
                release.core_archive_checksum(invalid, name)


VERSION = '2.5.10-personal.11'
TAG = f'v{VERSION}'
INSTALLER = f'Clash.Verge_{VERSION}_x64-setup.exe'
PAYLOADS = (INSTALLER, f'{INSTALLER}.sig', 'update.json', 'manifest.json', 'SHA256SUMS')


class PreparationTest(unittest.TestCase):
    def test_noop_requires_a_complete_release_and_matching_manifest(self):
        revision = 'a' * 40
        checksum = 'ab' * 32
        core_tag = 'v1.19.33'
        archive = f'mihomo-windows-amd64-{core_tag}.zip'
        identity = dict(upstream_commit=revision, core_tag=core_tag, core_sha256=checksum,
                        inputs_sha256=release.maintenance_digest())
        assets = [dict(name=name, state='uploaded', size=1, digest=f'sha256:{checksum}', browser_download_url=name)
                  for name in PAYLOADS]
        scenarios = (
            ('complete', assets, json.dumps(identity), True),
            ('missing updater', [a for a in assets if a['name'] != 'update.json'], json.dumps(identity), False),
            ('missing digest', [dict(a, digest=None) if a['name'] == INSTALLER else a for a in assets],
             json.dumps(identity), False),
            ('unreadable manifest', assets, 'not JSON', False),
        )
        for label, published_assets, manifest, unchanged in scenarios:
            responses = {
                f'repos/{release.UPSTREAM}/releases/latest': dict(tag_name='v2.5.10', draft=False, prerelease=False),
                f'repos/{release.CORE}/releases/latest': dict(tag_name=core_tag, draft=False, prerelease=False, assets=[
                    dict(name=archive, browser_download_url=archive, digest=f'sha256:{checksum}'),
                    dict(name='checksums.txt', browser_download_url='checksums.txt'),
                ]),
                f'repos/{release.REPO}/releases/latest': dict(tag_name=TAG, assets=published_assets),
            }
            downloads = {'manifest.json': manifest.encode(), 'checksums.txt': f'{checksum}  {archive}\n'.encode()}
            with (self.subTest(label=label), tempfile.TemporaryDirectory() as directory,
                  patch.object(release, 'WORK', Path(directory)), patch.object(release, 'SOURCE', Path(directory)),
                  patch.object(release, 'api', side_effect=lambda path, **_: responses[path]),
                  patch.object(release, 'run', return_value=f'{revision}\trefs/tags/v2.5.10') as git,
                  patch.object(release, 'read_url', side_effect=downloads.__getitem__),
                  patch.object(release, 'outputs') as outputs):
                if unchanged:
                    release.prepare()
                    outputs.assert_called_once_with(needed=False)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Expected a fresh checkout'):
                        release.prepare()
                    outputs.assert_not_called()
                git.assert_called_once()
                self.assertEqual(git.call_args.args[:2], ('git', 'ls-remote'))


class PublicationTest(unittest.TestCase):
    def test_package_uses_the_same_canonical_filename_in_the_updater_feed(self):
        plan = dict(tag=TAG, version=VERSION, upstream_tag='v2.5.10', core_tag='v1.19.33')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            dist = root / 'dist'
            bundle = source / 'target/x86_64-pc-windows-msvc/release/bundle/nsis'
            bundle.mkdir(parents=True)
            (bundle / 'Clash Verge-upstream-name-setup.exe').write_bytes(b'installer')
            (bundle / 'Clash Verge-upstream-name-setup.exe.sig').write_text('signature', encoding='utf-8')
            pe_offset = 0x80
            executable = bytearray(pe_offset)
            executable[0x3C:0x40] = pe_offset.to_bytes(4, 'little')
            executable.extend(b'PE\0\0\x64\x86')
            (bundle.parents[1] / 'clash-verge.exe').write_bytes(executable)
            (source / 'src-tauri').mkdir()
            release.write_json(source / 'src-tauri/tauri.conf.json', {'plugins': {'updater': {'pubkey': 'key'}}})
            release.write_json(root / 'plan.json', plan)
            with (patch.object(release, 'WORK', root), patch.object(release, 'SOURCE', source),
                  patch.object(release, 'DIST', dist), patch.object(release, 'run', return_value='a' * 40),
                  patch.object(release.subprocess, 'run', return_value=CompletedProcess([], 1)),
                  patch.object(release, 'verify_signature') as verify):
                release.package()
            self.assertEqual({path.name for path in dist.iterdir()}, set(PAYLOADS))
            update = json.loads((dist / 'update.json').read_text(encoding='utf-8'))
            self.assertEqual(update['version'], VERSION)
            for platform in ('windows-x86_64', 'windows-x86_64-nsis'):
                self.assertTrue(update['platforms'][platform]['url'].endswith(f'/{TAG}/{INSTALLER}'))
                self.assertEqual(update['platforms'][platform]['signature'], 'signature')
            verify.assert_called_once_with(dist / INSTALLER, dist / f'{INSTALLER}.sig', 'key', VERSION)
            self.assertIn(f'{release.sha(dist / INSTALLER)}  {INSTALLER}', (dist / 'SHA256SUMS').read_text().splitlines())

    def test_publication_rejects_invalid_assets_and_keeps_draft_on_digest_failure(self):
        plan = dict(tag=TAG, version=VERSION, source_commit='a' * 40, upstream_tag='v2.5.10', core_tag='v1.19.33')
        with (tempfile.TemporaryDirectory() as directory, patch.object(release, 'DIST', Path(directory)),
              patch.object(release, 'WORK', Path(directory)), patch.object(release, 'run') as command):
            root = Path(directory)
            release.write_json(root / 'manifest.json', plan)
            with self.assertRaisesRegex(RuntimeError, 'Local release assets are incomplete'):
                release.publish()
            command.assert_not_called()
            assets = []
            for name in PAYLOADS:
                path = root / name
                if name != 'manifest.json':
                    path.write_bytes(name.encode())
                digest = '0' * 64 if name == INSTALLER else release.sha(path)
                assets.append(dict(name=name, state='uploaded', size=path.stat().st_size, digest=f'sha256:{digest}'))
            command.return_value = json.dumps(dict(isDraft=True, assets=assets))
            with self.assertRaisesRegex(RuntimeError, 'Release asset verification failed'):
                release.publish()
            operations = [call.args[:3] for call in command.call_args_list]
            self.assertIn(('gh', 'release', 'create'), operations)
            self.assertNotIn(('gh', 'release', 'edit'), operations)


if __name__ == '__main__':
    unittest.main()
