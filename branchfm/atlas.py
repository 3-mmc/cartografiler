"""Deterministic file terrain. Coordinates are shared by rendering and navigation."""
from __future__ import annotations

import math
import os
import hashlib
import json
import csv
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .model import size
from .preview import IMAGE

TILE_W, TILE_H = 18, 10
BIOMES = [
    ('folders', 'Passages · folders', 2),
    ('pdf', 'Highlands · PDFs / height = pages', 2),
    ('images', 'Woodland · images', 6),
    ('audio', 'Lake district · audio', 1),
    ('video', 'Waterfalls · video', 3),
    ('tables', 'Farmland · tables', 6),
    ('code', 'Settlements · source code', 5),
    ('archives', 'Glaciers · archives', 4),
    ('binaries', 'Obsidian · executables', 4),
    ('disks', 'Calderas · disk images', 4),
    ('databases', 'Wells · databases', 1),
    ('documents', 'Meadows · documents', 2),
    ('other', 'Uncharted · other files', 4),
]
SOURCE = {'.py','.js','.ts','.tsx','.jsx','.c','.h','.cpp','.rs','.go','.java','.swift','.sh','.css','.html','.json','.yaml','.yml','.toml','.sql','.lisp','.clj','.rb','.php','.lua'}


def biome(path):
    if path.is_dir():
        return 'folders'
    ext = path.suffix.lower()
    if ext=='.pdf':
        return 'pdf'
    if ext in IMAGE or ext in {'.svg','.heic','.avif','.raw','.cr2','.nef'}:
        return 'images'
    if ext in {'.mp3','.flac','.wav','.ogg','.m4a','.aac','.opus','.aiff'}:
        return 'audio'
    if ext in {'.mp4','.mkv','.mov','.avi','.webm','.m4v'}:
        return 'video'
    if ext in {'.csv','.tsv','.xlsx','.xls','.ods','.parquet'}:
        return 'tables'
    if ext in SOURCE or path.name in {'Makefile','Dockerfile'}:
        return 'code'
    if ext in {'.zip','.tar','.gz','.tgz','.bz2','.xz','.7z','.rar','.zst','.jar','.whl'}:
        return 'archives'
    if ext in {'.exe','.dll','.so','.o','.a','.lib','.pyc','.class','.wasm','.msi','.sys','.dylib','.bin','.elf'}:
        return 'binaries'
    if ext in {'.iso','.img','.vhd','.vhdx','.vmdk','.qcow2','.vdi','.dmg'}:
        return 'disks'
    if ext in {'.db','.sqlite','.sqlite3','.mdb','.accdb','.duckdb','.ldb'}:
        return 'databases'
    if ext in {'.md','.txt','.rst','.doc','.docx','.odt','.epub','.rtf','.pptx','.ppt','.odp','.tex'}:
        return 'documents'
    return 'other'


def mountain_height(pages):
    """Logarithmic relief preserves differences from pamphlets to long books."""
    return max(1,min(6,math.ceil(math.log2(max(1,pages)+1)/2)))


CLIMATES = ('temperate', 'alpine', 'tropical', 'desert', 'wetland')
CLIMATE_COLORS = {'temperate':6, 'alpine':5, 'tropical':6, 'desert':2, 'wetland':1}


def climate(path):
    # The hash provides the base climate. World.ensure rotates sibling climates.
    return CLIMATES[int.from_bytes(hashlib.blake2b(str(path).encode(),digest_size=4).digest(),'big')%len(CLIMATES)]


