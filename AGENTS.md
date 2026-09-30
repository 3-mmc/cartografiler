# Cartografiler — project instructions

## Location and entry points

- Working directory: `/home/calomel/Projekte/cartografiler`; original WSL checkout: `/home/praetor/branch`.
- Main product: a native Godot file manager showing the **whole filesystem as one continuous world map**, launched with `./cartografiler [/path/to/start]` (`./cartografiler /` for the world view); `./atlas` is a compatibility alias.
- Legacy WSL convenience launchers: `~/.local/bin/branch-atlas` and `~/.local/bin/branch-fm`. Desktop entry: `~/.local/share/applications/branch-atlas.desktop` (starts in `/home/praetor`).
- Terminal companion: `./branch /path/to/folder`.
- `python3 -m branchfm.demo` creates explicitly synthetic demo files; `./atlas demo` starts over them.
- Survey from a terminal: `python3 -m branchfm.index /` (or a path).
- Do not edit `/home/praetor/AGENTS.md` for this project: it is generated from the shared machine instructions.

## Tools and architecture

- Godot **4.7.2 stable**, official Linux x86_64 binary at `tools/Godot_v4.7.2-stable_linux.x86_64`. Compatibility renderer (GL).
- **GPU:** WSLg's default GL is llvmpipe (CPU). `GALLIUM_DRIVER=d3d12` reaches the GTX 1080 (verified 2026-09-29: "Microsoft - D3D12 (NVIDIA GeForce GTX 1080)"); the launcher sets it. Vulkan/Forward+ still falls back to llvmpipe, because Debian's Mesa ships no Dozen (dzn) ICD.
- Python **3.13**, numpy 2.2, scipy 1.15. `branchfm/service.py` launches Godot and a token-authenticated, loopback-only HTTP service on an ephemeral port. Closing Godot stops the service.
- **Survey index** `branchfm/index.py`: SQLite at `$XDG_DATA_HOME/branch/index.sqlite` (≈1.3 GB for 3.65 M files). Breadth-first priority queue (0 viewed, 1 start, 2 home, 3 Linux, 4 Windows drives, 5 streamed drives such as G:). Linux paths via `os.scandir`; `/mnt/<letter>` via a persistent Windows `py.exe` worker (native NTFS: ≈70k entries/s on D:, 7k/s on E: HDD, versus ≈1k/s over 9p) with a 20 s per-listing timeout. Junctions/symlinks are never descended. A full survey is re-run every 12 h at launch (`meta.last_full_survey`).
- **World layout** `branchfm/world.py`: power-diagram territories on per-directory rasters, seeded by path hash, weights (files+dirs)^0.4 with a 5% floor; lazily computed and cached in `layout.sqlite` (shared by processes, persists). Root: `/mnt` drives promoted to continents (two-stage diagram: continents, then provinces); sea only between disks. Borders: shared world-absolute warp octaves (`warp_offsets`), identical at layout and render time.
- **Terrain** `branchfm/tiles.py`: 257² edge-inclusive tiles, format `BTL2` (float32 heights relative to tile base + RGBA colour + RGBA aux: rain, fog, depth, ridge + two RGBA material-weight maps), rendered in a spawn process pool (`atlas_api.py`). Client streams `/tile`, `/places`, `/at`, `/region`, `/status`. About 0.3–0.5 s per tile per worker.
- **Fields** (`World._places`, `Synth._fields`): a folder's own files are one patch per kind (power diagram over home sub-cells, seeded toward the child richest in that kind) and one Lloyd-relaxed parcel per file, alphabetical across the patch. Rendered by nearest-two-sites lookup (parcel edge = distance to the bisector).
- **Rivers** (`World._rivers`): a Dijkstra tree from the outlet over land, cheap along borders and home, dear across interiors; widths ∝ √flow; streams bent by the same warp as borders (`_follow_borders`); ridges gated off near them (`Territory.river_dist`). Hub lakes (≥ 6 subfolders), falls (zone/permission change), deltas at each disk's mouth (root only).
- **3D landmarks:** `Synth._instances` emits per-tile instances (tile format `BTL3`: after the material maps, a uint32 count and 24-byte records u, v, height/S, size/S as float32 + kind, yaw, RGB bytes). Kinds: broadleaf, conifer, house, boulder; meshes are built procedurally in `native/models.gd`, drawn by `native/instances.gdshader` (skip_vertex_transform; the instance transform carries tile-relative position/rotation/size so float32 keeps precision). Only tiles at the current level show models.
- **Roles and landforms** (2026-09-29): `world.file_role` (name/path only) gives hall, power, factory, depot, silo, house, arch, oak, shrub; `LANG_STYLE` in `tiles.py` maps extensions to architecture; 19 models (`MODEL_NAMES` in `tiles.py`, same order in `models.gd` and `MODEL_FIXED` in `main.gd`). Kind `weights` (model files; `.h5` excluded: often plain data). `world.climate()` keeps virtual filesystems desert even though read-only. Monuments: `Atlas.find_monuments` (background, daily) stores the largest file per disk in `meta.monuments`, which the tile workers read.
- Land-only height terms (the fractal `detail`) are faded by `coast_distance` of the root; unfaded, the coarse octaves stood as 10%-of-a-tile sea walls along every coast (`test_no_sea_walls_at_the_coast`). The smoke test waits for three steady all-loaded checks: a single check captured coarse fallback tiles.
- Don't reuse `hf` in `_fields` (it is the parcel height fade); a city-height variable of that name once broke every tile over a big town (`test_a_big_town_renders_at_every_zoom`).
- **Camera:** perspective, 38° horizontal FOV, default tilt 50° (Civ V-like); `V` toggles the orthographic map view, `M` toggles models. Tile coverage uses the view's ground footprint (`view_bbox`).
- **Libraries** (`World._uniform`) and **bytes per kind** (`nodes.kind_bytes`, migrated by re-settling every folder, ≈25 s for 461 k folders): shares via `world.content_shares`.
- **Materials:** CC0 Poly Haven textures in `native/textures/` (baked by `tools/fetch_textures.py`, loaded at runtime into a `Texture2DArray`; `.gdignore` keeps Godot from importing them).
- **Client** `native/main.gd` + `native/terrain.gdshader`: floating origin (100 render units across the view at any zoom, world coords in doubles), orthographic tilted camera, per-pixel normals from the height texture.
- Shared filesystem operations: `branchfm/model.py`. Previews: `branchfm/preview.py`. Per-file metadata: `branchfm/atlas.py` (not yet wired into the continuous map).
- Python standard-library `unittest` tests in `tests/` (`test_world.py` covers index, layout, border consistency, no-cliffs, tile format, continents).
- Fonts: DejaVu Serif, Serif Italic and Sans (Interface.ttf), with their licence in `native/fonts/LICENSE.txt`.

