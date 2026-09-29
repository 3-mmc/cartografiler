# Branch Atlas — project instructions

## Location and entry points

- Working directory: `/home/praetor/branch` (WSL Debian on the user's Windows machine).
- Main product: a native Godot file manager showing the **whole filesystem as one continuous world map**, launched with `./atlas [/path/to/start]` (`./atlas /` for the world view).
- Convenience launchers: `~/.local/bin/branch-atlas` and `~/.local/bin/branch-fm`. Desktop entry: `~/.local/share/applications/branch-atlas.desktop` (starts in `/home/praetor`).
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
- **Terrain** `branchfm/tiles.py`: 257² edge-inclusive tiles (float32 heights relative to tile base + RGBA colour + RGBA aux: rain, fog, depth, ridge), rendered in a spawn process pool (`atlas_api.py`). Client streams `/tile`, `/places`, `/at`, `/region`, `/status`.
- **Client** `native/main.gd` + `native/terrain.gdshader`: floating origin (100 render units across the view at any zoom, world coords in doubles), orthographic tilted camera, per-pixel normals from the height texture.
- Shared filesystem operations: `branchfm/model.py`. Previews: `branchfm/preview.py`. Per-file metadata: `branchfm/atlas.py` (not yet wired into the continuous map).
- Python standard-library `unittest` tests in `tests/` (`test_world.py` covers index, layout, border consistency, no-cliffs, tile format, continents).
- Fonts: DejaVu Serif, Serif Italic and Sans (Interface.ttf), with their licence in `native/fonts/LICENSE.txt`.

## Accepted product direction

- The original inspiration is Conrad Barski's spatial branching file browser: https://x.com/lisperati/status/2104681013909893184 .
- **One continuous, already-browsable world built from a persistent index** (accepted 2026-09-29): no per-directory islands dropped onto a map. Features blend into one another as in Civilization V and Mach Speed Intercept.
- **Continents are disks** (user's choice, 2026-09-29): the Linux disk and each drive are separate landmasses across sea; below that, provinces share land borders. Do not turn large folders into archipelagos (tried; it read as cracked tiles).
- **Do not build a regular board of equal-sized hex/square tiles.** Geography is amorphous, expansive, less cartoonish, visually informed by **Mach Speed Intercept**'s map view (continuous satellite-like ground, dark ridges with strong shadows, restrained palette). Reference install: `/mnt/e/SteamLibrary/steamapps/common/Mach Speed Intercept Playtest`; screenshots via the Steam API for app 3438610. Never lift game assets.
- **Civilization V assets:** installed at `/mnt/e/SteamLibrary/steamapps/common/Sid Meier's Civilization V` (FPK archives of DDS textures, plus the SDK). Its EULA licenses the software only "for gameplay" and bars derivative works, so do not load or ship its textures, even for prototyping. Use CC0 sources (Poly Haven, ambientCG) or generated materials. Exporting the index as a Civ V map file for use in the game is permitted user-created content.
- **The cartographic grammar in `docs/cartography.md` is the design contract.** Each system answers one question. The doc lists which earlier features (tides, sea level, roads, towns, time slider, page-count mountains, geysers, arches, canyons, mesas) are designed but not yet ported to the continuous world.
- Missing metadata remains unknown and is drawn neutral, never invented.
- **The map is the application.** No permanent panels: translucent overlays that appear when relevant and fade when idle.
- **Weather is an opt-in radar overlay** (`R`, off by default) with a stated legend. The earlier 3D clouds were hard to read.
- **Ctrl+P** Bash palette runs only explicitly submitted commands, as the user; output paths become fly-to destinations.

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
- Rivers are clipped to their own ground (trunk and lower courses to the home district, upper courses to the child); unclipped ancestor rivers carved screen-wide trenches.
- Interactive survey requests refresh one listing (recursive only if never surveyed). A recursive request from `/` at launch re-crawled everything and starved the UI.
- Don't `pkill -f` with a pattern that appears in your own command line: it kills the calling shell (exit 144).
- `ERR_CANT_OPEN` from `audio_driver_alsa` in windowed runs is WSLg having no ALSA device; harmless.
- Preview/metadata workers are bounded subprocesses. Never execute a file's contents to preview it.
- File operations refuse collisions. Trash is Branch's own recoverable store under `$XDG_DATA_HOME/branch/trash`, not the Windows Recycle Bin. Undo covers moves/renames/trash within a session, not copies or mkdir.
- Cloud tides: `branchfm/cloud.py` reads sync roots from `HKLM\...\Explorer\SyncRootManager`; the survey's Windows worker records `st_file_attributes` per entry (`nodes.attrs`), which is what the continuous-map tides will use. Never hydrate files.
