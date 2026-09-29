"""Local authenticated data service shared by the native atlas and terminal tools."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .atlas import biome, directory_facts, git_facts, metadata
from . import cloud, visits
from .model import Operations, size
from .preview import preview


def _extract(path, kind, conn):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,384*1024*1024))
        result = asdict(preview(path)) if kind=='preview' else metadata(path)
        if kind=='preview' and result.get('pixels'):
            import base64
            import io
            from PIL import Image, ImageOps
            with Image.open(path) as source:
                source = ImageOps.exif_transpose(source)
                source.thumbnail((1024,768))
                image = source.convert('RGBA')
                buffer = io.BytesIO()
                image.save(buffer,format='PNG')
                result['image_png'] = base64.b64encode(buffer.getvalue()).decode()
        conn.send(result)
    except Exception as e:
        conn.send({'error':str(e)})
    finally:
        conn.close()


def bounded_extract(path, kind):
    # Spawn is safe from the threaded HTTP server; no inherited locks or sockets.
    context = mp.get_context('spawn')
    receiver,sender = context.Pipe(duplex=False)
    process = context.Process(target=_extract,args=(path,kind,sender),daemon=True)
    process.start()
    sender.close()
    try:
        if receiver.poll(7):
            try:
                return receiver.recv()
            except EOFError:
                return {'error':'Preview worker exited without a result.'}
        return {'error':'Metadata / preview timed out.'}
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=.2)
        if process.is_alive():
            process.kill()
            process.join(timeout=.2)
        receiver.close()
        process.close()


class Service(ThreadingHTTPServer):
    daemon_threads = True
    # The client opens a connection per request: MAX_INFLIGHT tiles at once, plus status,
    # places and whatever the pointer is asking about. socketserver's default backlog of 5 is
    # smaller than that, and a connection that is never accepted is not refused either — the
    # client simply waits out its twenty seconds and reports no response, which is what made
    # picking "fail" on cold ground.
    request_queue_size = 128

    def __init__(self, start, atlas=False):
        super().__init__(('127.0.0.1',0),Handler)
        self.token = secrets.token_urlsafe(32)
        self.start = str(start)
        self.ops = Operations(Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'branch/trash')
        self.mutation_lock = threading.Lock()
        self.extract_slots = threading.Semaphore(3)
        self.cache = {}
        self.visits = Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'branch/visits.json'
        self.atlas = None
        if atlas:
            from .atlas_api import Atlas
            self.atlas = Atlas(str(start))


FS_CLIMATE = {'9p':'windows','drvfs':'windows','v9fs':'windows','cifs':'network','smb3':'network','nfs':'network',
              'nfs4':'network','fuse.sshfs':'network','fuse.rclone':'network','tmpfs':'ephemeral','ramfs':'ephemeral',
              'proc':'ephemeral','sysfs':'ephemeral','devtmpfs':'ephemeral','cgroup2':'ephemeral','debugfs':'ephemeral'}


_mounts = (0.0, [])


def mount_table():
    global _mounts
    if time.monotonic()-_mounts[0] > 30:
        table = []
        try:
            with open('/proc/mounts',encoding='utf-8',errors='replace') as mounts:
                for line in mounts:
                    parts = line.split()
                    if len(parts)>=3:
                        table.append((parts[1].replace('\\040',' '),parts[2]))
        except OSError:
            pass
        _mounts = (time.monotonic(),table)
    return _mounts[1]


def filesystem(path):
    """The mount containing path: its type decides the region's climate."""
    best = ('/','unknown')
    for mount, kind in mount_table():
        inside = str(path)==mount or str(path).startswith(mount.rstrip('/')+'/')
        if inside and len(mount)>=len(best[0]):
            best = (mount,kind)
    facts = {'mount':best[0],'type':best[1],'zone':FS_CLIMATE.get(best[1],'native'),'writable':os.access(path,os.W_OK)}
    try:
        usage = shutil.disk_usage(path)
        # Sea level: a filling disk floods its coastline.
        facts.update(total=usage.total,free=usage.free,used=round(1-usage.free/usage.total,4) if usage.total else None)
    except OSError:
        pass
    return facts


