"""The survey: a persistent index of the filesystem that the world map is drawn from.

The map must exist before you visit a place, so Branch keeps an SQLite index of every
directory and file it has surveyed and fills it in coarse-to-fine: a breadth-first crawl,
one directory at a time, from a priority queue. Whatever you are looking at jumps the
queue. Each filesystem is crawled natively: Linux paths with os.scandir in WSL, Windows
drives (/mnt/<letter>) by a Windows Python worker. Over WSL's 9p bridge a crawl runs at
about 1k entries/s. Natively on NTFS it reaches about 70k/s and also yields the Windows
attribute bits (cloud-file tides) for free.

Nothing here reads file contents. Directory listings and stat results only.
"""
from __future__ import annotations

import heapq
import itertools
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath

from .atlas import kind_of
from .cloud import to_linux, to_windows

# Listed as places on the map but never crawled: virtual, recursive or not ours to walk.
NO_CRAWL = {'/proc', '/sys', '/dev', '/run', '/mnt/wsl', '/mnt/wslg', '/lost+found', '/snap',
            '/var/lib/docker', '/usr/lib/wsl', '/boot/efi'}
REPARSE_POINT = 0x400
KINDS = ('folders', 'pdf', 'images', 'audio', 'video', 'tables', 'code', 'archives', 'binaries',
         'disks', 'databases', 'documents', 'other')
DAY = 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  id INTEGER PRIMARY KEY, parent INTEGER, name TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
  depth INTEGER NOT NULL, is_dir INTEGER NOT NULL, kind TEXT NOT NULL,
  size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, link INTEGER DEFAULT 0, attrs INTEGER DEFAULT 0,
  scanned REAL DEFAULT 0, error TEXT,
  files INTEGER DEFAULT 0, dirs INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, newest REAL DEFAULT 0,
  day INTEGER DEFAULT 0, week INTEGER DEFAULT 0, kinds TEXT, unscanned INTEGER DEFAULT 0,
  dirty INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS nodes_parent ON nodes(parent);
CREATE INDEX IF NOT EXISTS nodes_dirty ON nodes(dirty) WHERE dirty=1;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def default_location() -> Path:
    return Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'branch/index.sqlite'


def depth_of(path: str) -> int:
    return 0 if path == '/' else path.count('/')


def parent_of(path: str) -> str:
    return str(PurePosixPath(path).parent)


def clean_name(name: str) -> str | None:
    # Names that are not valid UTF-8 cannot round-trip through SQLite text; they are counted
    # as unindexable rather than stored under a lossy spelling that file operations would miss.
    try:
        name.encode('utf-8')
        return name
    except UnicodeEncodeError:
        return None


