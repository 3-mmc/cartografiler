# Cartografiler

A native, local file manager in which your whole filesystem is **one continuous world
map**. Each disk is a continent. Every folder owns a territory sized by what it holds,
rivers run down the valleys between folders toward the parent and on to a delta, land cover
follows content, and a folder's own files lie as fields: one patch per kind (a forest of
images, a town of source files, a massif of PDFs), one parcel per file.
The geography is continuous and irregular, with no visible board or regular grid.
Its visual direction draws on Civilization V map visuals: blended terrain, clustered
settlements, rivers between territories, and a tilted perspective camera.

## macOS app

The Apple Silicon standalone build targets macOS 14+. It bundles Python, dependencies,
Godot's release runtime, and the requested iDisk icon. Build/download instructions are in
**[docs/macos.md](docs/macos.md)**. The first build is not Apple-notarized.

## Run from source

From the repository on Linux (or WSL with WSLg), install Python 3.11+ dependencies and
put the official Godot 4.7.2 stable Linux x86_64 binary in
`tools/Godot_v4.7.2-stable_linux.x86_64`:

```bash
python3 -m pip install -e '.[images,performance]'
./cartografiler "$HOME"        # start over your home folder
./cartografiler /              # the whole world
./cartografiler /path/to/folder # start over any folder
./cartografiler demo            # explicitly synthetic demo files
```

The installed `cartografiler` command opens the same native Godot application.
NumPy and SciPy are required; Pillow enables image previews, while optional Numba and
Zstandard accelerate noise generation and tile compression. Without the performance
extras, NumPy and deflate provide the fallbacks. The existing `./atlas` launcher and
`./branch` terminal companion remain available for compatibility.

On WSLg, the launcher selects Mesa's D3D12 driver when available to use the GPU.
On native Linux it uses the existing graphics configuration. Python starts a private,
authenticated loopback service and shuts it down when the window closes. No files are
uploaded.

The first launch surveys the filesystem in the background. On this machine that's 3.65 M
files and 461 k folders across every drive, in about 16 minutes (a prior WSL benchmark;
your timing depends on the filesystem). Native Linux data disks, including mounts under
`/media` and `/run/media`, are discovered as separate continents. The map is usable from
the first seconds and fills in coarse to fine; unsurveyed places are parchment. To survey
ahead of time from a terminal:

```bash
python3 -m branchfm.index /          # everything
python3 -m branchfm.index /mnt/d     # one drive
```

The index lives in `~/.local/share/branch/index.sqlite` (about 1.3 GB for this machine),
with laid-out territories in `layout.sqlite` and rendered tiles in `tiles.sqlite` next
to it. These caches persist between sessions and are rebuilt if deleted. Tile cache
entries expire with time-sensitive features and are invalidated by survey changes.
The legacy `branch` data directory is retained so existing indexes and trash stay usable.

The lightweight terminal companion is `./branch /path`. Press `?` there for controls.

## Native platform support in progress

Linux/WSL is the validated platform. Host-specific runtime selection, file opening,
mount queries, and preview memory limits live in `branchfm/platform.py`.

For macOS, place `Godot.app` under `tools/`, or set `CARTOGRAFILER_GODOT` to its
`Contents/MacOS/Godot` executable. The Unix launchers resolve paths through Python;
macOS uses `open` for file associations and discovers external disks under `/Volumes`.
The survey excludes `/System/Volumes` to avoid crawling the duplicate APFS startup
volume tree. The standalone build includes compiled noise and compressed persistent
tile caches. See **[macOS setup and validation](docs/macos.md)** for build and testing details.

Windows runtime selection and file-opening adapters are present. The full native
Windows application is **not ready**: drive/UNC paths, world-root representation,
volume discovery, junction handling, command-palette process control, and packaging
still need porting. Linux retains preview address-space limits; macOS and Windows
retain worker timeouts but currently have no equivalent memory ceiling.

## Moving around

| Control | Action |
|---|---|
| Drag · wheel · right-drag | Pan · zoom at the cursor · turn and tilt |
| Click · double-click · Enter | Select · fly there |
| Backspace · Home | Up to the enclosing place · the whole world |
| WASD / arrows · Q/E · PgUp/PgDn | Pan · turn · tilt |
| G · K · R · L · F · I | Gazetteer · legend · weather radar · labels · map only · collapse notes |
| V · M | Perspective (Civ V-like) or flat map · 3D trees, houses and boulders |
| Ctrl+P · Ctrl+L | Bash navigation palette · type a path |
| Space · F2 · Delete · Ctrl+C/X/V · Ctrl+Z · Ctrl+Shift+N | Peek · rename · trash · copy/cut/paste · undo · new folder |

