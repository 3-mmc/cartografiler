# The cartographic grammar

Branch Atlas replaces the desktop metaphor with a map. A metaphor is only useful
if it can be *read*, so each natural system answers exactly one question about the
filesystem and never borrows another system's meaning.

| System | Question it answers | Driven by |
|---|---|---|
| **Climate** | *Which part of the machine am I on?* | the mount / filesystem and write permission |
| **Hydrology** | *How is it organised?* | the directory tree |
| **Geology & landforms** | *What is this, how big, and how old?* | file type, metadata, modification time |
| **Weather** | *What is happening now?* | recent modification activity |
| **Sea & tides** | *How much room is left, and what is really here?* | disk usage; cloud-file hydration state |
| **Human geography** | *Where do people act?* | your own visits; git repositories |

Every encoding below is computed from real data. When the data is missing the map
shows a neutral form (a grey, undated outcrop; a survey cairn under fog), never an
invented value. The inspector, labels and file list always carry the true name.

---

## Climate: the mount you stand on

Climate belongs to large-scale geography, so it follows the largest-scale fact about
a path: which filesystem it lives on. Crossing a mount boundary while descending is
crossing into a different climate zone. That boundary is where WSL2's behaviour changes.

| Climate | Filesystem | Reasoning |
|---|---|---|
| Temperate | Linux-native (ext4, btrfs, xfs…): the WSL home | Home soil: fast, ordinary, cultivated. |
| Tropical | Windows volumes over WSL's 9p/drvfs bridge (`/mnt/c`, `/mnt/e`) | Sprawling, overgrown land where downloads and media accumulate; slower across the bridge. |
| Wetland | Network mounts (CIFS/SMB, NFS, sshfs, rclone) | Across the water: reachable, but everything is wetter and slower. |
| Desert | Virtual and ephemeral (tmpfs, proc, sysfs) | Nothing permanent grows here; wiped at every boot. |
| Alpine | Anywhere you cannot write (system directories, `C:\Windows`) | Look, don't build. High, cold, owned by someone else. |

`T` still cycles a region's climate by hand. That is a display override, labelled as
such, and never saved.

## Hydrology: structure is drainage

Water always runs **downhill toward the parent directory.** Every region is a
catchment basin whose river mouth faces `..`. Once learnt, this one rule lets you read
orientation off any view.

- **Trunk river**: each region has one. It rises in the interior and reaches the sea
  at the region's outlet. Its width grows with the number of entries in the region.
- **Tributaries**: each subdirectory is a tributary valley. The stream rises at the
  subdirectory's landmark and joins the trunk. Width follows the logarithm of the
  subdirectory's item count, so large subtrees are visibly larger rivers. Entering a
  subdirectory follows the stream upstream: the child region's own outlet faces the
  direction its tributary flowed.
- **Valleys**: rivers carve V-shaped valleys into the ground mesh, and glaciers sit in
  broad U-shaped troughs. Topography carries the tree even with labels hidden.
- **Dry riverbeds (wadis)**: empty directories are channels with no water.
- **Marshes**: generated or cache directories (`node_modules`, `__pycache__`, `.venv`,
  `build`, `dist`, `target`, `.cache`, `.git`…) become braided, reed-choked
  wetland. Water still flows there, but you rarely wade in on purpose.
- **Lakes**: audio. Still water held in a basin, with area set by duration and ripple
  rings by channel count. Every lake drains: a short outflow creek links it to the trunk.
- **Waterfalls**: video. Moving water falling over a cliff: height from duration,
  width of the fall from resolution. The plunge pool also drains to the trunk.
- **Canyons**: a subdirectory with six or more subfolders cuts a canyon, a deep, narrow
  valley with banded red walls (the Grand Canyon). Branching depth becomes vertical depth.
- **Glaciers**: archives (zip, tar, 7z…). Frozen, compressed water that moves slowly
  and holds material inside. Length comes from the number of entries in the archive;
  ice density (white to deep blue) from the compression ratio. Meltwater runs from the
  toe, because extracting an archive is melting it.

## Geology: substance and age

**Landform = what the file is. Rock = how old it is.**

### Rock, from modification time

Geology is a record of time, so rock type is age, and erosion runs from sharp to worn:

| Last modified | Rock | Form |
|---|---|---|
| < 1 day | **Fresh basalt, still cooling** | Black, jagged, with an ember glow at the summit |
| < 7 days | **Fresh basalt** | Black, steep, sharp-crested |
| < 6 months | **Weathered basalt** | Dark brown-grey, still angular |
| < 3 years | **Sandstone** | Colorado Plateau forms: long documents stand as **mesas** (sheer banded cliffs under a flat caprock), middling ones as **buttes**, short ones weather into **hoodoos** (Bryce Canyon) |
| ≥ 3 years | **Granite** | Pale, low, broad and rounded: the long worn range |

A newly formed basalt cone is therefore a book you were editing this week. A long,
pale granite range is a big reference text left untouched for years. Bulk (page count)
and age are read independently: height is size, and shape and colour are time.

### Landforms, from file type

