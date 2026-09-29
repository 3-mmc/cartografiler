# The cartographic grammar

Branch Atlas replaces the desktop metaphor with a map. A metaphor is only useful
if it can be *read*, so each natural system answers exactly one question about the
filesystem and never borrows another system's meaning.

| System | Question it answers | Driven by |
|---|---|---|
| **Continents** | *Which disk is this?* | the mount |
| **Climate** | *What kind of ground, and may I build here?* | filesystem type and write permission |
| **Territory & hydrology** | *How is it organised, and how big is it?* | the directory tree and subtree sizes |
| **Land cover & landforms** | *What is here?* | file kinds, one landmark per file up close |
| **Geology** | *How old is it?* | modification time |
| **Weather** | *What is happening now?* | files changed today |

Every encoding is computed from real data. When the data is missing the map shows a
neutral form (parchment for terra incognita, a cairn for an unknown kind), never an
invented value. Labels, field notes and the gazetteer always carry the true name.

---

## One world, surveyed

The whole filesystem is **one continuous map**, drawn from a persistent survey index
(`~/.local/share/branch/index.sqlite`) rather than from whatever folder you open.

- **The survey** crawls breadth-first, coarse to fine, so the whole map exists early and
  gains detail. Each filesystem is crawled natively: Linux paths by `os.scandir` in WSL,
  Windows drives by a Windows Python worker. Measured here, 9p manages about 1k
  entries/s, while native NTFS reaches about 70k/s on D: and 7k/s on the E: hard disk.
  A full survey of this machine (3.65 M files, 461 k folders, every drive) took 16
  minutes. It refreshes in the background every 12 hours; the place you look at is
  re-listed first. Streamed cloud drives (Google Drive) go last and time out politely.
- **Terra incognita.** Places the survey hasn't reached are parchment with survey
  hatching. They fill in while you watch.
- **Territory.** Every folder owns land in proportion to what it holds (compressed:
  size^0.4, with a floor, so a 300 k-file tree can't swallow its 50-file siblings). The
  division is a weighted Voronoi (power diagram), seeded by path hashes, so a growing
  index moves borders gently instead of reshuffling the map. A folder's own files stand
  as landmarks in its **home district**.
- **Borders are fractal at every zoom.** Near a border the lookup point is displaced by
  a multi-octave warp, from ten raster cells down to two screen pixels. Coasts and
  provincial borders gain detail as you descend, the way real ones do.
- **Level of detail.** A place shows its insides once it is about 24 px across, fading
  in until about 90 px. Its detail also fades to nothing at its own border, so nothing
  nested can raise a wall at an edge. Places under 5 px merge into their parent's
  land cover.

## Continents are disks

The Linux (WSL) disk is one continent; each Windows drive (C:, D:, E:…) and each
cloud drive is another, across open sea. `/mnt` itself folds away: its drives sit
directly on the world. Inside a disk everything is **one landmass**. Its provinces share
land borders along ridgelines, and nothing below the continents is split by water.

## Climate: the ground you stand on

| Climate | Filesystem | Reasoning |
|---|---|---|
| Temperate | Linux-native (ext4, btrfs, xfs…) | Home soil: fast, ordinary, cultivated. |
| Tropical | Windows volumes over WSL's 9p bridge | Sprawling land where downloads and media accumulate. |
| Wetland | Network mounts (CIFS/SMB, NFS, sshfs, rclone) | Reachable, but wetter and slower. |
| Desert | Virtual and ephemeral (tmpfs, proc, sysfs) | Nothing permanent grows; wiped at every boot. |
| Alpine | Anywhere you cannot write (`/usr`, `C:\Windows`) | Look, don't build. High and owned by someone else. |

## Hydrology: structure is drainage

Water always runs **downhill toward the parent folder**. Each territory drains to one outlet
(for a disk, its coast). A least-cost tree is grown from there over the land: travel along
the borders between provinces and through the home district is cheap, across a province's
interior dear. So rivers run **in the valleys between places**, the way Civilization V runs
its rivers along tile edges, not through tiles, and ridgelines part where they pass.

- **Tributaries and confluences.** Every subfolder's water leaves at its outlet (the point
  of its border nearest the drainage) and follows the tree, so streams from neighbouring
  folders meet and carry on as one.
- **Width is flow.** A stream is as wide as the share of the territory it drains
  (width ∝ √flow). A place's own trunk ends as wide as the parent's stream begins, so rivers
  are continuous from a leaf folder to the sea. On screen, rivers are drawn at true width
  until 6 px, then grow sublinearly as you zoom (like a map symbol): a great river stays a
  river when you zoom into a town on its bank.
- **Lakes** pool at the capital of a hub, a folder with six or more subfolders, where many
  streams meet.
- **Waterfalls** mark where a river crosses onto other ground: a different filesystem, or
  the edge of what you may write (the river falls from your home into `/home`).
- **Deltas.** Each disk's great river reaches the sea through a lobed fan of marsh and sand,
  threaded by distributaries, with a turbid plume offshore.

## Land cover and landforms: what is here

From orbit, land cover is a patchwork of what a place holds, each kind winning ground in
proportion to its share. Cover patches use world-absolute noise per cover, so a forest on
one side of a border carries on across it when the neighbour grows forest too:

| Content | Cover |
|---|---|
| Images | Forest |
| Video | Canyon country |
| Tables | Fields |
| Source code, databases | Towns |
| PDFs, documents | Meadow |
| Audio | Wetland |
| Archives | Ice |
| Executables, disk images | Bare rock |
| Anything else | Scrub |

### Fields: a folder's own files

A folder's own files lie in its home district **as fields**, the way Civilization V fills
a farm tile with a patchwork of crops and a forest tile with one canopy that runs into the
next. Each **kind forms one patch**; each **file is one parcel** of its patch.

- **Cohesion.** A patch sits on the side facing the subfolder that holds most of that kind,
  so a folder's photographs grow into the same forest as its photo-filled subfolder.
- **Order.** Parcels are relaxed to even sizes and run **alphabetically** across the patch.
- **Level of detail.** Far away a patch is a single cover. Closer, it divides into parcels,
  and closer still each parcel shows its own form. Labels follow: a patch is named as a
  whole ("140 images") until its parcels are large, then a handful of its largest files are
  named.

| Kind | Patch | Parcel |
|---|---|---|
| PDF | a massif | a peak; rock by age, snow when dormant |
| Images | a forest | a stand of its own tone, crowns up close |
| Video | canyon country | a mesa of banded strata: film is banded in frames; a large file is a broad, tall mesa, a small one a butte |
| Audio | wetland | a reed bed with standing pools |
| Tables | a field system | a field of one crop, furrows in its own direction, hedgerows between |
| Source code | a town | a city block with streets between and roofs up close |
| Databases | a town | the same, with slate-blue roofs |
| Archives | a glacier | a tongue of ice, crevassed at its edges |
| Executables | obsidian | a dark volcanic flow |
| Disk images | calderas | a crater with its lake |
| Documents | meadow | meadow in flower, dry-stone walls between |
| Anything else | scrub | scrub with a cairn |

Landmarks (peaks, mesas, calderas) keep to the size they would have among eight
neighbours, so a file in a sparse folder owns a wide parcel, not a giant mountain. Video is
no longer water: water is structure (drainage), and a landform that means "content" must not
borrow it.

## Geology: age

Ridgelines take the rock of their region's age: dark **basalt** when changed in the
last week, weathered basalt within six months, **sandstone** within three years, pale
**granite** beyond. Regions untouched for more than two years are **snowbound**.

## Weather: the radar

Weather is transient, so it shows transient facts. The **radar** (`R`, off by default)
paints the cells where files changed today, drawn as rain radar: green for a few, yellow
for many, red for hundreds. It is an overlay you switch on to ask a question. It never
stands in for the map.

---

## Designed in the earlier nested map, not yet in the continuous world

These were built in the previous version, which drew one directory at a time. They
return as the continuous map matures. The design is unchanged, only the drawing is
pending:

- **Page counts as mountain height.** PDF and DOCX page counts, image dimensions,
  audio and video durations, and table sizes from per-file metadata. Parcels currently
  use kind, size and age only; metadata extraction runs per file and needs a background
  pass of its own.
- **Tides** (on hold): cloud-only files as phantom islands, pinned files behind dikes,
  downloaded ones on tidal flats, lighthouses at sync roots. The survey already records the
  Windows attribute bits this needs.
- **Sea level** (on hold) from disk usage, rising to flood a continent's coasts as its disk fills.
- **Roads** worn by your own visits (still recorded in `visits.json`), and **walled towns**
  for git repositories.
- **Time slider**: replaying last-modification times.
- **Geysers** for files changed in the last 15 minutes, **arches** for symlinks, and
  **canyons** for deeply branching folders.

## Materials

Ground detail comes from CC0 aerial and ground textures (Poly Haven; see
`native/textures/LICENSE.md`), baked by `tools/fetch_textures.py` into normal and
luminance-detail maps. They never set colour: the synthesised colour carries meaning, the
texture only grain and relief. Each tile carries per-pixel weights for eight materials
(meadow, forest, field, town, wet, snow, rock, sand), and the shader samples each at two
world-anchored scales an octave apart, cross-faded with zoom, so the grain looks the same
at every height.

---

## Interface: the map is the application

- No permanent panels. The window is the map.
- **Moving:** drag to pan, wheel to zoom at the cursor, and right-drag to turn and tilt.
  Double-click flies to a place; Backspace goes up. Home shows the whole world, and
  WASD/arrows pan. Flights rise high enough to see both ends, then descend.
- **Cartouche** (top left): the place under the centre of the view, its trail of place
  names, its size and activity, and the survey's progress. `Ctrl+L` types a path.
- **Field notes** (right): appear only when something is selected, with a preview and a
  *reading* that says in words why the land looks as it does.
- **Gazetteer** (`G`): the contents of the place you are over, as a list.
- **Bash** (`Ctrl+P`): output paths become destinations to fly to.
- **Contextual actions**: Paste appears only while something is being carried and lands
  in the place you are over; Undo appears only after an undoable change.
- **Chrome fades** while the mouse is idle; `F` hides everything but the map.
- **Legend** (`K`): this grammar, in the app.
