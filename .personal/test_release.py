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
    def test_check_starts_windows_only_for_a_new_stable_app(self):
        revision = 'a' * 40
        checksum = 'ab' * 32
        tag = 'v2.5.10'
        assets = [dict(name=name, state='uploaded', size=1, digest=f'sha256:{checksum}', browser_download_url=name)
                  for name in PAYLOADS]
        unchanged = dict(upstream_tag=tag, upstream_commit=revision,
                         core_tag='v1.19.32', core_sha256='old', inputs_sha256='old')
        scenarios = (
            ('unchanged app and changed core', assets, json.dumps(unchanged), False),
            ('changed maintenance inputs', assets, json.dumps(dict(unchanged, inputs_sha256='new')), False),
            ('new upstream app', assets, json.dumps(dict(unchanged, upstream_tag='v2.5.9')), True),
            ('incomplete release', assets[1:], json.dumps(unchanged), True),
            ('broken manifest', assets, 'not JSON', True),
        )
        for label, published_assets, manifest, needed in scenarios:
            responses = {
                f'repos/{release.UPSTREAM}/releases/latest': dict(tag_name=tag, draft=False, prerelease=False),
                f'repos/{release.REPO}/releases/latest': dict(tag_name=TAG, assets=published_assets),
            }
            with (self.subTest(label=label),
                  patch.object(release, 'api', side_effect=lambda path, **_: responses[path]),
                  patch.object(release, 'run', return_value=f'{revision}\trefs/tags/{tag}') as git,
                  patch.object(release, 'read_url', return_value=manifest.encode()),
                  patch.object(release, 'outputs') as outputs):
                release.check()
                outputs.assert_called_once_with(needed=needed)
                git.assert_called_once()

    def test_prepare_skips_all_core_metadata_when_upstream_app_is_unchanged(self):
        revision = 'a' * 40
        tag = 'v2.5.10'
        checksum = 'ab' * 32
        assets = [dict(name=name, state='uploaded', size=1, digest=f'sha256:{checksum}', browser_download_url=name)
                  for name in PAYLOADS]
        responses = {
            f'repos/{release.UPSTREAM}/releases/latest': dict(tag_name=tag, draft=False, prerelease=False),
            f'repos/{release.REPO}/releases/latest': dict(tag_name=TAG, assets=assets),
        }
        with (patch.object(release, 'api', side_effect=lambda path, **_: responses[path]),
              patch.object(release, 'run', return_value=f'{revision}\trefs/tags/{tag}'),
              patch.object(release, 'read_url', return_value=json.dumps(
                  dict(upstream_tag=tag, upstream_commit=revision, core_tag='outdated',
                       inputs_sha256='outdated')).encode()),
              patch.object(release, 'outputs') as outputs):
            release.prepare()
            outputs.assert_called_once_with(needed=False)


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