## Accepted product direction

- The visual inspiration is Civilization V map visuals: continuous terrain, clustered improvements, rivers along borders, and a tilted camera. Reference screenshots: https://store.steampowered.com/app/8930/Sid_Meiers_Civilization_V/ .
- **One continuous, already-browsable world built from a persistent index** (accepted 2026-09-29): no per-directory islands dropped onto a map. Features blend into one another as in Civilization V and Mach Speed Intercept.
- **Continents are disks** (user's choice, 2026-09-29): the Linux disk and each drive are separate landmasses across sea; below that, provinces share land borders. Do not turn large folders into archipelagos (tried; it read as cracked tiles).
- **Do not build a regular board of equal-sized hex/square tiles.** Geography is amorphous, expansive, less cartoonish, visually informed by **Mach Speed Intercept**'s map view (continuous satellite-like ground, dark ridges with strong shadows, restrained palette). Reference install: `/mnt/e/SteamLibrary/steamapps/common/Mach Speed Intercept Playtest`; screenshots via the Steam API for app 3438610. Never lift game assets.
- **Civilization V assets:** installed at `/mnt/e/SteamLibrary/steamapps/common/Sid Meier's Civilization V` (FPK archives of DDS textures, plus the SDK). Its EULA licenses the software only "for gameplay" and bars derivative works, so do not load or ship its textures, even for prototyping. Use CC0 sources (Poly Haven, ambientCG) or generated materials. Exporting the index as a Civ V map file for use in the game is permitted user-created content.
- **The cartographic grammar in `docs/cartography.md` is the design contract.** Each system answers one question. The doc lists which earlier features (tides, sea level, roads, towns, time slider, page-count mountains, geysers, arches, canyons, mesas) are designed but not yet ported to the continuous world.
- Missing metadata remains unknown and is drawn neutral, never invented.
- **The map is the application.** No permanent panels: translucent overlays that appear when relevant and fade when idle.
- **Weather is an opt-in radar overlay** (`R`, off by default) with a stated legend. The earlier 3D clouds were hard to read.
- **Files are fields, clustered by kind** (user's direction, 2026-09-29): one cohesive patch per kind, one parcel per file, patches named as a whole until parcels are large. Scattered per-file stamps were illegible. **Water is structure only**: video became canyon country (mesas of banded strata), audio a wetland with pools; lakes, falls and deltas belong to the drainage. Tides and sea level are on hold (user, 2026-09-29).
- Civilization V lessons applied (visual review of store screenshots only): the grid is only an overlay on continuous terrain; features fill their unit and merge with like neighbours; improvements are patchworks; rivers run between units, not through them.
- **Ctrl+P** Bash palette runs only explicitly submitted commands, as the user; output paths become fly-to destinations.

## Repository

- GitHub: `https://github.com/3-mmc/cartografiler` (private). This machine's SSH key is not registered with GitHub; push over HTTPS with the gh token without touching global config: `git -c credential.helper= -c credential.helper='!gh auth git-credential' push`.
- Commits are authored as the user (`git -c user.name=... -c user.email=...`, as in earlier commits); the repository has no local identity configured.

## Validation and constraints

```bash
python3 -m unittest discover -s tests -v
./tools/Godot_v4.7.2-stable_linux.x86_64 --headless --path native --editor --import --quit
./atlas / --smoke --capture /tmp/world.png
./atlas / --smoke --capture /tmp/home.png --enter /home/praetor
```

- Smoke runs need the survey index and render real tiles; allow ~10 s warm. The launcher kills Godot after 300 s in `--smoke` (a GDScript parse error otherwise hangs the run) and on SIGTERM/SIGINT. A force-killed Godot window can stay as an unclosable WSLg ghost; `wsl --shutdown` clears it.
- GDScript's `%` formatting has no `%g`: use `String.num_scientific()` for world coordinates (full double precision).
- Height terms that differ across a border must vanish at the border (b = 0), and nested detail must fade out at its container's border. Violations showed up as 100:1 cliff walls at deep zoom. `test_terrain_has_no_cliffs` guards this.
- Rivers run along borders (valleys between provinces), never straight through interiors; upper courses are clipped to their child until it draws its own network. Draw widths through `Synth.symbol_width` (true width to 6 px, then ∝ zoom^0.28): true-width ancestor rivers became screen-wide bands at deep zoom.
- Coastal heights use `coast_distance`: the smooth signed distance to the coast, looked up through the same warp as `label_at` and scaled by the four-cell land vote (bilinear of the clipped ±0.5 indicator), so it is exactly zero where the map draws the coast. Ridges fade toward the sea. An unwarped, always-positive sea distance plus ridges peaking on the coast made 100:1 walls beside Kino (`test_coast_distance_agrees_with_labels`).
- Labels are placed at their own terrain height (`height_at`), not the view's reference height: on a tilted view they slid onto neighbouring places.
- Terrain never carries building heights: houses are 3D models. Raised lots on wide parcels became pillars.
- Bump `LAYOUT_VERSION` in `world.py` whenever layout output changes; the signature includes it, so cached territories in `layout.sqlite` are recomputed.
- Height terms that are lit must use `GridNoise.smooth`/`smooth_fbm` (C2 B-spline) and B-spline raster sampling (`sea_distance`): bilinear value noise and bilinear distance fields showed as square facets in the hillshade. `GridNoise.value`/`value_noise` stay bilinear because border warps must match between layout (pointwise) and render (grid).
- Parcel heights fade within half a cell of the home district's border (the border field is only cell-accurate), and file sites are kept out of that band (`label_at` gap > 0.6 cell); colours do not fade.
- Interactive survey requests refresh one listing (recursive only if never surveyed). A recursive request from `/` at launch re-crawled everything and starved the UI.
- Don't `pkill -f` with a pattern that appears in your own command line: it kills the calling shell (exit 144).
- `ERR_CANT_OPEN` from `audio_driver_alsa` in windowed runs is WSLg having no ALSA device; harmless.
- Preview/metadata workers are bounded subprocesses. Never execute a file's contents to preview it.
- File operations refuse collisions. Trash is Cartografiler's own recoverable store under `$XDG_DATA_HOME/branch/trash`, not the Windows Recycle Bin. Undo covers moves/renames/trash within a session, not copies or mkdir.
- Cloud tides: `branchfm/cloud.py` reads sync roots from `HKLM\...\Explorer\SyncRootManager`; the survey's Windows worker records `st_file_attributes` per entry (`nodes.attrs`), which is what the continuous-map tides will use. Never hydrate files.
