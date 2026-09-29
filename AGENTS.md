# Branch Atlas — project instructions

## Location and entry points

- Working directory: `/home/praetor/branch` (WSL Debian on the user's Windows machine).
- Main product: a native Godot 3D file manager, launched with `./atlas /path/to/folder`.
- Convenience launchers: `~/.local/bin/branch-atlas` and `~/.local/bin/branch-fm`. Desktop entry: `~/.local/share/applications/branch-atlas.desktop` (starts in `/home/praetor`).
- Terminal companion: `./branch /path/to/folder`; `m` launches the native atlas.
- `python3 -m branchfm.demo` creates explicitly synthetic demo files; `./atlas demo` opens them.
- Do not edit `/home/praetor/AGENTS.md` for this project: it is generated from the shared machine instructions.

## Tools and architecture

- Godot **4.7.2 stable**, official Linux x86_64 binary at `tools/Godot_v4.7.2-stable_linux.x86_64`.
- GDScript native scene/UI/camera in `native/main.gd`; terrain generation in `native/terrain.gd`.
- Python **3.13** on this machine. `branchfm/service.py` launches Godot and a token-authenticated, loopback-only HTTP service on an ephemeral port. Closing Godot stops the service.
- Shared Python filesystem operations: `branchfm/model.py`. Read-only previews: `branchfm/preview.py`. Metadata extraction: `branchfm/atlas.py`.
- Pillow for image dimensions, EXIF, and thumbnails; Poppler `pdfinfo` / `pdftotext` for PDF page counts / text; FFmpeg `ffprobe` for media metadata.
- Python standard-library `unittest` tests in `tests/`; Godot's `--smoke` flow checks native navigation and labels, and can capture a rendered PNG.
- Shell, `rg`, Python scripts, Godot headless checks, and rendered-window screenshots are the development tools. Browser/web research is used for primary documentation and visual references. No sub-agents were used.
- Native runtime runs through WSLg. The current Linux OpenGL context reports **Mesa llvmpipe**, not the GTX 1080; do not claim hardware acceleration was verified.
- Official fonts copied from the system: DejaVu Serif and Serif Italic, with their licence in `native/fonts/LICENSE.txt`.

## Accepted product direction

- The original inspiration is Conrad Barski's spatial branching file browser: https://x.com/lisperati/status/2104681013909893184 .
- User initially chose a terminal app, then explicitly chose the native graphical map as the main application. The terminal is now a companion.
- **Do not build a regular board of equal-sized hex/square tiles.** The user corrected the first prototype: geography should be amorphous, expansive, less cartoonish, and visually informed by **Mach Speed Intercept Playtest's map view**. Civilization V supplied the earlier strategic-camera reference, not a requirement for hex tiles.
- The reference is installed at `E:\SteamLibrary\steamapps\common\Mach Speed Intercept Playtest` (`/mnt/e/SteamLibrary/steamapps/common/Mach Speed Intercept Playtest`), Steam app `3989450`. Official screenshots are on the parent game's page, https://store.steampowered.com/app/3438610/Mach_Speed_Intercept/ . Inspect broad continuous terrain, irregular ridges, muted materials, free camera movement; do not lift game assets.
- Directory hierarchy determines geography. Deeper paths are nested regions; entering pans and zooms into existing geography, backing out restores the surroundings. Preserve explored siblings rather than replacing the map with an unrelated island.
- **The cartographic grammar is in `docs/cartography.md` and is the design contract.** Each system answers one question: climate = mount/filesystem (and write permission), hydrology = directory tree (water flows toward the parent), geology = file type + age (basalt when fresh → granite when old), weather = recent modification activity. Do not let one system borrow another's meaning. Climate is a filesystem fact, never a claim about real-world geography; `T` is a session-only override.
- File metadata modifies bounded landmark characteristics (see the grammar's tables). Missing metadata must remain unknown and be drawn neutral, never invented.
- **The map is the application.** No permanent panels: cartouche, tools, gazetteer, field notes and legend are translucent overlays that appear when relevant and fade when idle (accepted 2026-09-28).
- File/directory names form a cartographic label layer, with all / directories-only / off toggles, zoom-dependent density and collision suppression. The inspector always exposes the real full name.
- **Ctrl+P** opens an explicitly submitted Bash navigation palette. `cd`, `find`, and pipelines yielding paths drive an animated zoom-out / travel / zoom-in journey. Bash runs as the user; it is not a fake command parser. Never execute palette text automatically while it is being typed.
- Preview follows selection (field notes appear only while something is selected); Space enlarges it. The conventional list lives in the collapsible Gazetteer (G); path typing via Ctrl+L or the cartouche trail.

## Validation and constraints

```bash
python3 -m unittest discover -s tests -v
./tools/Godot_v4.7.2-stable_linux.x86_64 --headless --path native --editor --import --quit
./atlas demo --headless --smoke
./atlas demo --smoke --capture /tmp/branch-atlas.png
./atlas demo --smoke --capture /tmp/x.png --focus 'Glacier crossing.mp4'   # close-up of one landform
```

- A GDScript parse error used to hang the smoke run, because its timeout timer lives in the failed script. The launcher now kills Godot after 150 s in `--smoke`, and on SIGTERM/SIGINT. A force-killed Godot window can stay on screen as an unclosable WSLg ghost; `wsl --shutdown` clears it.
- Capture flags for inspection: `--focus NAME`, `--enter SUBFOLDER`, `--time`, `--legend`.
- `ERR_CANT_OPEN` from `audio_driver_alsa` in windowed runs is WSLg having no ALSA device; pre-existing and harmless.
- The demo generator is versioned by `demo/.atlas-demo-v2`; delete `demo/` to regenerate after changing it.

- Sandboxed tools cannot create loopback sockets here; integrated app tests require sandbox escalation. Read-only headless import can emit socket warnings unrelated to script parsing.
- Preview/metadata workers are bounded subprocesses. Never execute a file's contents to preview it.
- File operations refuse collisions. Trash is Branch's own recoverable store under `$XDG_DATA_HOME/branch/trash` (normally `~/.local/share/branch/trash`), not the Windows Recycle Bin. Undo covers moves/renames/trash within a session, not copies or mkdir.
- The map shows up to 600 entries per page; over 120 becomes an archipelago of glyph islands with no per-file metadata. Be explicit about this limit; it is not an unbounded full-disk renderer.
- Cloud tides come from `branchfm/cloud.py`: sync roots from `HKLM\...\Explorer\SyncRootManager` (cached per session), attributes via one `powershell.exe -EncodedCommand` per directory (~1 s). Never hydrate files. Verified 2026-09-28 on OneDrive (C:, two E: business accounts) and Proton Drive.
- Visits (roads) are stored locally in `$XDG_DATA_HOME/branch/visits.json`.
- Child regions sit at `SEAT` (y 0.13) above the parent's ground. Lower seats get buried under the magnified parent terrain; this showed up with large (archipelago) children.
- Never copy proprietary game assets into this project. Inspect the reference for visual principles and generate original geometry/materials.
