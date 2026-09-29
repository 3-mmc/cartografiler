# Branch Atlas

A native, local file manager that turns directories into nested landscapes. PDF page counts shape mountain ridges; photographs form woods; audio becomes lakes; videos become waterfalls; tables become fields. The geography is continuous and irregular, with no equal-sized file tiles.

## Run on this machine

```bash
cd /home/praetor/branch
./atlas /home/praetor
./atlas /mnt/e
```

Convenience commands `branch-atlas` and `branch-fm` are installed in `~/.local/bin`. A **Branch Atlas** desktop entry starts at your home directory.

It opens a native **Godot window through WSLg**. This is not a browser app or a packaged Windows `.exe`. Python starts a private, authenticated loopback service and shuts it down when the window closes. No files are uploaded.

For the synthetic demonstration landscape:

```bash
python3 -m branchfm.demo
./atlas demo
```

The lightweight terminal companion is `./branch /path`. Press `?` there for controls; `m` opens that directory in the native atlas.

## Navigate

| Control | Action |
|---|---|
| Click a landmark / file-list row | Select and preview |
| Double-click a directory / Enter | Pan and zoom into its landscape |
| Backspace / Parent | Return to the parent landscape |
| Right or middle drag | Pan |
| Wheel / + / − | Zoom |
| Z | Parent overview / local view |
| L | All names / directory names / no map labels |
| T | Cycle the active directory's climate |
| F | Expand map; press again to restore list and inspector |
| Space | Large preview |
| Ctrl+P | Bash navigation palette |

The path bar accepts Linux paths or Windows drive paths such as `E:\Photos`. Files remain selectable through the list even if a map label is suppressed to avoid overlap. The inspector always contains the full name.

## Bash navigation

Ctrl+P runs explicitly submitted commands in the active directory. Bash is real and runs as your user. Enter or **Run** executes; typing alone does not. Paths in stdout become destinations; selecting one triggers a zoom-out / pan / zoom-in journey. A single destination is chosen automatically.

```bash
cd ../Photos
find . -iname '*.pdf'
find . -type f -print0
rg --files | grep Uzbekistan
printf '%s\n' '/mnt/e/Photos/My trip/photo.jpg'
```

Commands have a 10-second limit, output is limited to 1 MiB, and results to 200 destinations. Newline and NUL-delimited filenames are supported. `-print0` handles filenames containing newlines. Bash startup files are not loaded, so interactive aliases/functions are not available; installed commands on PATH are.

## Reading the map

Directory ancestry creates nested geography. Unvisited directories are survey markers; entering reveals a real miniature landscape in that place, then approaches it. Visited parents and siblings are kept, with two ancestor levels rendered around the current region. Deeper history reappears when returning. Explicit **Go** / **Choose folder** starts a new map.

Climates rotate across siblings and can be cycled manually. Coastlines and climates are illustrative, not inferred geographic locations. Rivers trace directory branches; leaf landscapes also have decorative coastal streams.

| File | Landmark | Metadata variations |
|---|---|---|
| PDF | Irregular ridge | Page count controls bounded logarithmic elevation |
| Images | Vegetation patch | Pixel count controls growth; orientation changes shape/density; EXIF capture month accents foliage |
| Audio | Irregular lake | Duration changes area; channel count adds ripples |
| Video | Waterfall | Duration changes height; resolution changes width |
| CSV / TSV | Fields | Rows affect area; columns create furrows |
| Source / configuration | Settlement | Type-based representation |
| ZIP / archives | Vault | Type-based representation |
| Text / other documents | Meadow | Type-based representation |

Missing metadata uses a neutral landmark; exact known values appear in the inspector. Capture-month accents do not claim to know the hemisphere or actual season. Spreadsheet workbooks receive a field landmark but native Excel row/column metadata is not yet used to size it.

## Previews and file operations

- Text, JSON, CSV/TSV, images, PDF text (first six pages), audio/video metadata, ZIP/TAR listings, DOCX text, PPTX slide text, and raw XLSX values are supported. Image previews use Pillow. Binary files show a short hex view; devices and pipes are not read.
- PDF pages are not yet rasterised in the inspector; scanned PDFs may have no text. Audio/video playback uses **Open externally**. Office previews are extracted text/data, not faithful page layouts.
- Metadata/preview extraction runs in memory- and time-bounded subprocesses. It never executes file contents.
- Rename, new folder, copy, cut/paste, recoverable trash, and undo of moves/renames/trash are available. Existing destinations are refused. Copy and mkdir are not undoable.
- Trash lives in `~/.local/share/branch/trash` (or `$XDG_DATA_HOME/branch/trash`). `recovery.tsv` contains JSON-line recovery records for restoration after restarting; the in-app undo stack lasts for the session. This is separate from the Windows Recycle Bin.

## Scope and dependencies

This is a working prototype. Large directories are mapped in **120-entry pages**, with filtering across the whole directory. It does not recursively scan an entire drive. Geometry is generated from metadata, not proprietary game assets. Label modes, explored geography, and manual climate changes are session-local.

Python 3.11+, Godot 4.x, Pillow (optional image support), Poppler (`pdfinfo`, `pdftotext`), and FFmpeg (`ffprobe`) are used. The official Godot 4.7.2 runtime is installed locally under `tools/`; it is excluded from source control. DejaVu fonts are bundled with their licence.

The current WSLg test renderer reports **Mesa llvmpipe (software OpenGL)**. Hardware acceleration has not been verified. The renderer uses Godot's Compatibility backend.

## Validate

```bash
python3 -m unittest discover -s tests -v
./atlas demo --headless --smoke
./atlas demo --smoke --capture /tmp/branch-atlas.png
```

The smoke flow exercises directory descent/return, retained parent geography, labels, climate changes, Bash path search, animated travel, and optional screenshot capture. Tests use generated fixtures, not personal documents.

## References

- [Conrad Barski's spatial file-browser clip](https://x.com/lisperati/status/2104681013909893184): the branching terminal companion.
- [Mach Speed Intercept](https://store.steampowered.com/app/3438610/Mach_Speed_Intercept/): continuous relief, restrained materials, and free camera movement. Original geometry is generated here.
- [Godot Camera3D](https://docs.godotengine.org/en/stable/classes/class_camera3d.html): orthographic camera.

Project-specific agent instructions, machine paths, and accepted design constraints are in `AGENTS.md`.