def terrain(kind, facts=None, weather='temperate'):
    facts = facts or {}
    pages = facts.get('pages')
    if kind=='pdf':
        if not isinstance(pages,int):
            return ['       ·','      /?\\','     /   \\','  __/_____\\__']
        height = mountain_height(pages)
        lines = []
        for i in range(height):
            inside = ('*' if weather=='alpine' else '^') if i==0 else ' '* (2*i+1)
            lines.append(' '*(7-i)+'/'+inside+'\\')
        return lines+['  '+ '─'*13]
    if kind=='images':
        width,height = facts.get('width',0),facts.get('height',0)
        growth = min(2,int(math.log2(max(1,width*height/1_000_000)+1)/2))
        tree = {
            'temperate': ['  ♠  ',' /│\\ ','  │  '],
            'alpine': ['  ^  ',' /▲\\ ','  │  '],
            'tropical': [' \\|/ ','--+--','  )  '],
            'desert': [' ╷╷  ',' └┤╷ ','  ├┘ '],
            'wetland': [' ╲♠╱ ','  │  ',' ╱│╲ '],
        }[weather]
        if height>width>0:
            tree = tree[:2]+['  │  ']+tree[2:]
        tree = tree[:-1]+[tree[-1]]*(1+growth)
        copies = 3 if width>height*1.3 and height else 2
        return [(' '+line*copies)[:17] for line in tree]+[' '+('≈' if weather=='wetland' else '·')*15]
    if kind=='audio':
        duration = facts.get('duration',0)
        radius = min(6,3+int(math.log2(max(1,duration/60+1))))
        wave = '≈ ≈' if facts.get('channels',1)>1 else ' ≈ '
        return [' '*3+'.'+'~'*(radius*2)+'.',' '*2+'(~'+wave.center(radius*2-2)+'~)', ' '*3+"'"+'~'*(radius*2)+"'"]
    if kind=='video':
        length = min(6,3+int(math.log2(max(1,facts.get('duration',0)/60+1))))
        wave = '≈'*(3 if facts.get('width',0)>=3840 else 2 if facts.get('width',0)>=1920 else 1)
        return ['    ═══════']+['    '+wave+'│'+wave+'│'+wave for _ in range(length-1)]+['   ~≈≈≈≈≈≈~']
    if kind=='tables':
        rows = min(5,2+int(math.log10(max(1,facts.get('rows',1)))))
        cols = min(5,max(2,facts.get('columns',3)))
        return ['  '+('┬─'*cols)]+['  '+('│·'*cols) for _ in range(rows-1)]+['  '+('┴─'*cols)]
    return {
        'folders': ['      ╭───╮','   ╭──┤   ├──╮','   │  │   │  │',' ··┴──┘   └──┴··'],
        'images': ['    ♠     ♠','  ♠ │  ♠  │ ♠','  │ ♠  │ ♠  │','  ┴─┴──┴─┴──┴─'],
        'audio': ['     .~~~~.','  .~~ ≈  ≈ ~~.','   ~≈   ≈   ~','     ~~~~~~~'],
        'video': ['    ≈≈╮','      ╰≈≈╮','   ╭≈≈≈≈╯','   ╰≈≈≈≈≈≈≈'],
        'tables': ['   ╱┬─┬─┬─╱','  ╱─┼─┼─┼╱',' ╱──┼─┼─╱',' ────────'],
        'code': ['    ⌂    ⌂','  ╱─╲  ╱──╲','  │·│──│··│','  ┴─┴··┴──┴'],
        'archives': ['    ▄▄▄▄▄','  ╱▒▒▒▒▒▒▒╲','  │▒▒ ░ ▒▒│','  └~~~~~~~┘'],
        'binaries': ['    ▲  ▲','   ▐█▌▐█▌ ▲','   ▐█▌▐█▌▐█▌','  ─┴─┴┴─┴┴─┴─'],
        'disks': ['    .───.','  /  ~~~  \\','  \\  ~~~  /','    `───´'],
        'databases': ['     ___','    (   )','    |≈≈≈|','    `───´'],
        'documents': ['       ,','  ,  · │   ,','  │  , │ · │',' ─┴──┴─┴───┴─'],
        'other': ['     . · .','   ·   ?   ·','     · . ·','  · · · · · ·'],
    }[kind]


@dataclass
class Tile:
    index: int
    kind: str
    x: int
    y: int
    color: int


