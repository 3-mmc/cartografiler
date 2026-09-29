"""Bounded, read-only preview handlers. No file content is executed."""
from __future__ import annotations

import csv
import io
import json
import shutil
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .model import clean, size

LIMIT = 256 * 1024
IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.ico'}
MEDIA = {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac'}


@dataclass
class Preview:
    kind: str
    lines: list[str] = field(default_factory=list)
    pixels: list = field(default_factory=list)


def command(args, limit=LIMIT):
    # Capture to disk rather than an unbounded pipe. Tools also receive bounded output
    # arguments (PDF first 6 pages; ffprobe metadata only).
    import tempfile
    with tempfile.TemporaryFile() as out:
        subprocess.run(args, stdout=out, stderr=subprocess.DEVNULL, timeout=5, check=True)
        out.seek(0)
        return out.read(limit).decode('utf-8', errors='replace')


def xml_text(archive, name):
    info = archive.getinfo(name)
    if info.file_size > 2*1024*1024:
        return '[XML part exceeds the 2 MiB preview limit]'
    root = ET.fromstring(archive.read(name))
    return '\n'.join(' '.join(e.itertext()) for e in root.iter()
                     if e.tag.rsplit('}', 1)[-1] in ('p', 'row'))


def image_preview(path):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return Preview('Image', ['Install Pillow for an in-terminal image preview.', 'Press o to open externally.'])
    with Image.open(path) as im:
        w, h = im.size
        if w*h > 25_000_000:
            return Preview('Image', [f'{w} × {h}', 'Image exceeds the 25 megapixel preview limit.', 'Press o to open externally.'])
        im = ImageOps.exif_transpose(im)
        im.thumbnail((72, 64))
        rgba = im.convert('RGBA')
        bg = Image.new('RGBA', im.size, (24, 24, 24, 255))
        bg.alpha_composite(rgba)
        rgb = bg.convert('RGB')
        pixels = [[rgb.getpixel((x, y)) for x in range(rgb.width)] for y in range(rgb.height)]
        return Preview('Image', [f'{w} × {h} pixels', 'First frame · press o for the original'], pixels)


def preview(path: Path) -> Preview:
    try:
        s = path.stat()
        meta = [size(s.st_size), datetime.fromtimestamp(s.st_mtime).strftime('Modified %Y-%m-%d %H:%M'), '']
        if stat.S_ISDIR(s.st_mode):
            entries = []
            # Do not recurse or stat every item simply to preview a directory.
            import os
            with os.scandir(path) as scan:
                for i, entry in enumerate(scan):
                    if i >= 200:
                        entries.append('… first 200 entries only')
                        break
                    entries.append(entry.name+('/' if entry.is_dir(follow_symlinks=False) else ''))
            return Preview('Folder', meta + sorted(entries, key=str.casefold))
        if not stat.S_ISREG(s.st_mode):
            return Preview('Special file', ['Devices, sockets, and pipes are not read for previews.'])
        ext = path.suffix.lower()
        if ext in IMAGE:
            return image_preview(path)
        if ext == '.pdf':
            if not shutil.which('pdftotext'):
                return Preview('PDF', meta+['Install poppler-utils for PDF text previews.', 'Press o to view the document externally.'])
            text = command(['pdftotext', '-f', '1', '-l', '6', '-layout', str(path), '-'])
            return Preview('PDF · first 6 pages', meta+(text.splitlines() or ['No extractable text (possibly a scan). Press o to view.']))
        if ext in MEDIA:
            if not shutil.which('ffprobe'):
                return Preview('Media', meta+['Install ffmpeg for media metadata.', 'Press o to play externally.'])
            data = json.loads(command(['ffprobe', '-v', 'quiet', '-show_format', '-show_streams', '-of', 'json', str(path)]))
            lines = ['Press o to play in your default application.', '']
            fmt = data.get('format', {})
            lines += [f'Duration: {fmt.get("duration", "unknown")} seconds', f'Format: {fmt.get("format_long_name", "unknown")}']
            for stream in data.get('streams', []):
                lines.append(f'{stream.get("codec_type", "stream")}: {stream.get("codec_name", "unknown")}')
                if 'width' in stream:
                    lines.append(f'  {stream["width"]} × {stream["height"]}')
                if 'sample_rate' in stream:
                    lines.append(f'  {stream["sample_rate"]} Hz · {stream.get("channels", "?")} channels')
            lines += [f'{k}: {v}' for k, v in fmt.get('tags', {}).items()]
            return Preview('Media', meta+lines)
        if ext in {'.zip', '.docx', '.xlsx', '.pptx', '.epub', '.odt'}:
            # Bound archive central directory work by file size.
            if s.st_size > 100*1024*1024:
                return Preview('Archive / document', meta+['Archive exceeds the 100 MiB preview limit.'])
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if ext == '.docx':
                    return Preview('Word · text', meta+xml_text(z, 'word/document.xml').splitlines())
                if ext == '.odt':
                    return Preview('OpenDocument · text', meta+xml_text(z, 'content.xml').splitlines())
                if ext == '.pptx':
                    slides = sorted(n for n in names if n.startswith('ppt/slides/slide') and n.endswith('.xml'))[:20]
                    return Preview('PowerPoint · slide text', meta+[line for n in slides for line in [n, xml_text(z, n), '']])
                if ext == '.xlsx':
                    sheets = [n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')]
                    # Shared strings are shown separately; avoid pretending this is a faithful grid.
                    lines = ['Raw worksheet values (styles and formulas are not evaluated).', '']
                    if 'xl/sharedStrings.xml' in names:
                        info = z.getinfo('xl/sharedStrings.xml')
                        if info.file_size <= 2*1024*1024:
                            root = ET.fromstring(z.read(info))
                            lines += ['Shared strings:'] + [f'{i}: {"".join(e.itertext())}' for i, e in enumerate(root)][:200]
                    for n in sheets[:3]:
                        lines += ['', n, xml_text(z, n)]
                    return Preview('Excel · raw values', meta+lines)
                return Preview('Archive contents', meta+[f'{size(i.file_size):>10}  {i.filename}' for i in z.infolist()[:500]]+(['… first 500 entries only'] if len(names)>500 else []))
        if ext in {'.tar', '.tgz', '.gz', '.bz2', '.xz'}:
            # Stream only plain tar files. Compressed streams can be arbitrarily costly.
            if ext != '.tar':
                return Preview('Compressed file', meta+['Compressed-stream preview is not enabled.', 'Press o to open externally.'])
            lines = []
            with tarfile.open(path, 'r:') as t:
                for i, member in enumerate(t):
                    if i == 500:
                        lines.append('… first 500 entries only')
                        break
                    lines.append(f'{size(member.size):>10}  {member.name}')
            return Preview('Tar contents', meta+lines)
        with path.open('rb') as f:
            raw = f.read(LIMIT)
        if b'\0' in raw[:8192] and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            lines = ['Binary file · first 256 bytes', '']
            for i in range(0, min(256, len(raw)), 16):
                b = raw[i:i+16]
                lines.append(f'{i:08x}  {b.hex(" ")}  '+''.join(chr(c) if 32<=c<127 else '.' for c in b))
            return Preview('Hex', meta+lines)
        text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig', errors='replace')
        kind = 'Text'
        if ext == '.json' and s.st_size <= LIMIT:
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
                kind = 'JSON'
            except ValueError:
                pass
        if ext in {'.csv', '.tsv'}:
            rows = list(csv.reader(io.StringIO(text), delimiter='\t' if ext=='.tsv' else ','))[:100]
            text = '\n'.join(' │ '.join(c[:80] for c in r[:12]) for r in rows)
            kind = 'Table · first 100 rows'
        lines = text.expandtabs(4).splitlines()[:3000]
        if s.st_size > LIMIT:
            lines.append('… preview limited to the first 256 KiB')
        return Preview(kind, meta+lines)
    except Exception as e:
        return Preview('Preview unavailable', [clean(e), 'Press o to open externally.'])