class Index:
    def __init__(self, location: Path | None = None):
        self.location = Path(location or default_location())
        self.location.parent.mkdir(parents=True, exist_ok=True)
        self.local = threading.local()
        self.write_lock = threading.RLock()
        self.version = 0          # bumped whenever aggregates settle; the map polls it
        self.changed = []         # (version, path) of directories whose aggregates changed
        self.listed = []          # (time, path) of directories whose own listing was stored
        with self.write_lock:
            db = self.db()
            db.executescript(SCHEMA)
            if db.execute("SELECT 1 FROM nodes WHERE path='/'").fetchone() is None:
                db.execute("INSERT INTO nodes(parent,name,path,depth,is_dir,kind) VALUES(NULL,'/','/',0,1,'folders')")
            db.commit()

    def db(self) -> sqlite3.Connection:
        db = getattr(self.local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.location, timeout=30, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=NORMAL')
            db.execute('PRAGMA cache_size=-65536')
            self.local.db = db
        return db

    def close(self):
        db = getattr(self.local, 'db', None)
        if db is not None:
            db.close()
            self.local.db = None

    # ---------------------------------------------------------------- reading

    def node(self, path: str) -> sqlite3.Row | None:
        return self.db().execute('SELECT * FROM nodes WHERE path=?', (path,)).fetchone()

    def node_by_id(self, node_id: int) -> sqlite3.Row | None:
        return self.db().execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()

    def children(self, node_id: int) -> list[sqlite3.Row]:
        return self.db().execute('SELECT * FROM nodes WHERE parent=? ORDER BY is_dir DESC, name COLLATE NOCASE', (node_id,)).fetchall()

    def stats(self) -> dict:
        row = self.db().execute('SELECT files, dirs, unscanned, bytes FROM nodes WHERE path=?', ('/',)).fetchone()
        return dict(row) if row else {}

    # ---------------------------------------------------------------- writing

    def ensure(self, path: str) -> int:
        """Id of a directory node, creating unscanned ancestors as needed."""
        row = self.db().execute('SELECT id FROM nodes WHERE path=?', (path,)).fetchone()
        if row:
            return row[0]
        parent = self.ensure(parent_of(path))
        name = PurePosixPath(path).name
        with self.write_lock:
            cur = self.db().execute('INSERT OR IGNORE INTO nodes(parent,name,path,depth,is_dir,kind,dirty) VALUES(?,?,?,?,1,?,1)',
                                    (parent, name, path, depth_of(path), 'folders'))
            self.db().commit()
        return cur.lastrowid or self.db().execute('SELECT id FROM nodes WHERE path=?', (path,)).fetchone()[0]

    def store(self, path: str, entries: list, error: str | None = None, when: float | None = None) -> list[str]:
        """Record one directory listing. Returns the subdirectory paths that may be crawled."""
        when = when or time.time()
        dir_id = self.ensure(path)
        base = '' if path == '/' else path
        depth = depth_of(path)+1
        subdirs = []
        with self.write_lock:
            db = self.db()
            if error is not None:
                db.execute('UPDATE nodes SET scanned=?, error=?, dirty=1 WHERE id=?', (when, error, dir_id))
                self._mark_ancestors(path)
                db.commit()
                return []
            old = {r['name']: r['is_dir'] for r in db.execute('SELECT name,is_dir FROM nodes WHERE parent=?', (dir_id,))}
            rows = []
            seen = set()
            for name, is_dir, size, mtime, link, attrs in entries:
                name = clean_name(name)
                if name is None or name in seen:
                    continue
                seen.add(name)
                child = base+'/'+name
                crawlable = bool(is_dir) and not link
                if old.get(name) not in (None, int(bool(is_dir))):
                    self._delete_subtree(child)
                rows.append((dir_id, name, child, depth, int(bool(is_dir)), kind_of(name, bool(is_dir)),
                             int(size or 0), float(mtime or 0), int(bool(link)), int(attrs or 0), int(bool(is_dir))))
                if crawlable and child not in NO_CRAWL:
                    subdirs.append(child)
            for name in set(old) - seen:
                self._delete_subtree(base+'/'+name)
            db.executemany("""INSERT INTO nodes(parent,name,path,depth,is_dir,kind,size,mtime,link,attrs,dirty)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)
                              ON CONFLICT(path) DO UPDATE SET is_dir=excluded.is_dir, kind=excluded.kind,
                              size=excluded.size, mtime=excluded.mtime, link=excluded.link, attrs=excluded.attrs""", rows)
            db.execute('UPDATE nodes SET scanned=?, error=NULL, dirty=1 WHERE id=?', (when, dir_id))
            self._mark_ancestors(path)
            db.commit()
            self.listed.append((time.time(), path))
            del self.listed[:-20000]
        return subdirs

    def _delete_subtree(self, path: str):
        # Path range on the unique index: everything under path/ sorts between 'path/' and 'path0'.
        self.db().execute("DELETE FROM nodes WHERE path=? OR (path>=? AND path<?)", (path, path+'/', path+'0'))

    def _mark_ancestors(self, path: str):
        prefixes = ['/']+['/'+'/'.join(PurePosixPath(path).parts[1:i+1]) for i in range(1, len(PurePosixPath(path).parts))]
        self.db().execute(f"UPDATE nodes SET dirty=1 WHERE path IN ({','.join('?'*len(prefixes))})", prefixes)

    def settle(self, limit: int = 20000) -> int:
        """Recompute subtree aggregates for dirty directories, deepest first."""
        now = time.time()
        with self.write_lock:
            db = self.db()
            dirty = db.execute('SELECT id, path FROM nodes WHERE dirty=1 ORDER BY depth DESC LIMIT ?', (limit,)).fetchall()
            for row in dirty:
                files = dirs = size = day = week = unscanned = 0
                newest = 0.0
                kinds = {}
                for c in db.execute('SELECT is_dir,kind,size,mtime,link,scanned,files,dirs,bytes,newest,day,week,kinds,unscanned FROM nodes WHERE parent=?', (row['id'],)):
                    if c['is_dir'] and not c['link']:
                        dirs += 1+c['dirs']
                        files += c['files']
                        size += c['bytes']
                        newest = max(newest, c['newest'] or 0, c['mtime'] or 0)
                        day += c['day']
                        week += c['week']
                        unscanned += c['unscanned'] + (0 if c['scanned'] else 1)
                        if c['kinds']:
                            for k, v in json.loads(c['kinds']).items():
                                kinds[k] = kinds.get(k, 0)+v
                    else:
                        files += 1
                        size += c['size'] or 0
                        m = c['mtime'] or 0
                        newest = max(newest, m)
                        day += (now-m) < DAY
                        week += (now-m) < 7*DAY
                        kinds[c['kind']] = kinds.get(c['kind'], 0)+1
                db.execute('UPDATE nodes SET files=?,dirs=?,bytes=?,newest=?,day=?,week=?,kinds=?,unscanned=?,dirty=0 WHERE id=?',
                           (files, dirs, size, newest, day, week, json.dumps(kinds), unscanned, row['id']))
            db.commit()
            if dirty:
                self.version += 1
                self.changed.extend((self.version, r['path']) for r in dirty)
                del self.changed[:-5000]
        return len(dirty)


# ---------------------------------------------------------------- native listing

WINDOWS_WORKER = r'''
import json, os, sys
REPARSE = 0x400
LINK_TAGS = {0xA000000C, 0xA0000003}   # symlink, junction/mount point: never descend
for line in sys.stdin:
    req = json.loads(line)
    out = []
    try:
        with os.scandir(req["p"]) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    attrs = getattr(st, "st_file_attributes", 0)
                    is_dir = e.is_dir(follow_symlinks=False)
                    link = e.is_symlink()
                    if is_dir and attrs & REPARSE and not link:
                        try: link = os.lstat(e.path).st_reparse_tag in LINK_TAGS
                        except OSError: link = True
                    out.append([e.name, is_dir, st.st_size, st.st_mtime, link, attrs])
                except OSError:
                    out.append([e.name, False, 0, 0, False, 0])
        res = {"e": out}
    except OSError as ex:
        res = {"err": ex.strerror or str(ex)}
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()
'''


class WindowsLister:
    """A persistent Windows Python process that lists NTFS directories natively."""
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.available = bool(shutil.which('py.exe') or shutil.which('python.exe'))
        self.timeout = 20.0

    def _start(self):
        exe = shutil.which('py.exe')
        cmd = [exe, '-3', '-u', '-c', WINDOWS_WORKER] if exe else [shutil.which('python.exe'), '-u', '-c', WINDOWS_WORKER]
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd='/')

    def list(self, path: str):
        windows = to_windows(Path(path))
        if windows is None:
            raise OSError('not a Windows drive path')
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self._start()
            self.process.stdin.write((json.dumps({'p': windows})+'\n').encode())
            self.process.stdin.flush()
            # Streamed drives (Google Drive, cloud placeholders) can stall on the network.
            import select
            ready, _, _ = select.select([self.process.stdout], [], [], self.timeout)
            if not ready:
                self.process.kill()
                self.process = None
                raise OSError(f'listing timed out after {self.timeout:.0f} s')
            line = self.process.stdout.readline()
        if not line:
            self.process = None
            raise OSError('Windows lister stopped')
        result = json.loads(line)
        if 'err' in result:
            raise OSError(result['err'])
        return result['e']

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            self.process.terminate()


