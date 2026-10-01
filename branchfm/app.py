from __future__ import annotations

import argparse
import curses
import json
import locale
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from .model import Node, Operations, clean, layout
from .preview import Preview, preview
from . import platform as host

WIDTH = 29
GAP = 7
HELP = '''Cartografiler — spatial file browser

Native atlas      m (open the graphical 3D companion)
Move              ↑ ↓ or k j
Enter branch      → or l or Enter
Parent branch     ← or h (keeps siblings expanded)
Collapse branch   Backspace (then returns to its parent)
Switch branch     Tab (cycles visible directory listings)
Preview / peek    Space; Escape closes the peek
Scroll preview    J K or Page Down / Page Up
Preview pane      v (show / hide)
Filter folder     / (empty input clears)
Clear filter      Escape
Show hidden       .
Go to path        g (also accepts C:\\Users\\… or D:\\…)
Bookmark folder   b; B opens the bookmark picker
Refresh           R

Copy / cut        c / x (selected file or folder)
Paste             p (into the active directory listing)
Rename            r (single filename)
New folder        n (in the active directory listing)
Trash             d (asks first; never permanently deletes)
Undo              u (last rename, move, or trash this session)
Open externally   o (default application)
Quit              q

Click a filename to select it; double-click a folder to enter.
Multiple branches stay expanded until you collapse them.
Preview reads run in a separate, time-limited process.
Files are never executed to generate previews.

Trash is private to Cartografiler, not the Windows Recycle Bin.
Recovery paths: ~/.local/share/branch/trash/recovery.tsv
Copy and new-folder actions are not in the undo stack.
Name collisions are refused, not overwritten.
'''


def cell_width(s):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)


def clip(s, width):
    out, used = '', 0
    for c in clean(s):
        n = cell_width(c)
        if used+n > width:
            break
        out += c
        used += n
    return out


def path_input(value):
    if len(value)>2 and value[1]==':' and value[0].isalpha():
        value = '/mnt/'+value[0].lower()+'/'+value[2:].replace('\\', '/').lstrip('/')
    return Path(value).expanduser().absolute()


def worker(path, conn):
    try:
        host.limit_worker_memory(384*1024*1024)
        conn.send(preview(path))
    except BaseException:
        pass
    finally:
        conn.close()


