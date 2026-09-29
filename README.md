# Branch Atlas

A native, local file manager in which your whole filesystem is **one continuous world
map**. Each disk is a continent. Every folder owns a territory sized by what it holds,
rivers run down the valleys between folders toward the parent and on to a delta, land cover
follows content, and a folder's own files lie as fields: one patch per kind (a forest of
images, a town of source files, a massif of PDFs), one parcel per file.
The geography is continuous and irregular, with no tiles, board or grid.

## Run on this machine

```bash
branch-atlas                  # the world, starting over your home folder
branch-atlas /mnt/e/Photos    # start over any folder
~/branch/atlas demo           # synthetic demo folder
```

It opens a native **Godot window through WSLg**, rendered on the **GTX 1080** (the
launcher sets `GALLIUM_DRIVER=d3d12`; without it WSLg falls back to CPU rendering). A
**Branch Atlas** entry is also in the Windows Start menu. Python starts a private,
authenticated loopback service and shuts it down when the window closes. No files are
uploaded.

The first launch surveys the filesystem in the background. On this machine that's 3.65 M
files and 461 k folders across every drive, in about 16 minutes. The map is usable from
the first seconds and fills in coarse to fine; unsurveyed places are parchment. To survey
ahead of time from a terminal:

```bash
python3 -m branchfm.index /          # everything
python3 -m branchfm.index /mnt/d     # one drive
```

The index lives in `~/.local/share/branch/index.sqlite` (about 1.3 GB for this machine),
with laid-out territories cached next to it in `layout.sqlite`. Both are rebuilt if deleted.

The lightweight terminal companion is `./branch /path`. Press `?` there for controls.

## Moving around

| Control | Action |
|---|---|
| Drag · wheel · right-drag | Pan · zoom at the cursor · turn and tilt |
| Click · double-click · Enter | Select · fly there |
| Backspace · Home | Up to the enclosing place · the whole world |
| WASD / arrows · Q/E · PgUp/PgDn | Pan · turn · tilt |
| G · K · R · L · F · I | Gazetteer · legend · weather radar · labels · map only · collapse notes |
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

The grammar and its reasoning are in **[docs/cartography.md](docs/cartography.md)** and in
the app (`K`). In short: continents are disks, climate is the filesystem and write
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
  undo of moves/renames/trash. Existing destinations are refused. Trash is Branch's own
  store under `~/.local/share/branch/trash`, not the Windows Recycle Bin.

## Architecture

| Piece | Where |
|---|---|
| Survey index and native crawlers (Linux `scandir`, Windows Python worker) | `branchfm/index.py` |
| World layout: power-diagram territories, fractal borders, rivers, landmarks | `branchfm/world.py` |
| Terrain synthesis: 257² height/colour/aux/material tiles at any zoom; fields, rivers, lakes, deltas | `branchfm/tiles.py` |
| CC0 ground textures (Poly Haven) and their baker | `native/textures/`, `tools/fetch_textures.py` |
| Map service: tiles (worker processes), labels, picking, survey status | `branchfm/atlas_api.py`, `branchfm/service.py` |
| Native client: streaming terrain, floating origin, overlays | `native/main.gd`, `native/terrain.gdshader` |

Tiles are generated on demand in a pool of worker processes, about 0.3–0.5 s each.
The client keeps the view 100 render units wide at any zoom (floating origin) with world
coordinates in doubles. Territories are laid out lazily, the first time the map needs a
place's interior, and cached on disk.

## Validate

```bash
python3 -m unittest discover -s tests -v
./tools/Godot_v4.7.2-stable_linux.x86_64 --headless --path native --editor --import --quit
./atlas / --smoke --capture /tmp/world.png
./atlas / --smoke --capture /tmp/home.png --enter /home/praetor
```

## References

- [Conrad Barski's spatial file-browser clip](https://x.com/lisperati/status/2104681013909893184): the original inspiration.
- [Mach Speed Intercept](https://store.steampowered.com/app/3438610/Mach_Speed_Intercept/): continuous relief, restrained materials, free camera movement. All geometry and colour here are generated; no game assets are used.

Project-specific agent instructions, machine paths, and accepted design constraints are in `AGENTS.md`.