The **cartouche** (top left) names the place under the centre of the view and shows the
survey's progress. **Field notes** (right) appear when you select something. The
**Gazetteer** lists the place you are over. Chrome fades while the mouse rests.

## Bash navigation

Ctrl+P runs explicitly submitted commands in the place you are over. Bash is real and runs
as your user; typing alone never runs anything. Paths in stdout become destinations, and
choosing one flies you there.

```bash
find . -iname '*.pdf'
rg --files | grep Uzbekistan
printf '%s\n' '/mnt/e/Photos/My trip/photo.jpg'
```

Commands have a 10-second limit, 1 MiB of output and 200 destinations. Bash startup
files are not loaded.

## Reading the map

Link-heavy folders form aqueduct arcades. Source-file towns grow along riverbanks and
can bridge crossings. Terrain streams coarse to fine with completed fine tiles masking
their fallback coverage, and picking requests remain responsive during tile loading.

The grammar and its reasoning are in **[docs/cartography.md](docs/cartography.md)** and in
the app (`K`). Continents are disks, climate is the filesystem and write
permission, territory is size, water flows toward the parent (tributaries, hub lakes,
waterfalls where the ground changes, deltas at the sea), land cover is content, files are
fields, rock is age (basalt → sandstone → granite), snow is dormancy, and the optional radar
shows files changed today. Features from the earlier nested-map version that haven't yet
moved into the continuous world are listed there too: tides, sea level, roads, time
slider, and page-count mountains.

## Previews and file operations

- Text, JSON, CSV/TSV, images, PDF text (first six pages), audio/video metadata, ZIP/TAR
  listings, DOCX text, PPTX slide text and raw XLSX values are previewed in bounded
  subprocesses that never execute file contents.
- Rename, new folder, copy, cut/paste (into the place you are over), recoverable trash, and
  undo of moves/renames/trash. Existing destinations are refused. Trash is Cartografiler's own
  store under `~/.local/share/branch/trash`, not the Windows Recycle Bin.

## Architecture

| Piece | Where |
|---|---|
| Survey index and native crawlers (Linux `scandir`, Windows Python worker) | `branchfm/index.py` |
| World layout: power-diagram territories, fractal borders, rivers, landmarks | `branchfm/world.py` |
| Terrain synthesis: 257² height/colour/aux/material tiles at any zoom; fields, rivers, lakes, deltas | `branchfm/tiles.py` |
| CC0 ground textures (Poly Haven) and their baker | `native/textures/`, `tools/fetch_textures.py` |
| 3D landmarks: procedural low-poly meshes, GPU-instanced per tile | `native/models.gd`, `native/instances.gdshader` |
| Map service: tiles (worker processes), labels, picking, survey status | `branchfm/atlas_api.py`, `branchfm/service.py` |
| Native client: streaming terrain, floating origin, overlays | `native/main.gd`, `native/terrain.gdshader` |

Tiles are generated on demand in a pool of worker processes, about 0.3–0.5 s each.
The client keeps the view 100 render units wide at any zoom (floating origin) with world
coordinates in doubles. Territories are laid out lazily, the first time the map needs a
place's interior, and cached on disk.

## Validate

```bash
python3 -m unittest discover -s tests -v
./tools/Godot_v4.7.2-stable_linux.x86_64 --headless --path native --script res://tests/streaming.gd
./tools/Godot_v4.7.2-stable_linux.x86_64 --headless --path native --editor --import --quit
./cartografiler / --smoke --capture /tmp/world.png
./cartografiler / --smoke --capture /tmp/home.png --enter "$HOME"
```

## References

- [Civilization V map visuals and official screenshots](https://store.steampowered.com/app/8930/Sid_Meiers_Civilization_V/): terrain blending, clustered improvements, rivers along borders, and the perspective camera. Cartografiler uses generated geometry and CC0 materials.
- [Mach Speed Intercept](https://store.steampowered.com/app/3438610/Mach_Speed_Intercept/): continuous relief, restrained materials, free camera movement. All geometry and colour here are generated; no game assets are used.

Project-specific agent instructions, machine paths, and accepted design constraints are in `AGENTS.md`.