class App:
    def __init__(self, screen, start):
        self.s = screen
        self.hidden = False
        self.root = self.active = Node(start)
        self.root.refresh()
        self.rows = 12
        self.nodes = []
        self.panx = self.pany = 0
        self.peek = self.help = False
        self.show_preview = True
        self.preview_offset = 0
        self.preview = Preview('Preview', ['Select a file to look inside.'])
        self.target = None
        self.pending_at = 0
        self.proc = self.conn = None
        self.started_at = 0
        self.message = 'Open folders with →. Expanded siblings stay visible. Press ? for help.'
        self.clipboard = None
        self.data = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'branch'
        self.ops = Operations(self.data/'trash')
        self.bookmarks_file = self.data/'bookmarks.json'
        try:
            self.bookmarks = json.loads(self.bookmarks_file.read_text())
            if not isinstance(self.bookmarks, list) or not all(isinstance(x, str) for x in self.bookmarks):
                self.bookmarks = []
        except (OSError, ValueError):
            self.bookmarks = []
        self.hits = []
        self.colors = False
        self.image_pairs = {}
        self.s.keypad(True)
        self.s.timeout(60)
        try:
            curses.curs_set(0)
            curses.set_escdelay(25)
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            self.colors = True
            for i, color in enumerate([curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_MAGENTA, curses.COLOR_BLUE, curses.COLOR_WHITE, curses.COLOR_GREEN], 1):
                curses.init_pair(i, color, -1)
            if curses.COLORS >= 256:
                curses.init_pair(4, 245, -1)

    def color(self, n):
        return curses.color_pair(n) if self.colors else 0

    def put(self, y, x, text, attr=0, limit=None):
        h, w = self.s.getmaxyx()
        if y<0 or y>=h or x>=w:
            return
        text = clean(text)
        if x<0:
            # Graph labels use clipped cell widths, including wide Unicode filenames.
            while text and x<0:
                x += cell_width(text[0])
                text = text[1:]
        text = clip(text, max(0, min(w-x-1, limit if limit is not None else w)))
        if text:
            try:
                self.s.addstr(y, max(0,x), text, attr)
            except curses.error:
                pass

    def prompt(self, label, initial=''):
        value = initial
        self.s.timeout(-1)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                h, w = self.s.getmaxyx()
                self.s.move(h-1, 0)
                self.s.clrtoeol()
                display = label+value
                # Show the tail while editing long paths.
                while cell_width(display)>w-2:
                    display = display[1:]
                self.put(h-1, 0, display, curses.A_REVERSE)
                self.s.refresh()
                key = self.s.get_wch()
                if key in ('\n', '\r', curses.KEY_ENTER):
                    return value
                if key == '\x1b':
                    return None
                if key in ('\b', '\x7f', curses.KEY_BACKSPACE):
                    value = value[:-1]
                elif key == '\x15':
                    value = ''
                elif isinstance(key, str) and key.isprintable():
                    value += key
        finally:
            self.s.timeout(60)
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def stop_worker(self):
        if self.proc:
            if self.proc.is_alive():
                self.proc.terminate()
            self.proc.join(timeout=.2)
            if self.proc.is_alive():
                self.proc.kill()
                self.proc.join(timeout=.2)
            self.proc.close()
        if self.conn:
            self.conn.close()
        self.proc = self.conn = None

    def poll_preview(self):
        path = self.active.selected or self.active.path
        if path != self.target:
            self.stop_worker()
            self.target = path
            self.pending_at = time.monotonic()+.12
            self.preview_offset = 0
            self.preview = Preview('Loading', [])
        now = time.monotonic()
        if self.pending_at and now>=self.pending_at:
            self.pending_at = 0
            self.conn, sender = mp.Pipe(duplex=False)
            self.proc = mp.Process(target=worker, args=(path, sender), daemon=True)
            self.proc.start()
            sender.close()
            self.started_at = now
        if self.conn and self.conn.poll():
            try:
                self.preview = self.conn.recv()
            except EOFError:
                self.preview = Preview('Preview unavailable', ['Preview process exited without a result.'])
            self.stop_worker()
        elif self.proc and now-self.started_at>6:
            self.stop_worker()
            self.preview = Preview('Preview timed out', ['Press o to open externally.'])

    def draw_image(self, y, x, width, height):
        pixels = self.preview.pixels
        if not pixels or width<2 or height<2:
            return
        # One colored full block per cell: works in ordinary 256-color terminals,
        # without needing Sixel, Kitty, or inline-image escape protocols.
        scale = min(width/len(pixels[0]), height*2/len(pixels), 1)
        iw, ih = max(1,int(len(pixels[0])*scale)), max(1,int(len(pixels)*scale/2))
        for yy in range(ih):
            for xx in range(iw):
                r,g,b = pixels[min(len(pixels)-1,int(yy*2/scale))][min(len(pixels[0])-1,int(xx/scale))]
                if self.colors and curses.COLORS>=256:
                    color = 16+36*round(r/255*5)+6*round(g/255*5)+round(b/255*5)
                    pair = 16+color-16
                    if pair < curses.COLOR_PAIRS:
                        if color not in self.image_pairs:
                            curses.init_pair(pair, color, -1)
                            self.image_pairs[color] = pair
                        self.put(y+yy, x+xx, '█', self.color(pair), 1)
                else:
                    self.put(y+yy, x+xx, ' .:-=+*#%@'[min(9,int((r+g+b)/3/256*10))], 0, 1)

    def draw_preview(self, x, width, height):
        self.put(3, x, 'Peek' if self.peek else self.preview.kind, self.color(2)|curses.A_BOLD, width)
        p = self.active.selected or self.active.path
        self.put(4, x, p.name, curses.A_BOLD, width)
        if self.help:
            lines = HELP.splitlines()
        else:
            lines = [line for text in self.preview.lines for line in text.splitlines()]
        self.preview_offset = max(0, min(self.preview_offset, max(0,len(lines)-(height-9))))
        for i, line in enumerate(lines[self.preview_offset:self.preview_offset+height-9]):
            self.put(6+i, x, line, 0, width)
        if not self.help and self.preview.pixels:
            self.draw_image(9, x, width, height-13)
        self.put(height-3, x, 'J/K scroll · o open · Space peek', self.color(4), width)

    def draw(self):
        self.s.erase()
        h,w = self.s.getmaxyx()
        if h<12 or w<48:
            self.put(0,0,'Cartografiler needs at least 48 columns × 12 rows.')
            self.put(2,0,'Resize your terminal, or press q to quit.')
            self.s.refresh()
            return
        self.put(0,1,'Cartografiler',self.color(1)|curses.A_BOLD)
        self.put(0,15, str(self.active.path),curses.A_BOLD,w-17)
        sub = f'{len(self.active.entries)} items'
        if self.active.query:
            sub += f'  / {self.active.query}'
        if self.hidden:
            sub += '  hidden files shown'
        if self.clipboard:
            sub += f'  {self.clipboard[0]}: {self.clipboard[1].name}'
        self.put(1,1,sub,self.color(4))
        if self.peek or self.help:
            self.draw_preview(2,w-4,h)
        else:
            pw = max(32,min(60,w//3)) if self.show_preview and w>=100 else 0
            gw = w-pw-2 if pw else w
            self.rows = max(3,min(16,h-11))
            self.nodes = layout(self.root,self.rows,WIDTH,GAP)
            # A selected branch may have been hidden by its parent's scrolling.
            if self.active not in self.nodes:
                self.active = self.root
            a = self.active
            sx,sy = a.x,a.y+1+a.index-a.offset
            if sx-self.panx<0:
                self.panx = sx
            if sx+WIDTH-self.panx>gw-2:
                self.panx = max(0,sx+WIDTH-gw+2)
            if sy-self.pany<0:
                self.pany = max(0,sy-1)
            if sy-self.pany>h-8:
                self.pany = sy-(h-8)
            self.hits = []
            def graph(y,x,text,attr=0):
                yy,xx = y-self.pany+3,x-self.panx+1
                if 3<=yy<h-3 and xx<gw:
                    self.put(yy,xx,text,attr,gw-max(0,xx))
            for n in self.nodes:
                for i,p in enumerate(n.visible(self.rows)):
                    child = n.children.get(p)
                    if child not in self.nodes:
                        continue
                    x1,y1 = n.x+WIDTH,n.y+i+1
                    x2,y2 = child.x-1,child.y
                    mid = x1+GAP//2
                    graph(y1,x1,'─'*(mid-x1),self.color(4))
                    if y1==y2:
                        graph(y1,mid,'─'*(x2-mid+1),self.color(4))
                    else:
                        graph(y1,mid,'╮' if y2>y1 else '╯',self.color(4))
                        for yy in range(max(min(y1,y2)+1,self.pany),min(max(y1,y2),self.pany+h)):
                            graph(yy,mid,'│',self.color(4))
                        graph(y2,mid,('╰' if y2>y1 else '╭')+'─'*(x2-mid),self.color(4))
            for n in self.nodes:
                graph(n.y,n.x,clip(n.path.name+'/',WIDTH), self.color(2 if n is a else 4)|curses.A_BOLD)
                entries = n.visible(self.rows)
                if not entries:
                    graph(n.y+1,n.x,'  '+('Cannot read folder' if n.error else 'No matching files' if n.query else 'Empty folder'),self.color(4))
                for i,p in enumerate(entries):
                    selected = n is a and n.index==n.offset+i
                    directory = p.is_dir()
                    marker = '▾ ' if p in n.children else '▸ ' if directory else '  '
                    label = clip(marker+p.name+(' ↗' if p.is_symlink() else ''),WIDTH)
                    label += ' '*(WIDTH-cell_width(label))
                    attr = self.color(1 if directory else 5)
                    if selected:
                        attr = curses.A_REVERSE|curses.A_BOLD
                    graph(n.y+i+1,n.x,label,attr)
                    self.hits.append((n.x-self.panx+1,n.y+i+1-self.pany+3,n,n.offset+i))
                if len(n.entries)>len(entries):
                    graph(n.y+len(entries)+1,n.x,f'  {n.offset+1}–{n.offset+len(entries)} / {len(n.entries)}',self.color(4))
            if pw:
                for yy in range(3,h-2):
                    self.put(yy,gw,'│',self.color(4))
                self.draw_preview(gw+2,pw-2,h)
        self.put(h-2,1,self.message,self.color(2),w-2)
        self.put(h-1,1,'↑↓ select  → branch  ← parent  Space peek  m atlas  / filter  ? help  q quit',self.color(4),w-2)
        self.s.refresh()

    def refresh(self):
        def visit(n):
            n.refresh(self.hidden)
            for child in n.children.values():
                visit(child)
        visit(self.root)
        self.target = None

    def enter(self):
        p = self.active.selected
        if not p:
            return
        if not p.is_dir():
            self.peek = not self.peek
            return
        # Avoid expanding symlink cycles indefinitely.
        n = self.active
        while n:
            if n.path.resolve()==p.resolve():
                self.message = 'This symlink points to an ancestor; use g to browse it as a new root.'
                return
            n = n.parent
        if p not in self.active.children:
            child = Node(p,self.active)
            child.refresh(self.hidden)
            self.active.children[p] = child
        self.active = self.active.children[p]

    def go(self,path):
        p = path_input(str(path))
        if not p.is_dir():
            raise ValueError('That folder does not exist or is not accessible.')
        self.root = self.active = Node(p)
        self.root.refresh(self.hidden)
        self.panx = self.pany = 0

    def open_external(self):
        p = self.active.selected or self.active.path
        host.open_external(p)
        self.message = 'Requested external open: '+p.name

    def handle(self,key):
        if key=='q':
            if self.peek or self.help:
                self.peek = self.help = False
            else:
                return False
        elif key=='?':
            self.help = not self.help
            self.preview_offset = 0
        elif key=='\x1b':
            if self.peek or self.help:
                self.peek = self.help = False
            elif self.active.query:
                self.active.query = ''
                self.active.refresh(self.hidden)
        elif key==' ':
            self.peek = not self.peek
            self.help = False
        elif key in ('J','K',curses.KEY_NPAGE,curses.KEY_PPAGE):
            self.preview_offset += (1 if key in ('J',curses.KEY_NPAGE) else -1)*(10 if isinstance(key,int) else 1)
        elif self.help:
            return True
        elif key=='m':
            launcher = Path(__file__).resolve().parent.parent/'cartografiler'
            subprocess.Popen([str(launcher),str(self.active.path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            self.message = 'Requested Cartografiler for this directory.'
        elif key in ('j',curses.KEY_DOWN,'k',curses.KEY_UP):
            self.active.index = max(0,min(len(self.active.entries)-1,self.active.index+(1 if key in ('j',curses.KEY_DOWN) else -1)))
        elif key in ('l',curses.KEY_RIGHT,'\n','\r',curses.KEY_ENTER):
            self.enter()
        elif key in ('h',curses.KEY_LEFT):
            if self.active.parent:
                self.active = self.active.parent
            else:
                old = self.active.path
                self.go(old.parent)
                if old in self.active.entries:
                    self.active.index = self.active.entries.index(old)
                    child = Node(old,self.active)
                    child.refresh(self.hidden)
                    self.active.children[old] = child
        elif key in (curses.KEY_BACKSPACE,'\x7f','\b'):
            if self.active.parent:
                p = self.active.path
                self.active = self.active.parent
                self.active.children.pop(p,None)
            elif self.active.selected in self.active.children:
                self.active.children.pop(self.active.selected)
        elif key=='\t' and self.nodes:
            self.active = self.nodes[(self.nodes.index(self.active)+1)%len(self.nodes)]
        elif key=='v':
            self.show_preview = not self.show_preview
        elif key=='.':
            self.hidden = not self.hidden
            self.refresh()
        elif key=='R':
            self.refresh()
            self.message = 'Refreshed.'
        elif key=='/':
            q = self.prompt('Filter this folder: ',self.active.query)
            if q is not None:
                self.active.query = q
                self.active.refresh(self.hidden)
        elif key=='g':
            p = self.prompt('Go to folder: ',str(self.active.path))
            if p:
                self.go(p)
        elif key=='b':
            p = str(self.active.path)
            if p not in self.bookmarks:
                self.bookmarks.append(p)
            self.data.mkdir(parents=True,exist_ok=True)
            tmp = self.bookmarks_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.bookmarks,indent=2))
            tmp.replace(self.bookmarks_file)
            self.message = 'Bookmarked '+p
        elif key=='B':
            if not self.bookmarks:
                self.message = 'No bookmarks yet. Press b to bookmark the active folder.'
            else:
                self.s.erase()
                self.put(0,1,'Bookmarks',curses.A_BOLD)
                for i,p in enumerate(self.bookmarks):
                    self.put(i+2,1,f'{i+1}. {p}')
                value = self.prompt('Bookmark number (Escape cancels): ')
                if value:
                    i = int(value)-1
                    if not 0<=i<len(self.bookmarks):
                        raise ValueError('Invalid bookmark number.')
                    self.go(self.bookmarks[i])
        elif key in ('c','x') and self.active.selected:
            self.clipboard = ('Copy' if key=='c' else 'Move',self.active.selected)
            self.message = f'{self.clipboard[0]} ready. Navigate to the destination folder and press p.'
        elif key=='p' and self.clipboard:
            action,source = self.clipboard
            dest = self.active.path/source.name
            self.message = f'{action} in progress…'
            self.draw()
            if action=='Copy':
                self.ops.copy(source,dest)
            else:
                self.ops.move(source,dest)
                self.clipboard = None
            self.refresh()
            self.message = f'{action} complete: {dest.name}'
        elif key in ('r','n'):
            source = self.active.selected
            if key=='r' and not source:
                return True
            name = self.prompt('Rename to: ' if key=='r' else 'New folder name: ',source.name if key=='r' else '')
            if name:
                if name in ('.','..') or '/' in name or '\\' in name:
                    raise ValueError('Enter a single filename, without path separators.')
                dest = self.active.path/name
                if key=='r':
                    self.ops.move(source,dest)
                else:
                    dest.mkdir()
                self.refresh()
                if dest in self.active.entries:
                    self.active.index = self.active.entries.index(dest)
                self.message = 'Renamed.' if key=='r' else 'Folder created.'
        elif key=='d' and self.active.selected:
            source = self.active.selected
            answer = self.prompt('Move '+clean(source.name)+' to Cartografiler trash? Type yes: ')
            if answer=='yes':
                self.ops.remove(source)
                self.refresh()
                self.message = 'Moved to Cartografiler trash. Press u to restore.'
        elif key=='u':
            self.ops.undo()
            self.refresh()
            self.message = 'Last relocation undone.'
        elif key=='o':
            self.open_external()
        elif key==curses.KEY_MOUSE:
            _,x,y,_,state = curses.getmouse()
            if state & (curses.BUTTON1_CLICKED|curses.BUTTON1_DOUBLE_CLICKED):
                for xx,yy,n,i in self.hits:
                    if yy==y and xx<=x<xx+WIDTH:
                        self.active,self.active.index = n,i
                        if state & curses.BUTTON1_DOUBLE_CLICKED:
                            self.enter()
                        break
        return True

    def run(self):
        try:
            while True:
                self.poll_preview()
                self.draw()
                try:
                    key = self.s.get_wch()
                except curses.error:
                    continue
                try:
                    if not self.handle(key):
                        break
                except (OSError,ValueError,shutil.Error,curses.error) as e:
                    self.message = clean(e)
        finally:
            self.stop_worker()


def main():
    parser = argparse.ArgumentParser(description='Cartografiler — a spatial terminal file manager. Press ? for controls.')
    parser.add_argument('path',nargs='?',default='.',help='Starting folder (Linux or Windows drive path)')
    parser.add_argument('--preview',metavar='FILE',help='Print a read-only preview without opening the TUI')
    args = parser.parse_args()
    locale.setlocale(locale.LC_ALL,'')
    if args.preview:
        result = preview(path_input(args.preview))
        print(result.kind)
        for line in result.lines:
            print(clean(line))
        return
    path = path_input(args.path)
    if not path.is_dir():
        parser.error('Starting path must be an accessible directory.')
    if not sys.stdin.isatty():
        parser.error('Run Cartografiler in an interactive terminal, or use --preview FILE.')
    try:
        curses.wrapper(lambda s: App(s,path).run())
    except KeyboardInterrupt:
        pass


if __name__=='__main__':
    main()
