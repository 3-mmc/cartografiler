# Cartografiler on macOS (first native test)

The standalone build targets **Apple Silicon (M1 or newer), macOS 14 or newer**.
It contains Python, NumPy/SciPy, Pillow, Numba, Zstandard, the Godot release runtime,
and the map resources. End users do not install Python or Godot.

## Install the standalone app

Download `Cartografiler-macos-arm64.zip` from the **Build macOS app** workflow's
`Cartografiler-macos-arm64` artifact. Unzip it, move `Cartografiler.app` to Applications,
and double-click. It starts over your home folder without opening Terminal.

This first build is ad-hoc signed and is not Apple-notarized. macOS may require its
explicit approval for an app from an unidentified developer. Developer ID signing and
notarization can be added when a signing identity is available; the build supports
`CARTOGRAFILER_SIGN_IDENTITY`. Logs are in `~/Library/Logs/Cartografiler/application.log`.

The iDisk application icon requested for this project is from BeOS Icons. Its original
source files, Design Science License, and attribution are bundled with the app.

## Build the standalone app

Use an Apple Silicon Mac with arm64 Python 3.12 (or the GitHub Actions workflow):

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[images,performance,macos-build]'
python3 tools/build_macos.py
```

The builder verifies official Godot downloads against release SHA512 checksums,
exports the map PCK, bundles the backend, verifies signatures, runs frozen preview/tile
worker checks, and tests the packed application's headless lifecycle. The archive and
synthetic cold/cached tile measurements are written under `dist/`.
No app-launch downloads or dependency installation are needed.

The build bundles Numba's existing bit-identical compiled noise kernels and Zstandard
compression. JIT caches live outside the app, so subsequent launches can reuse them.
Each tile process gets one BLAS thread; macOS uses at most four tile workers and reserves
CPU capacity for rendering. Persistent layout/tile caches are retained. The first run
still needs to survey the filesystem and compile noise kernels. The workflow validates
CPU behavior on ARM64; interactive GPU performance needs checking on the target Mac.

## Run from source on an M1 Mac

Use an Apple Silicon Python 3.11 or newer and the standard, universal macOS
[Godot 4.7.2 download](https://godotengine.org/download/macos/). No .NET build is needed.
Copy this repository to the Mac and extract `Godot.app` into its `tools/` folder.
Alternatively, point `CARTOGRAFILER_GODOT` at an existing Godot installation:

```bash
export CARTOGRAFILER_GODOT='/Applications/Godot.app/Contents/MacOS/Godot'
```

In Terminal, from the repository:

```bash
python3 -c 'import platform; print(platform.machine())' # arm64 for native M1 Python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[images]'
./cartografiler "$HOME"
```

Start without the optional Numba/Zstandard acceleration packages. Once basic operation
passes, install them with `python3 -m pip install -e '.[images,performance]'`.

With `tools/Godot.app` and `.venv` in place, double-click `Cartografiler.command` in
Finder to launch over your home folder. This is a development launcher that opens
Terminal, not a bundled or notarized application. Leave Terminal open while using it.

## What should work

- Native Godot map rendering using the existing Compatibility renderer.
- A local, authenticated Python service and spawned tile/preview workers.
- Home-folder navigation, previews, file operations, and the Bash palette.
- External disks under `/Volumes` as separate continents, including names with spaces.
- File opening through macOS associations (`open`).

The survey lists directories and metadata in the background, including the root tree.
It does not follow symlinks. `/System/Volumes` is excluded from automatic traversal:
macOS exposes startup system/data volumes there, overlapping the normal filesystem
view. See [Apple's APFS description](https://support.apple.com/en-ca/guide/security/seca6147599e/web).
The normal `/Users`, `/Applications`, and `/private` views are still surveyed.

macOS controls access to Desktop, Documents, Downloads and some volumes through
[privacy permissions](https://support.apple.com/en-ph/guide/security/secddd1d86a6/web).
Allow the folders you want to browse when prompted. Protected locations can remain
unavailable; a permission failure should stay visible as unknown, rather than empty.
Do not run Cartografiler as root. Full Disk Access is not required for the initial test.

Caches and recoverable trash still use `~/.local/share/branch/` (or `XDG_DATA_HOME`).
Preview workers retain timeouts and format-specific size limits. The Linux address-space
memory ceiling is not applied on macOS; an equivalent limit remains future work.

Pinch open to zoom in and pinch closed to zoom out. Two-finger vertical scrolling
also zooms at the cursor; click and drag to pan. The +/− keys and on-screen buttons
remain available.

## Validate on the Mac

From the activated virtual environment:

```bash
python3 -m unittest discover -s tests -v
./tools/Godot.app/Contents/MacOS/Godot --headless --path native --editor --import --quit
./tools/Godot.app/Contents/MacOS/Godot --headless --path native --script res://tests/streaming.gd
./tools/Godot.app/Contents/MacOS/Godot --headless --path native --script res://tests/gestures.gd
./cartografiler / --smoke --capture /tmp/cartografiler-world.png
./cartografiler / --smoke --capture /tmp/cartografiler-home.png --enter "$HOME"
```

Use the executable selected above instead if Godot lives outside `tools/`.
Then check panning/zooming, opening a text file/image, a folder with spaces, an external
disk, and a restricted folder. Test rename/trash/undo only in a temporary test folder.
Close the map and check that its service and workers exit too.

Developer ID notarization and interactive M1 performance measurements remain
outstanding. Native Windows world paths are deferred.
