"""Local authenticated data service shared by the native atlas and terminal tools."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import secrets
import subprocess
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .atlas import biome, metadata
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

    def __init__(self, start):
        super().__init__(('127.0.0.1',0),Handler)
        self.token = secrets.token_urlsafe(32)
        self.start = str(start)
        self.ops = Operations(Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'branch/trash')
        self.mutation_lock = threading.Lock()
        self.extract_slots = threading.Semaphore(3)
        self.cache = {}


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

    def authorized(self):
        return secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+self.server.token)

    def do_GET(self):
        if not self.authorized():
            return self.reply(403,{'error':'Authentication required.'})
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
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
                page_size = 120
                if focus:
                    match = next((i for i,e in enumerate(entries) if e['path']==focus),None)
                    if match is not None: page = match//page_size
                page = min(page,max(0,(len(entries)-1)//page_size))
                return self.reply(200,{'path':str(path),'parent':str(path.parent),'entries':entries[page*page_size:(page+1)*page_size],
                                       'total':len(entries),'page':page,'pages':max(1,(len(entries)+page_size-1)//page_size)})
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
            if not 0<length<=16384:
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(length))
            action = body.get('action')
            source = Path(body.get('source',self.server.start)).absolute()
            if self.path=='/command':
                return self.reply(200,bash_destinations(body.get('command',''),source))
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
    args = parser.parse_args()
    path = Path(args.path).expanduser().absolute()
    if not path.is_dir():
        parser.error('Starting path must be a directory.')
    base = Path(__file__).resolve().parent.parent
    runtimes = sorted((base/'tools').glob('Godot*_linux.x86_64'))
    if not runtimes:
        parser.error('Godot runtime missing from tools/. See README.md.')
    service = Service(path)
    thread = threading.Thread(target=service.serve_forever,daemon=True)
    thread.start()
    env = dict(os.environ,BRANCH_API=f'http://127.0.0.1:{service.server_port}',BRANCH_TOKEN=service.token,BRANCH_ROOT=str(path))
    cmd = [str(runtimes[-1]),'--path',str(base/'native')]
    if args.headless:
        cmd.append('--headless')
    cmd += ['--']
    if args.smoke:
        cmd.append('--smoke')
    if args.capture:
        cmd += ['--capture',args.capture]
    try:
        return subprocess.call(cmd,env=env)
    finally:
        service.shutdown()
        service.server_close()


if __name__=='__main__':
    raise SystemExit(launch())
