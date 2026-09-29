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
- Climates rotate among directory regions; forests may become pine, tropical, desert vegetation, or wetland. Climate is illustrative and must not be presented as inferred real-world geography.
- File metadata modifies bounded landmark characteristics: PDF pages → mountain elevation, image dimensions/orientation/EXIF date → vegetation, media duration/resolution/channels → water features, table rows/columns → fields. Missing metadata must remain unknown, never invented.
- File/directory names form a cartographic label layer, with all / directories-only / off toggles, zoom-dependent density and collision suppression. The inspector always exposes the real full name.
- **Ctrl+P** opens an explicitly submitted Bash navigation palette. `cd`, `find`, and pipelines yielding paths drive an animated zoom-out / travel / zoom-in journey. Bash runs as the user; it is not a fake command parser. Never execute palette text automatically while it is being typed.
- Preview follows selection; Space enlarges it. Keep a conventional file list and path navigation alongside the map.

## Validation and constraints

```bash
python3 -m unittest discover -s tests -v
./tools/Godot_v4.7.2-stable_linux.x86_64 --headless --path native --editor --import --quit
./atlas demo --headless --smoke
./atlas demo --smoke --capture /tmp/branch-atlas.png
```

- Sandboxed tools cannot create loopback sockets here; integrated app tests require sandbox escalation. Read-only headless import can emit socket warnings unrelated to script parsing.
- Preview/metadata workers are bounded subprocesses. Never execute a file's contents to preview it.
- File operations refuse collisions. Trash is Branch's own recoverable store under `$XDG_DATA_HOME/branch/trash` (normally `~/.local/share/branch/trash`), not the Windows Recycle Bin. Undo covers moves/renames/trash within a session, not copies or mkdir.
- The initial native prototype maps 120 entries per page, with filters and pagination. Be explicit about this limit; it is not yet an unbounded full-disk renderer.
- Never copy proprietary game assets into this project. Inspect the reference for visual principles and generate original geometry/materials.
