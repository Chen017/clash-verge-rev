# Clash Verge Personal

完整中文需求与维护清单见 [个人 Fork 定制说明](https://github.com/Chen017/mihomo/blob/codex/window-builds/CUSTOMIZATIONS.md)。后续升级请保留列出的配置语义和签名身份；移除补丁前应先验证上游已有等价实现。

Unofficial Windows amd64 builds of the latest stable Clash Verge Rev, bundling Chen017/mihomo with independent XHTTP download routing and VLESS 0-RTT ticket recovery. Only one NSIS installer is published. No ARM, 32-bit, MSI, portable or fixed-WebView variants are built.

## Migration and updates

Install the personal installer once, choosing the current installation directory. Export a configuration backup before migration. The application identifier and configuration directory remain `io.github.clash-verge-rev.clash-verge-rev`; existing providers and selections are retained. The personal stable core is selected automatically, including migration from the old Alpha selection. The two internal core filenames contain the same verified personal binary for installer/service compatibility.

After migration, use the ordinary application updater. Its update feed and permanent signing key belong to this fork. Core-only updates increment `personal.N`, so a changed core is delivered in a new installer without manually replacing files. Updating an administrator-approved service core still uses the existing elevation and rollback mechanism. A core upgrade verifies the release checksum and the actual running core version before reporting success. Windows may request permission to install or approve a service update.

## Maintenance and local verification

`codex/personal-builds` is the maintenance and default branch. Keep the custom app changes and their replay in `app.patch` synchronized. The release script starts from the official stable tag and applies this patch to a separate worktree. `core.mjs` stages the pinned ZIP into both sidecar slots after validating the archive checksum, Windows amd64 header and reported version. `release.py` handles planning, packaging and publication. `test_release.py` exercises those boundaries without network access or signing secrets.

Run the offline checks from the repository root with Git, Python 3.11 or later and Node.js. Use `python3` where `python` is unavailable.

```sh
python -B -m unittest discover -s .personal -p 'test_*.py' -v
node --check .personal/core.mjs
git worktree add --detach .personal/work/patch-check v2.5.10
git -C .personal/work/patch-check apply --check ../../../.personal/app.patch
git worktree remove .personal/work/patch-check
```

Use the new stable tag in the patch check when rebasing the customizations. If a newer release needs a three-way application, review the overlapping patch hunks and compare the resulting custom behavior before updating `app.patch`.

For a local Windows integration build, install Visual Studio C++ build tools and WebView2. Authenticate `gh` for public release metadata, then run `python .personal/release.py prepare` from a fresh maintenance checkout. It creates `.personal/work/source` only when the release inputs require a build. Use the Node.js and Rust versions recorded in `.personal/work/plan.json` and the pnpm version in the prepared `package.json`. Run these commands from `.personal/work/source`.

```sh
pnpm install --frozen-lockfile
pnpm prebuild x86_64-pc-windows-msvc
pnpm web:build
cargo test --release --features clippy --target x86_64-pc-windows-msvc -p clash-verge --lib personal_update_tests
cargo test --release --features clippy --target x86_64-pc-windows-msvc -p clash-verge --lib feat::core_upgrade::tests
```

The `clippy` feature supplies the Windows test manifest. The updater test covers numeric ordering within `personal.N`, where two installers can share an upstream version. The core upgrade test rejects corrupt archives and unrelated checksums. The packaging regression checks that the public filename, updater URL and SHA-256 entry agree even when upstream's installer filename contains spaces.

## CI and release

The [Personal Windows amd64 workflow](../.github/workflows/personal-windows.yml) runs offline checks on pull requests to `codex/personal-builds` touching the custom files or workflow. Pushes that change custom code, tests or the workflow, manual dispatch on that branch and the six-hour schedule run the release job in the personal repository. GitHub runs scheduled workflows from the default branch and may suspend them for inactive public repositories. If scheduling stops, check the default branch and workflow state in Actions before using manual dispatch.

When the upstream commit, Mihomo tag and checksum, and build inputs match a complete published release, preparation stops before fetching source history or setting up build tools. The build input hash covers the patch, core staging code, release script and release workflow. Builds cache the prepared source's pnpm store and Rust dependencies. The workflow builds one signed NSIS installer, checks the version recorded in its updater signature and runs the signature verifier against both the real installer and a corrupt copy. A draft becomes the latest release only after all five expected assets have the same size and digest as the local files.

Generated tags point to the exact app source. `manifest.json` records the upstream tag and commit, generated app commit, pinned core source provenance and archive checksum. The generated source keeps upstream workflows unchanged, and publication uses the built-in repository token's contents permission. The token is passed to the metadata and publication steps, while checkout leaves no Git credentials in the worktree used for the build.

Patch conflicts and failed build or signature checks leave the last successful release available. Inspect the failed step and the `windows-failure-evidence` artifact when present. For a failed publication with a tag or draft already on GitHub, rerun from Actions so a new `personal.N` is allocated above existing tags. For local retries, start from a fresh checkout so partial files in `.personal/work` or `.personal/dist` do not enter another build.

Secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` hold the permanent encrypted Tauri updater key and password. Existing installations depend on keeping this key. The updater public key is in `src-tauri/tauri.conf.json`.
