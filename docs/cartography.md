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
| < 3 years | **Sandstone** | Warm tan, terraced mesa steps |
| ≥ 3 years | **Granite** | Pale, low, broad and rounded: the long worn range |

A newly formed basalt cone is therefore a book you were editing this week. A long,
pale granite range is a big reference text left untouched for years. Bulk (page count)
and age are read independently: height is size, and shape and colour are time.

### Landforms, from file type

| Files | Landform | Metadata → form |
|---|---|---|
| PDF; DOCX/PPTX with a page/slide count | **Mountain / ridge** | pages → height (bounded log); age → rock |
| Other documents, notes, text | **Meadow** | size → extent; unknown page count stays a meadow |
| Images | **Woodland** | pixel count → growth; orientation → shape; EXIF month → foliage |
| Audio | **Lake** | duration → area; channels → ripples |
| Video | **Waterfall** | duration → height; resolution → width |
| CSV / TSV / spreadsheets | **Fields** | rows → length; columns → furrows |
| Source & config | **Settlement** | size → number of buildings |
| Archives | **Glacier** | entries → length; compression → ice density |
| Executables & compiled objects | **Obsidian outcrop** | Rock transformed under heat and pressure; size → spire count |
| Disk images (vhdx, iso, img, qcow2) | **Caldera** | A whole world collapsed into one crater; size → diameter |
| Databases (sqlite, db, mdb) | **Well** | An aquifer: a deep store you draw from |
| Symbolic links | **Signpost** beside the landform | Points somewhere else |
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
- **Legend** (`?` / `K`): this grammar, in the app.