| Files | Landform | Metadata → form |
|---|---|---|
| PDF; DOCX/PPTX with a page/slide count | **Mountain / ridge** | pages → height (bounded log); age → rock |
| Other documents, notes, text | **Meadow** | size → extent; unknown page count stays a meadow |
| Images | **Woodland** | pixel count → growth; orientation → shape; EXIF month → foliage; ≥ 40 MP → a giant **sequoia** |
| Audio | **Lake** | duration → area; channels → ripples |
| Video | **Waterfall** | duration → height; resolution → width |
| CSV / TSV / spreadsheets | **Fields** | rows → length; columns → furrows |
| Source & config | **Settlement** | size → number of buildings |
| Archives | **Glacier** | entries → length; compression → ice density |
| Executables & compiled objects | **Obsidian outcrop** | Rock transformed under heat and pressure; size → spire count |
| Disk images (vhdx, iso, img, qcow2) | **Caldera** | A whole world collapsed into one crater; size → diameter |
| Databases (sqlite, db, mdb) | **Well** | An aquifer: a deep store you draw from |
| Symbolic links | **Natural arch** beside the landform | A span that leads somewhere else (Arches) |
| Any file changed in the last 15 minutes | **Geyser** beside it | Live thermal activity (Yellowstone): logs being written, files being edited |
| Anything else | **Cairn** | Uncharted |

## Weather: what's happening now

Weather is transient, so it shows transient facts: modification activity, read from
the entries of the current region and from a shallow survey of its subdirectories.

| Conditions | Meaning |
|---|---|
| **Thunderstorm**, dark cloud and lightning | ≥3 changes in the last hour, or ≥12 today |
| **Showers** | changes today; rain density follows the count |
| **Fair-weather cumulus** | changes this week |
| **Clear** | changed this season, quiet this week |
| **Snow cover** | nothing changed for more than two years; the land whitens with dormancy |
| **Fog** | unexplored subdirectories sit under fog that lifts when you enter; unreadable directories stay fogged |

Subdirectories with activity today carry a small rain cloud of their own, so you can
see where work is happening from the parent's altitude before going in. `W` hides
the weather layer.

## Sea level: room left on the disk

The sea stands at a level set by the mount's disk usage. Below 75% full it stays offshore.
Above that it rises until, at a full disk, it laps the ground: coasts drown, river valleys
become fjords, and only landforms stand clear. The cartouche states the percentage and the
free space.

## Tides: cloud-mirrored files

Cloud-sync providers that use the Windows Cloud Files API (OneDrive, Proton Drive, and
others) register their roots in the registry. Branch reads the roots once per session and
each directory's attribute bits with one PowerShell call. **Reading them never downloads
anything.** The sea is the cloud:

| State | Attribute | Form |
|---|---|---|
| Cloud-only placeholder | RECALL_ON_DATA_ACCESS / OFFLINE | **Phantom island**: ghostly and translucent, in sea mist. Charted and named, but not on this disk until opened. (Old sea charts carried reported islands that did not exist.) |
| Always keep on this device | PINNED | **Dike**: a stone ring holding the land against the tide |
| Downloaded, not pinned | neither | **Tidal flat**: wet sand; Windows may reclaim it when space runs low |
| The sync root itself | registered root | **Lighthouse and pier**: the harbour where the cloud comes ashore |

Not yet drawn: sync *activity* (a ferry on a shipping lane) and sync *errors* (a wreck on a
reef). Their status lives in each provider's shell extension, not in file attributes.

## Human geography: where people act

- **Roads** are desire paths. Every folder you enter and file you open in Branch is counted
  locally (`~/.local/share/branch/visits.json`). Routes wear from the island's gate, where
  you arrive from the parent, and widen with use. They cross rivers as fords.
- **Walled towns** are git repositories. Commits are the population (more houses), and
  uncommitted changes stand as scaffolding. Source files outside repositories remain small
  **settlements**.

## Time: replaying the record

`Y` opens a time slider over the current region. Dragging it redraws the map as it stood
on that date: rock is re-aged (today's granite was yesterday's sandstone), and weather is
recomputed. Files whose last change comes later haven't formed yet. **Only
last-modification times exist**, so a file appears at its most recent change, not its
creation. The slider says so.

## Scale: archipelagos

Up to 120 entries share one island. Beyond that, up to 600 per page, a region becomes an
**archipelago**: islands grouped by kind (folders, images, captions…) of at most 90 entries
each, named on the map, with each landmark drawn as a simple glyph. Per-file metadata isn't
fetched at archipelago scale, so forms stay neutral rather than guessed.

---

## Interface: the map is the application

- No permanent panels. The window is the map.
- **Cartouche** (top left): the region's name, the path as a trail of place names, and
  the current climate and weather in one line. Click the trail, or press `Ctrl+L`, to
  type a path.
- **Field notes** (right): appear only when something is selected. They show a preview,
  facts, and a *reading* that states in words why the landform looks as it does.
  Collapsible.
- **Gazetteer** (left, `G` / `Tab`): the conventional list, filter and paging. Hidden
  until asked for.
- **Contextual actions**: Paste appears only while something is being carried; Undo
  appears only after an undoable change.
- **Chrome fades** to a faint outline while the mouse is idle and returns on movement.
  `F` hides everything but the map.
- **Time** (`Y`): the replay slider.
- **Legend** (`?` / `K`): this grammar, in the app.
