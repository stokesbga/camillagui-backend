# Build and install a release

This fork's `Build and release` workflow combines the redesigned frontend with
this backend and freezes the Python runtime/dependencies with PyInstaller. The
Pi does not need Node, npm, pip or a virtual environment to run the bundle.
CamillaDSP itself is installed separately.

## Build on GitHub (recommended)

1. Commit and push the backend changes, including `.github/workflows/build.yml`
   and `release_automation/`. Enable Actions in the fork if GitHub prompts you.
2. Open **Actions → Build and release → Run workflow** in
   [stokesbga/camillagui-backend](https://github.com/stokesbga/camillagui-backend/actions/workflows/build.yml).
3. Leave **Platforms to bundle** as `raspberry-pi-64`. Leave **Frontend ref** blank
   to use the pinned commit in `release_automation/versions.yml`, or supply a
   branch, tag or commit from the configured frontend repository.
4. Download the `release-bundle_linux_aarch64.tar.gz` artifact from the completed
   run. GitHub wraps artifacts in a ZIP: unzip that wrapper to get
   `bundle_linux_aarch64.tar.gz` and its `.sha256` file.

Ordinary backend pushes also build the Pi bundle. **All** additionally builds
Linux x86-64, Linux armv7/armv6, Windows and macOS. Pushing a backend tag builds
all platforms and publishes the archives and checksums to that fork's GitHub
Releases page after every build succeeds. Manual and branch builds only upload
Actions artifacts; they do not publish a release.

For future frontend releases, push the frontend commit first, then update
`camillagui_tag` in `release_automation/versions.yml` to its SHA. The current pin
selects this fork's redesigned UI, not the upstream v4.1.0 frontend. Backend and
resolved frontend commits are recorded in each archive's `release.json`;
`python-packages.txt` records the installed Python dependency versions. Python
transitive dependencies are resolved during the build, so this is not a
byte-for-byte reproducible build.

Each bundle is checked by starting its executable, fetching the frontend and
passing a synthetic 750 Hz tone through the included spectrum helper and FFT API.
This does not test real ALSA/HiFiBerry hardware. The source ZIP `camillagui.zip`
also includes the built frontend, but requires a separately installed Python
environment; use the ARM64 tarball for the simplest Pi installation.

## Install on a 64-bit Raspberry Pi

Target: 64-bit Raspberry Pi OS **Bookworm or newer**, on an ARM64-capable Pi.
The native bundle is built on Ubuntu 22.04 ARM64; older OS releases such as
Bullseye may have incompatible system libraries. `getconf LONG_BIT` should print
`64`, and `uname -m` should print `aarch64`.

Copy the tarball and checksum to the Pi (for example with `scp`). In that directory:

```sh
sha256sum -c bundle_linux_aarch64.tar.gz.sha256
mkdir -p ~/camilladsp/configs ~/camilladsp/coeffs
tar -xzf bundle_linux_aarch64.tar.gz -C ~/camilladsp
```

For an **existing installation**, stop the GUI service first and move the old
`camillagui_backend` directory aside before extracting. Keep it for rollback;
avoid extracting a new bundle on top of an old `_internal` directory. Preserve
your current backend configuration before replacing anything.

Keep configuration outside the bundle, so future updates do not overwrite it.
For a new installation, copy the supplied default once:

```sh
cp -n ~/camilladsp/camillagui_backend/_internal/config/camillagui.yml ~/camilladsp/camillagui.yml
nano ~/camilladsp/camillagui.yml
```

For an existing installation, copy your **existing** backend configuration to
that external path instead. Check the DSP host/port, config and coefficient paths,
and state-file path. If you use a custom `gui-config.yml`, keep it outside the
bundle too and set `gui_config_file` to its absolute path.

Start the server:

```sh
~/camilladsp/camillagui_backend/camillagui_backend -c ~/camilladsp/camillagui.yml
```

Open `http://PI_HOSTNAME:5005`. If you already have a systemd service, update its
`ExecStart` to the executable above with `-c` pointing to the external config.
Use absolute paths (no `~`) and run as the user who owns the DSP configuration.
Reload systemd and restart that GUI service after changing its unit. The
CamillaDSP service and its audio configuration are separate from this install.

## Enable live output spectrum

The archive includes `camillagui_backend/spectrum_tap.py` and
`camillagui_backend/docs/spectrum.md`. Follow [the ALSA setup](spectrum.md), using
the installed helper's absolute path, for example:

```conf
file "|/usr/bin/python3 /home/YOUR_USER/camilladsp/camillagui_backend/spectrum_tap.py --rate %r --channels %c --format %f --port 9878"
```

The helper needs the Pi's system Python 3 (standard library only). The server's
Python and NumPy are already bundled. Spectrum remains disabled until you enable
it in the external backend config and configure the ALSA playback tap. Installing
the archive alone does not modify ALSA or the active DSP configuration.

## Build locally on Linux ARM64

The same scripts can run on a Pi or another Linux ARM64 machine. PyInstaller
builds for the OS/architecture it runs on; running it directly on macOS produces
a macOS bundle. Use GitHub's ARM64 runner for the Pi build from your Mac.

Build the frontend in the sibling `camillagui` checkout with `npm ci && npm run
build`, then copy its `build/` contents into this backend's `build/` directory.
In the backend checkout, with a fresh Python environment activated:

```sh
python -m pip install jinja2 PyYAML
python -Bm release_automation.render_env_files --output-dir .release-env
python -m pip install -r .release-env/requirements.txt pyinstaller
python -Bm release_automation.package source \
  --frontend-repository stokesbga/camillagui \
  --frontend-ref "$(git -C ../camillagui rev-parse HEAD)"
cd release-dist/source
python -Bm release_automation.package bundle --asset-name bundle_linux_aarch64.tar.gz
```

Use a clean committed frontend so its recorded SHA matches the copied build.
Output is `release-dist/source/release-dist/bundle_linux_aarch64.tar.gz` relative
to the backend checkout. The scripts refuse to reuse an existing staging/bundle
directory; move the previous `release-dist` aside or choose a new `--output-dir`
(before `source` or `bundle`) for another run. The environment renderer's
`--output-dir` option avoids overwriting the development `pyproject.toml`.