def survey(paths, budget=3.0):
    """Shallow facts for a region's subdirectories, within a time budget. Directories not
    reached in time are simply absent: the map shows them as unsurveyed, not as empty."""
    deadline = time.monotonic()+budget
    facts = {}
    for raw in paths[:200]:
        if time.monotonic()>deadline:
            break
        path = Path(os.path.abspath(str(raw)))
        if path.is_dir():
            facts[str(path)] = directory_facts(path)
            if facts[str(path)].get('git') and time.monotonic()<deadline:
                facts[str(path)].update(git_facts(path))
    return facts


def bash_destinations(command: str, cwd: Path):
    """Execute only explicit palette submissions, then interpret output as paths."""
    import signal
    import tempfile
    if not command.strip() or len(command)>8192:
        raise ValueError('Enter a Bash command of at most 8192 characters.')
    if not cwd.is_dir():
        raise ValueError('The working directory is unavailable.')
    marker = '\0BRANCH_'+secrets.token_hex(12)+'\0'
    # The command is deliberately shell code supplied by the user. The cwd/status
    # trailer is constant Bash syntax; paths are never interpolated into the script.
    trailer = "\nbranch_result=$?\nprintf '"+marker.replace('\0','\\0')+"'\nprintf '%s\\0%s' \"$PWD\" \"$branch_result\"\n"
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(['bash','--noprofile','--norc','-c',command+trailer],cwd=cwd,stdout=out,stderr=err,start_new_session=True)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait()
            raise ValueError('Command exceeded 10 seconds and was stopped.')
        out.seek(0,2)
        length = out.tell()
        out.seek(0)
        output = out.read(1024*1024)
        err.seek(0)
        errors = err.read(4096).decode(errors='replace')
    if length>1024*1024:
        raise ValueError('Command output exceeds 1 MiB. Narrow the search.')
    marker_bytes = marker.encode()
    final_cwd = cwd
    exit_code = process.returncode
    if marker_bytes in output:
        output,trailer_data = output.rsplit(marker_bytes,1)
        parts = trailer_data.split(b'\0')
        final_cwd = Path(os.fsdecode(parts[0]))
        if len(parts)>1:
            try: exit_code = int(parts[1])
            except ValueError: pass
    if exit_code:
        raise ValueError(errors.strip() or f'Command exited with status {exit_code}.')
    parts = output.split(b'\0') if b'\0' in output else output.splitlines()
    destinations = []
    seen = set()
    for raw in parts:
        if not raw: continue
        name = os.fsdecode(raw)
        path = Path(name).expanduser()
        if not path.is_absolute(): path = final_cwd/path
        path = Path(os.path.abspath(path))
        if path.exists() and str(path) not in seen:
            seen.add(str(path))
            destinations.append({'path':str(path),'name':path.name,'directory':path.is_dir()})
        if len(destinations)>=200: break
    if not destinations and not output.strip() and final_cwd!=cwd:
        destinations = [{'path':str(final_cwd),'name':final_cwd.name,'directory':True}]
    return {'results':destinations,'cwd':str(final_cwd),'stderr':errors,'truncated':len(destinations)>=200}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def reply(self,status,data):
        body = json.dumps(data,ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def world_request(self, endpoint, query):
        atlas = self.server.atlas
        if atlas is None:
            return self.reply(404,{'error':'The world map is not enabled in this service.'})
        q = lambda k, d=None: query.get(k,[d])[0]
        if endpoint=='/tile':
            body = atlas.tile(int(q('l')),int(q('x')),int(q('y')))
            self.send_response(200)
            self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):
                pass
            return
        if endpoint=='/places':
            return self.reply(200,atlas.places(float(q('x0')),float(q('y0')),float(q('x1')),float(q('y1')),float(q('px'))))
        if endpoint=='/at':
            return self.reply(200,atlas.at(float(q('x')),float(q('y')),float(q('px'))))
        if endpoint=='/region':
            return self.reply(200,atlas.region(q('path','/')))
        return self.reply(200,atlas.status(int(q('since','0'))))

    def authorized(self):
        return secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+self.server.token)

    def do_GET(self):
        if os.environ.get('BRANCH_TRACE'):
            import time as _t
            _t0 = _t.perf_counter()
            try:
                return self._do_GET_traced(_t0)
            finally:
                pass
        return self._do_GET()

    def _do_GET_traced(self, t0):
        import time as _t
        path = self.path.split('?')[0]
        print(f'[trace] -> {path}', flush=True)
        try:
            return self._do_GET()
        finally:
            print(f'[trace] <- {path} {(_t.perf_counter()-t0)*1000:.0f} ms', flush=True)

    def _do_GET(self):
        if not self.authorized():
            return self.reply(403,{'error':'Authentication required.'})
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ('/tile','/places','/at','/region','/status'):
                return self.world_request(url.path, query)
            path = Path(os.path.abspath(Path(query.get('path',[self.server.start])[0]).expanduser()))
            if url.path=='/list':
                hidden = query.get('hidden',['false'])[0]=='true'
                needle = query.get('filter',[''])[0].casefold()
                focus = query.get('focus',[''])[0]
                entries = []
                with os.scandir(path) as scan:
                    for entry in scan:
                        if entry.path==focus or ((hidden or not entry.name.startswith('.')) and needle in entry.name.casefold()):
                            p = Path(entry.path)
                            try:
                                s = entry.stat()
                                is_dir = entry.is_dir()
                                entries.append({'name':entry.name,'path':str(p),'directory':is_dir,
                                                'kind':biome(p),'bytes':s.st_size,'size':size(s.st_size),
                                                'modified':s.st_mtime,'symlink':entry.is_symlink()})
                            except OSError:
                                entries.append({'name':entry.name,'path':str(p),'directory':False,'kind':'other','bytes':0,'size':'Unavailable','symlink':entry.is_symlink()})
                entries.sort(key=lambda e:(not e['directory'],e['name'].casefold()))
                # One map page is bounded; all names remain searchable via the filter.
                page = max(0,int(query.get('page',['0'])[0]))
                page_size = max(1,min(1000,int(query.get('page_size',['120'])[0])))
                if focus:
                    match = next((i for i,e in enumerate(entries) if e['path']==focus),None)
                    if match is not None: page = match//page_size
                page = min(page,max(0,(len(entries)-1)//page_size))
                shown = entries[page*page_size:(page+1)*page_size]
                extra = {'visits':visits.counts(self.server.visits,[e['path'] for e in shown])}
                if query.get('cloud',['true'])[0]=='true':
                    tides = cloud.directory_tides(path)
                    if tides:
                        for e in shown:
                            e['tide'] = tides['entries'].get(e['name'])
                        extra['cloud'] = {'provider':tides['provider'],'root':tides['root']}
                    roots = {root for root,_ in cloud.sync_roots()}
                    for e in shown:
                        if e['path'] in roots: e['sync_root'] = True
                if (path/'.git').exists():
                    extra['git'] = git_facts(path)
                return self.reply(200,{**extra,'path':str(path),'parent':str(path.parent),'filesystem':filesystem(path),'entries':shown,
                                       'total':len(entries),'page':page,'pages':max(1,(len(entries)+page_size-1)//page_size)})
            if url.path=='/metadata' and path.is_dir():
                # A shallow scandir; no file contents are read, so no worker process.
                return self.reply(200,directory_facts(path))
            if url.path in ('/preview','/metadata'):
                stat = path.stat()
                kind = url.path[1:]
                key = (str(path),stat.st_mtime_ns,stat.st_size,kind)
                if key not in self.server.cache:
                    with self.server.extract_slots:
                        result = bounded_extract(path,kind)
                    if len(self.server.cache)>500:
                        self.server.cache.clear()
                    self.server.cache[key] = result
                return self.reply(200,self.server.cache[key])
            self.reply(404,{'error':'Unknown endpoint.'})
        except (OSError,ValueError) as e:
            self.reply(400,{'error':str(e)})

    def do_POST(self):
        if not self.authorized():
            return self.reply(403,{'error':'Authentication required.'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=65536:
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(length))
            action = body.get('action')
            source = Path(body.get('source',self.server.start)).absolute()
            if self.path=='/command':
                return self.reply(200,bash_destinations(body.get('command',''),source))
            if self.path=='/visit':
                visits.record(self.server.visits,str(source))
                return self.reply(200,{'ok':True})
            if self.path=='/survey':
                return self.reply(200,{'facts':survey(body.get('paths',[]))})
            with self.server.mutation_lock:
                if action in ('rename','mkdir'):
                    name = body.get('name','')
                    if not name or name in ('.','..') or '/' in name or '\\' in name:
                        raise ValueError('Enter one filename without path separators.')
                    if action=='mkdir':
                        (source/name).mkdir()
                    else:
                        self.server.ops.move(source,source.parent/name)
                elif action in ('copy','move'):
                    dest = Path(body['destination']).absolute()/source.name
                    getattr(self.server.ops,action)(source,dest)
                elif action=='trash':
                    self.server.ops.remove(source)
                elif action=='undo':
                    self.server.ops.undo()
                elif action=='open':
                    self.open_external(source)
                else:
                    raise ValueError('Unknown action.')
                self.server.cache.clear()
            self.reply(200,{'ok':True})
        except (OSError,ValueError,KeyError) as e:
            self.reply(400,{'error':str(e)})

    @staticmethod
    def open_external(path):
        import base64
        import shutil
        if str(path).startswith('/mnt/') and len(path.parts)>3 and len(path.parts[2])==1 and shutil.which('powershell.exe'):
            win = path.parts[2].upper()+':\\'+'\\'.join(path.parts[3:])
            encoded = base64.b64encode(win.encode()).decode()
            cmd = ['powershell.exe','-NoProfile','-NonInteractive','-Command',
                   "$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"')); Invoke-Item -LiteralPath $p"]
        else:
            cmd = ['xdg-open',str(path)]
        subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


def launch():
    import argparse
    parser = argparse.ArgumentParser(description='Branch Atlas — native 3D file manager')
    parser.add_argument('path',nargs='?',default='.')
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--capture',default='')
    parser.add_argument('--headless',action='store_true')
    parser.add_argument('--focus',default='',help='smoke/capture only: zoom to this entry name')
    parser.add_argument('--legend',action='store_true',help='smoke/capture only: open the legend')
    parser.add_argument('--enter',default='',help='smoke/capture only: enter this subfolder first')
    parser.add_argument('--time',action='store_true',help='smoke/capture only: open the time slider mid-history')
    args = parser.parse_args()
    path = Path(args.path).expanduser().absolute()
    if args.path=='demo':
        from .demo import create_demo
        path = create_demo(Path(__file__).resolve().parent.parent/'demo')
    if not path.is_dir():
        parser.error('Starting path must be a directory.')
    base = Path(__file__).resolve().parent.parent
    runtimes = sorted((base/'tools').glob('Godot*_linux.x86_64'))
    if not runtimes:
        parser.error('Godot runtime missing from tools/. See README.md.')
    service = Service(path, atlas=True)
    thread = threading.Thread(target=service.serve_forever,daemon=True)
    thread.start()
    env = dict(os.environ,BRANCH_API=f'http://127.0.0.1:{service.server_port}',BRANCH_TOKEN=service.token,BRANCH_ROOT=str(path))
    if 'GALLIUM_DRIVER' not in env and Path('/usr/lib/wsl/lib/libd3d12.so').exists():
        # WSLg's default OpenGL is llvmpipe (CPU). Mesa's D3D12 driver reaches the real GPU.
        env['GALLIUM_DRIVER'] = 'd3d12'
    cmd = [str(runtimes[-1]),'--path',str(base/'native')]
    if args.headless:
        cmd.append('--headless')
    cmd += ['--']
    if args.smoke:
        cmd.append('--smoke')
    if args.capture:
        cmd += ['--capture',args.capture]
    if args.focus:
        cmd += ['--focus',args.focus]
    if args.legend:
        cmd.append('--legend')
    if args.enter:
        cmd += ['--enter',args.enter]
    if args.time:
        cmd.append('--time')
    import signal
    godot = subprocess.Popen(cmd,env=env)
    def stop(*_):
        # Never leave an orphaned window behind: WSLg keeps dead windows on screen.
        if godot.poll() is None:
            godot.terminate()
            try: godot.wait(timeout=5)
            except subprocess.TimeoutExpired: godot.kill()
        raise SystemExit(130)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        # A script parse error means the in-game smoke timer never starts; watch from here.
        return godot.wait(timeout=300 if args.smoke else None)
    except subprocess.TimeoutExpired:
        print('Smoke run exceeded 300 s (a GDScript parse error hangs the run); stopping Godot.')
        godot.kill()
        return 3
    finally:
        if godot.poll() is None:
            godot.kill()
        service.shutdown()
        service.server_close()
        if service.atlas:
            service.atlas.close()


if __name__=='__main__':
    raise SystemExit(launch())
