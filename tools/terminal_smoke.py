"""Exercise real curses startup, navigation, filtering, preview, and clean exit."""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import termios
import time

base = Path(__file__).resolve().parent.parent
master,slave = pty.openpty()
fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',45,150,0,0))
process = subprocess.Popen([str(base/'branch'),str(base/'demo')],stdin=slave,stdout=slave,stderr=slave,env=dict(os.environ,TERM='xterm-256color'),start_new_session=True)
os.close(slave)
output = bytearray()

def drain(seconds):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        if select.select([master],[],[],0.05)[0]:
            try: output.extend(os.read(master,65536))
            except OSError: break

try:
    drain(.4)
    for keys in (b'l',b'h',b'j',b'l',b'h',b'R',b'.',b'/Field\n',b' ',b' ',b'\x1b',b'q'):
        os.write(master,keys)
        drain(.18)
    process.wait(timeout=5)
    assert process.returncode==0,output.decode(errors='replace')[-4000:]
    assert b'Traceback' not in output,output.decode(errors='replace')[-4000:]
    print('TERMINAL_SMOKE_OK: navigation, refresh, hidden toggle, filtering, preview, clean exit')
finally:
    if process.poll() is None: process.kill(); process.wait()
    os.close(master)