def list_linux(path: str):
    out = []
    with os.scandir(path) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
                out.append([e.name, e.is_dir(follow_symlinks=False), st.st_size, st.st_mtime, e.is_symlink(), 0])
            except OSError:
                out.append([e.name, False, 0, 0, False, 0])
    return out


def windows_drive(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) >= 3 and parts[1] == 'mnt' and len(parts[2]) == 1


class Surveyor(threading.Thread):
    """Background crawler. Priorities: 0 what you're looking at, 1 where you started,
    2 home, 3 the rest of Linux, 4 Windows drives, 5 network or streamed drives."""
    def __init__(self, index: Index, windows: WindowsLister | None = None):
        super().__init__(daemon=True)
        self.index = index
        self.windows = windows or WindowsLister()
        self.queue = []
        self.queued = {}
        self.counter = itertools.count()
        self.wake = threading.Condition()
        self.stopping = False
        self.current = ''
        self.done = 0
        self.last_settle = 0.0

    def priority_for(self, path: str) -> int:
        if path.startswith(str(Path.home())):
            return 2
        if windows_drive(path):
            return 5 if path.startswith('/mnt/g') else 4
        return 3

    def request(self, path: str, priority: int | None = None, recursive: bool = True):
        priority = self.priority_for(path) if priority is None else priority
        with self.wake:
            known = self.queued.get(path)
            if known is not None and known[0] <= priority:
                return
            self.queued[path] = (priority, recursive)
            heapq.heappush(self.queue, (priority, next(self.counter), path, recursive))
            self.wake.notify()

    def pending(self) -> int:
        return len(self.queued)

    def stop(self):
        self.stopping = True
        with self.wake:
            self.wake.notify()
        self.windows.close()

    def run(self):
        while not self.stopping:
            with self.wake:
                while not self.queue and not self.stopping:
                    self.wake.wait(timeout=1.0)
                    self._settle(force=True)
                if self.stopping:
                    return
                priority, _, path, recursive = heapq.heappop(self.queue)
                if self.queued.get(path, (None,))[0] != priority:
                    continue  # superseded by a higher-priority request
                del self.queued[path]
            self.current = path
            try:
                entries = self.windows.list(path) if windows_drive(path) and self.windows.available else list_linux(path)
                subdirs = self.index.store(path, entries)
            except OSError as e:
                self.index.store(path, [], error=str(e))
                subdirs = []
            self.done += 1
            self.current = ''
            if recursive:
                # Children inherit the priority, except that interactive requests (0)
                # hand their subtree back to the ordinary survey order.
                for sub in subdirs:
                    # Never better than the place's own class: a streamed cloud drive stays
                    # last in line even when the survey started from the root.
                    self.request(sub, None if priority == 0 else max(priority, self.priority_for(sub)), True)
            self._settle()
        self.current = ''

    def _settle(self, force: bool = False):
        if force or time.monotonic()-self.last_settle > 1.5:
            self.last_settle = time.monotonic()
            while self.index.settle():
                if not force:
                    break


def main():
    """Survey from the command line: python3 -m branchfm.index [PATH ...] (default: everything)."""
    import argparse
    parser = argparse.ArgumentParser(description='Build or refresh the Branch Atlas survey index')
    parser.add_argument('paths', nargs='*', default=['/'])
    parser.add_argument('--index', default=None)
    args = parser.parse_args()
    index = Index(args.index)
    surveyor = Surveyor(index)
    surveyor.start()
    for path in args.paths:
        surveyor.request(os.path.abspath(path), 1 if path != '/' else 3)
    started = time.time()
    try:
        while surveyor.pending() or surveyor.current:
            time.sleep(5)
            stats = index.stats()
            print(f"{time.time()-started:7.0f}s  dirs scanned {surveyor.done:>9,}  queued {surveyor.pending():>8,}  "
                  f"files {stats.get('files', 0):>11,}  now {surveyor.current[:70]}", flush=True)
    finally:
        surveyor._settle(force=True)
        surveyor.stop()
    index.db().execute("INSERT OR REPLACE INTO meta VALUES('last_full_survey', ?)", (str(time.time()),))
    index.db().commit()
    print('done', index.stats(), f'{time.time()-started:.0f}s', flush=True)


if __name__ == '__main__':
    main()