def atlas_layout(entries, width):
    cols = max(1,width//TILE_W)
    tiles, headings = [], []
    groups = {kind: [] for kind,_,_ in BIOMES}
    for index,path in enumerate(entries):
        groups[biome(path)].append(index)
    y = 0
    for kind,title,color in BIOMES:
        indices = groups[kind]
        if not indices:
            continue
        headings.append((y,title,color))
        y += 2
        for n,index in enumerate(indices):
            tiles.append(Tile(index,kind,(n%cols)*TILE_W,y+(n//cols)*TILE_H,color))
        y += math.ceil(len(indices)/cols)*TILE_H+1
    return tiles,headings,y


def neighbour(tiles, index, dx, dy):
    current = next((t for t in tiles if t.index==index),None)
    if current is None:
        return index
    if dx:
        row = [t for t in tiles if t.y==current.y and (t.x-current.x)*dx>0]
        return min(row,key=lambda t:abs(t.x-current.x)).index if row else index
    candidates = [t for t in tiles if (t.y-current.y)*dy>0]
    return min(candidates,key=lambda t:(abs(t.y-current.y),abs(t.x-current.x))).index if candidates else index


def page_count(path):
    if not shutil.which('pdfinfo'):
        return None
    from .preview import command
    result = command(['pdfinfo',str(path)],8192)
    for line in result.splitlines():
        if line.startswith('Pages:'):
            try:
                return int(line.split(':',1)[1].strip())
            except ValueError:
                pass
    return None


# Generated or cache trees: rendered as marsh rather than clear tributaries.
GENERATED = {'node_modules','__pycache__','.cache','cache','build','dist','target','.venv','venv',
             '.tox','.mypy_cache','.pytest_cache','.gradle','.next','tmp','temp','.parcel-cache','obj'}


def directory_facts(path, count_limit=5000, stat_limit=400):
    """Shallow survey of one directory: size of the tributary and its weather."""
    now = time.time()
    facts = {'items':0,'subdirs':0,'changed_hour':0,'changed_day':0,'changed_week':0,'newest':0.0}
    try:
        with os.scandir(path) as scan:
            for entry in scan:
                facts['items'] += 1
                try:
                    facts['subdirs'] += entry.is_dir(follow_symlinks=False) and entry.name!='.git'
                except OSError:
                    pass
                if entry.name=='.git':
                    facts['git'] = True
                if facts['items']>=count_limit:
                    facts['more'] = True
                    break
                if facts['items']>stat_limit:
                    continue
                try:
                    modified = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                facts['newest'] = max(facts['newest'],modified)
                age = now-modified
                facts['changed_hour'] += age<3600
                facts['changed_day'] += age<86400
                facts['changed_week'] += age<7*86400
    except PermissionError:
        return {'readable':False}
    facts['generated'] = path.name in GENERATED
    return facts


def git_facts(path, timeout=1.5):
    """A repository is a town: commits are its population, uncommitted changes its scaffolding."""
    facts = {}
    if not shutil.which('git'):
        return facts
    try:
        count = subprocess.run(['git','-C',str(path),'rev-list','--count','HEAD'],capture_output=True,timeout=timeout)
        if count.returncode==0:
            facts['commits'] = int(count.stdout.strip() or 0)
        status = subprocess.run(['git','-C',str(path),'status','--porcelain','-uno'],capture_output=True,timeout=timeout)
        if status.returncode==0:
            facts['uncommitted'] = len(status.stdout.splitlines())
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return facts


def archive_facts(path, limit=20000):
    """Entry count and compression ratio from the archive index; nothing is extracted."""
    import tarfile
    import zipfile
    name = path.name.lower()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()[:limit]
            unpacked = sum(i.file_size for i in infos)
            packed = sum(i.compress_size for i in infos)
            return {'entries':len(infos),'unpacked':unpacked,'ratio':round(packed/unpacked,4) if unpacked else None}
    if name.endswith(('.tar','.tar.gz','.tgz','.tar.bz2','.tar.xz')):
        entries = unpacked = 0
        with tarfile.open(path) as archive:
            for member in archive:
                entries += 1
                unpacked += member.size
                if entries>=limit: break
        return {'entries':entries,'unpacked':unpacked,'ratio':round(path.stat().st_size/unpacked,4) if unpacked else None}
    return {}


def office_pages(path):
    """DOCX/PPTX record page or slide counts in docProps/app.xml when the author's app saved them."""
    import re
    import zipfile
    with zipfile.ZipFile(path) as archive:
        try:
            text = archive.read('docProps/app.xml')[:65536].decode('utf-8','replace')
        except KeyError:
            return None
    match = re.search(r'<(?:\w+:)?(?:Pages|Slides)>(\d+)<',text)
    return int(match.group(1)) if match else None


def metadata(path):
    kind = biome(path)
    facts = {}
    if kind=='folders':
        return directory_facts(path)
    if kind=='archives':
        return archive_facts(path)
    if path.suffix.lower() in ('.docx','.pptx'):
        pages = office_pages(path)
        return {'pages':pages} if pages else {}
    if kind=='pdf':
        facts['pages'] = page_count(path)
    elif kind=='images':
        from PIL import Image
        with Image.open(path) as image:
            facts.update(width=image.width,height=image.height)
            exif = image.getexif()
            date = exif.get(36867)
            if not date and 34665 in exif:
                date = exif.get_ifd(34665).get(36867)
            if date:
                facts['captured'] = str(date)
    elif kind in ('audio','video') and shutil.which('ffprobe'):
        from .preview import command
        data = json.loads(command(['ffprobe','-v','quiet','-show_entries','format=duration:stream=width,height,channels','-of','json',str(path)],16384))
        try:
            duration = float(data.get('format',{}).get('duration',0))
            if math.isfinite(duration) and duration>=0:
                facts['duration'] = duration
        except (TypeError,ValueError):
            pass
        for stream in data.get('streams',[]):
            facts.update({k:v for k,v in stream.items() if k in ('width','height','channels')})
    elif path.suffix.lower() in ('.csv','.tsv'):
        # Small tables get exact counts. Large ones keep neutral terrain.
        if path.stat().st_size<=2*1024*1024:
            with path.open(encoding='utf-8-sig',errors='replace',newline='') as f:
                rows = csv.reader(f,delimiter='\t' if path.suffix.lower()=='.tsv' else ',')
                count,columns = 0,0
                for row in rows:
                    count += 1
                    columns = max(columns,len(row))
                facts.update(rows=count,columns=columns)
    return facts


def fact_label(kind,facts):
    if kind=='pdf':
        return f'{facts["pages"]:,} pages' if isinstance(facts.get('pages'),int) else 'pages unknown'
    if kind=='images' and 'width' in facts:
        return f'{facts["width"]}×{facts["height"]}'
    if kind in ('audio','video') and 'duration' in facts:
        secs = int(facts['duration'])
        return f'{secs//60}:{secs%60:02d}'
    if kind=='tables' and 'rows' in facts:
        return f'{facts["rows"]}r × {facts["columns"]}c'
    return 'Enter to explore' if kind=='folders' else ''


def seasonal_color(facts,default):
    try:
        month = int(facts['captured'][5:7])
        # Capture-month accents, not a geographic inference about actual weather.
        return 5 if month in (12,1,2) else 1 if month in (3,4,5) else 6 if month in (6,7,8) else 2
    except (KeyError,TypeError,ValueError):
        return default


def scan_metadata(paths,conn):
    """One bounded metadata task per directory, not a recursive disk scan."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
        deadline = time.monotonic()+20
        for path in paths:
            if time.monotonic()>deadline:
                break
            try:
                facts = metadata(path)
            except Exception:
                facts = {}
            conn.send((str(path),facts))
        conn.send(None)
    except (BrokenPipeError,EOFError):
        pass
    finally:
        conn.close()


@dataclass
class Region:
    node: object
    x: float
    y: float
    width: int
    height: int
    weather: str
    scale: float = 1.0
    tiles: list = field(default_factory=list)
    headings: list = field(default_factory=list)
    signature: tuple = ()

    def update(self):
        signature = tuple(self.node.entries)
        if signature!=self.signature or not self.tiles:
            self.tiles,self.headings,height = atlas_layout(self.node.entries,self.width-4)
            self.height = max(14,height+5)
            self.signature = signature


class World:
    """Nested geography: a directory's landscape fits inside its parent's portal.

    Descending changes camera scale, never throws away sibling regions. All world
    coordinates are stable while exploring; explicit refresh can rebuild terrain.
    """
    def __init__(self):
        self.regions = {}
        self.links = []

    def ensure(self,node):
        if node.path in self.regions:
            region = self.regions[node.path]
            region.node = node
            region.update()
            return region
        cols = max(2,min(6,math.ceil(math.sqrt(max(1,len(node.entries))/2))))
        width = cols*TILE_W+4
        anchor = self.regions.get(node.parent.path) if node.parent else None
        child = next((r for r in self.regions.values() if r.node.path.parent==node.path),None)
        region = Region(node,0,0,width,14,climate(node.path))
        region.update()
        if anchor:
            tile = next((t for t in anchor.tiles if anchor.node.entries[t.index]==node.path),None)
            if tile:
                factor = min((TILE_W-3)/region.width,6/region.height)
                region.scale = anchor.scale*factor
                region.x = anchor.x+(tile.x+3+(TILE_W-3-region.width*factor)/2)*anchor.scale
                region.y = anchor.y+(tile.y+4)*anchor.scale
            siblings = [p for p in anchor.node.entries if p.is_dir()]
            ordinal = siblings.index(node.path) if node.path in siblings else 0
            region.weather = CLIMATES[(CLIMATES.index(anchor.weather)+ordinal+1)%len(CLIMATES)]
        elif child:
            tile = next((t for t in region.tiles if node.entries[t.index]==child.node.path),None)
            if tile:
                factor = min((TILE_W-3)/child.width,6/child.height)
                region.scale = child.scale/factor
                region.x = child.x-(tile.x+3+(TILE_W-3-child.width*factor)/2)*region.scale
                region.y = child.y-(tile.y+4)*region.scale
        self.regions[node.path] = region
        if anchor:
            self.links.append((anchor.node.path,node.path))
        elif child:
            self.links.append((node.path,child.node.path))
        return region

    def nearest(self,path,dx,dy):
        current = self.regions[path]
        candidates = [r for r in self.regions.values() if ((r.x-current.x)*dx>0 if dx else (r.y-current.y)*dy>0)]
        if not candidates:
            return current
        return min(candidates,key=lambda r:abs(r.x-current.x)+2*abs(r.y-current.y))
