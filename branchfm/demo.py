"""Small, explicitly synthetic fixtures for trying the atlas without personal data."""
from pathlib import Path
import math
import os
import shutil
import subprocess
import time
import struct
import wave
import zipfile


def make_pdf(path, pages):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for i in range(pages):
        page_id = len(objects)+1
        kids.append(f'{page_id} 0 R')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 420] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id+1} 0 R >>'.encode())
        text = f'BT /F1 14 Tf 25 360 Td (Cartografiler - synthetic sample) Tj 0 -25 Td (Page {i+1} of {pages}) Tj ET'.encode()
        objects.append(f'<< /Length {len(text)} >>\nstream\n'.encode()+text+b'\nendstream')
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {pages} >>'.encode()
    document = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(document))
        document.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start = len(document)
    document.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: document.extend(f'{offset:010} 00000 n \n'.encode())
    document.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    path.write_bytes(document)


DAY = 86400


def age(path, days):
    """Backdate a fixture so the map shows every rock age, basalt to granite."""
    stamp = time.time()-days*DAY
    os.utime(path,(stamp,stamp))


def create_demo(root: Path):
    root.mkdir(parents=True,exist_ok=True)
    if (root/'.atlas-demo-v3').exists():
        (root/'Expedition log.txt').write_text('Synthetic log, rewritten at every demo launch: a geyser.\n')
        return root
    for name in ('Northern journey','Coastal journey','Research library','Expedition code'):
        (root/name).mkdir(exist_ok=True)
    (root/'About this landscape.md').write_text('# A synthetic landscape\n\nAll files in this folder are generated demonstration fixtures.\nChoose your own folder using the path bar or Choose folder.\n\nCtrl+P opens Bash navigation. Try: find . -iname "*.pdf"\n\nL cycles labels. Z pulls back. Double-click a directory to enter.\n')
    make_pdf(root/'Field guide.pdf',8)
    make_pdf(root/'Mountain atlas.pdf',96)
    make_pdf(root/'Collected journeys.pdf',512)
    make_pdf(root/'Research library'/'Monograph.pdf',180)
    make_pdf(root/'Research library'/'Short paper.pdf',4)
    (root/'Weather observations.csv').write_text('day,temperature,humidity,wind\n'+''.join(f'{i},{12+i%11},{45+i%28},{i%9}\n' for i in range(140)))
    (root/'Expedition code'/'route.py').write_text('"""Synthetic route planning sample."""\n\ndef distance(a, b):\n    return sum((x-y)**2 for x, y in zip(a, b)) ** 0.5\n')
    (root/'Expedition code'/'settings.json').write_text('{"climate":"temperate","labels":true,"camera":"orthographic"}\n')
    with zipfile.ZipFile(root/'Expedition archive.zip','w') as archive:
        archive.writestr('README.txt','Synthetic archive preview fixture.\n')
        archive.writestr('notes/route.txt','Coast to mountain pass.\n')
    with wave.open(str(root/'Mountain spring.wav'),'wb') as audio:
        audio.setparams((2,2,8000,0,'NONE','not compressed'))
        audio.writeframes(b''.join(struct.pack('<hh',int(350*math.sin(i*.2)),int(350*math.sin(i*.3))) for i in range(16000)))
    try:
        from PIL import Image, ImageDraw
        destinations = [(root,'River bend.jpg',1400,800,7),(root,'High pass.jpg',800,1200,1),(root,'Autumn forest.jpg',1200,800,10)]
        for folder in ('Northern journey','Coastal journey'):
            for i,name in enumerate(('Morning light','Trailhead','Ridgeline','Camp','Return journey')):
                destinations.append((root/folder,name+'.jpg',1200 if i%2 else 800,800 if i%2 else 1200,i+5))
        for folder,name,width,height,month in destinations:
            image = Image.new('RGB',(width,height),(155,191,194))
            draw = ImageDraw.Draw(image)
            draw.polygon([(0,height),(0,height*.65),(width*.22,height*.28),(width*.47,height*.62),(width*.7,height*.38),(width,height*.70),(width,height)],fill=(83,115,103))
            draw.polygon([(0,height),(width*.25,height*.66),(width*.48,height*.72),(width*.74,height*.57),(width,height*.78),(width,height)],fill=(47,85,78))
            draw.text((30,30),'SYNTHETIC DEMO - '+name,fill='white')
            exif = Image.Exif()
            exif[36867] = f'2025:{month:02d}:15 09:30:00'
            image.save(folder/name,quality=82,exif=exif)
    except ImportError:
        pass
    # Landforms added with the cartographic grammar (docs/cartography.md).
    (root/'Empty survey').mkdir(exist_ok=True)
    modules = root/'Expedition code'/'node_modules'
    (modules/'left-pad').mkdir(parents=True,exist_ok=True)
    (modules/'left-pad'/'index.js').write_text('// synthetic fixture\nmodule.exports = s => s;\n')
    (root/'Expedition code'/'route-planner.exe').write_bytes(b'MZ'+b'\0'*60+b'synthetic fixture, not a program'+b'\0'*300000)
    (root/'Station log.sqlite').write_bytes(b'SQLite format 3\0'+b'\0'*8176)
    with open(root/'Survey machine.vhdx','wb') as disk:
        disk.truncate(3*1024**3)  # sparse: occupies almost no space
    with zipfile.ZipFile(root/'Research library'/'Field recordings.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for i in range(40):
            archive.writestr(f'transcripts/day-{i:02d}.txt',('Synthetic transcript line.\n'*200))
    if shutil.which('ffmpeg'):
        subprocess.run(['ffmpeg','-v','quiet','-y','-f','lavfi','-i','testsrc=size=1920x1080:rate=10:duration=150',
                        '-c:v','libx264','-preset','ultrafast','-crf','40',str(root/'Glacier crossing.mp4')],check=False,timeout=120)
    for name,days in (('Collected journeys.pdf',1900),('Mountain atlas.pdf',520),('Field guide.pdf',0.2),
                      ('Expedition archive.zip',800),('Mountain spring.wav',3),('Survey machine.vhdx',400),
                      ('Research library/Monograph.pdf',2400),('Research library/Short paper.pdf',40),
                      ('Weather observations.csv',0.5),('River bend.jpg',9),('Autumn forest.jpg',300)):
        if (root/name).exists(): age(root/name,days)
    for folder in ('Northern journey','Coastal journey'):
        for item in (root/folder).iterdir(): age(item,3000)
        age(root/folder,3000)
    # Colorado Plateau forms: sandstone-age books of three lengths.
    canyon = root/'Canyon country'
    canyon.mkdir(exist_ok=True)
    for name,pages in (('Plateau survey.pdf',400),('Butte notes.pdf',40),('Hoodoo sketches.pdf',3)):
        make_pdf(canyon/name,pages)
        age(canyon/name,500)
    for i in range(7):  # six or more subfolders cut a canyon
        (canyon/f'Side canyon {i+1}').mkdir(exist_ok=True)
        (canyon/f'Side canyon {i+1}'/'notes.md').write_text(f'Synthetic side canyon {i+1}.\n')
    # A git repository: a walled town with scaffolding for uncommitted work.
    code = root/'Expedition code'
    if shutil.which('git') and not (code/'.git').exists():
        run = lambda *a: subprocess.run(['git','-C',str(code),*a],capture_output=True,check=False)
        run('init','-q')
        for i in range(12):
            (code/'route.py').write_text(f'"""Synthetic route planning sample, revision {i}."""\n\ndef distance(a, b):\n    return sum((x-y)**2 for x, y in zip(a, b)) ** 0.5\n')
            run('add','route.py','settings.json')
            run('-c','user.name=Cartografiler demo','-c','user.email=demo@example.invalid','commit','-qm',f'Synthetic revision {i}')
        (code/'settings.json').write_text('{"climate":"temperate","labels":true,"camera":"orthographic","draft":true}\n')
    # Symbolic link: a natural arch.
    link = root/'Shortcut to the library'
    if not link.exists(): link.symlink_to('Research library')
    # A giant sequoia: over 40 megapixels.
    try:
        from PIL import Image
        Image.new('RGB',(7000,6000),(70,96,72)).save(root/'Panorama.jpg',quality=60)
        age(root/'Panorama.jpg',30)
    except ImportError:
        pass
    # An archipelago: more than 120 entries.
    photos = root/'Photo archive'
    photos.mkdir(exist_ok=True)
    try:
        from PIL import Image
        for i in range(140):
            Image.new('RGB',(16,12),(60+i%120,90,80)).save(photos/f'frame-{i:03d}.jpg')
    except ImportError:
        pass
    for i in range(110): (photos/f'caption-{i:03d}.txt').write_text(f'Synthetic caption {i}.\n')
    for i in range(50): (photos/f'index-{i:02d}.csv').write_text('frame,exposure\n1,0.5\n')
    for item in photos.iterdir(): age(item,60+hash(item.name)%900)
    for name in ('High pass.jpg','About this landscape.md','Station log.sqlite','Glacier crossing.mp4'):
        if (root/name).exists(): age(root/name,2+hash(name)%20)
    (root/'Expedition log.txt').write_text('Synthetic log, rewritten at every demo launch: a geyser.\n')
    (root/'.atlas-demo-v3').touch()
    return root


if __name__=='__main__':
    print(create_demo(Path(__file__).resolve().parent.parent/'demo'))
