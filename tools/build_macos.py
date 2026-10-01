#!/usr/bin/env python3
"""Build a self-contained Apple Silicon app on macOS; no downloads at app launch."""
from __future__ import annotations

import argparse
import hashlib
import io
import importlib.metadata
import sysconfig
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = '4.7.2-stable'
URL = f'https://github.com/godotengine/godot-builds/releases/download/{VERSION}'


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def fetch(name: str, folder: Path, sums: dict[str, str]) -> Path:
    target = folder/name
    if not target.exists():
        print(f'Downloading {name}', flush=True)
        partial = target.with_suffix(target.suffix+'.partial')
        with urllib.request.urlopen(f'{URL}/{name}', timeout=240) as source, partial.open('wb') as dest:
            shutil.copyfileobj(source, dest)
        partial.replace(target)
    if name not in sums:
        raise RuntimeError(f'No official SHA512 checksum for {name}')
    digest = hashlib.sha512()
    with target.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != sums[name]:
        raise RuntimeError(f'Checksum mismatch: {target}; remove this download and retry.')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-tests', action='store_true', help='skip source tests; frozen checks always run')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        parser.error('Build on an Apple Silicon Mac using arm64 Python (or the macOS Actions workflow).')
    assets = ROOT/'build/macos-assets'
    downloads = ROOT/'build/downloads'
    assets.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f'{URL}/SHA512-SUMS.txt', timeout=60) as source:
        sums = {parts[1].lstrip('*'): parts[0] for line in source.read().decode().splitlines()
                if len(parts := line.split()) == 2}
    editor_zip = fetch(f'Godot_v{VERSION}_macos.universal.zip', downloads, sums)
    template_zip = fetch(f'Godot_v{VERSION}_export_templates.tpz', downloads, sums)
    editor = assets/'Godot'
    with zipfile.ZipFile(editor_zip) as archive:
        member = next(n for n in archive.namelist() if n.endswith('/Contents/MacOS/Godot'))
        editor.write_bytes(archive.read(member))
    editor.chmod(0o755)
    runtime = assets/'CartografilerMap'
    with zipfile.ZipFile(template_zip) as archive:
        member = next(n for n in archive.namelist() if n.endswith('/macos.zip'))
        with zipfile.ZipFile(io.BytesIO(archive.read(member))) as mac:
            executable = next(n for n in mac.namelist() if n.endswith('/godot_macos_release.universal'))
            runtime.write_bytes(mac.read(executable))
            licenses = assets/'licenses'
            licenses.mkdir(exist_ok=True)
            for name in mac.namelist():
                if name.endswith(('LICENSE.txt', 'COPYRIGHT.txt')):
                    (licenses/Path(name).name).write_bytes(mac.read(name))
    runtime.chmod(0o755)
    # Explicit Godot notices even if the template archive stores them elsewhere.
    for name, url in {'GODOT-LICENSE.txt': 'https://raw.githubusercontent.com/godotengine/godot/4.7.2-stable/LICENSE.txt',
                      'GODOT-COPYRIGHT.txt': 'https://raw.githubusercontent.com/godotengine/godot/4.7.2-stable/COPYRIGHT.txt'}.items():
        with urllib.request.urlopen(url, timeout=60) as source:
            (licenses/name).write_bytes(source.read())
    for package in ('numpy', 'scipy', 'Pillow', 'numba', 'llvmlite', 'zstandard', 'PyInstaller'):
        distribution = importlib.metadata.distribution(package)
        destination = licenses/package
        destination.mkdir(exist_ok=True)
        copied = []
        for number, file in enumerate(distribution.files or []):
            if any(part.lower().startswith(('license', 'copying', 'copyright', 'notice')) for part in file.parts):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = destination/f'{number}-{source.name}'
                    shutil.copy2(source, target)
                    copied.append(str(file))
        (destination/'METADATA.txt').write_text(str(distribution.metadata))
    python_license = Path(sysconfig.get_path('stdlib'))/'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Python license missing; use the python.org/setup-python distribution.')
    shutil.copy2(python_license, licenses/'PYTHON-LICENSE.txt')
    shutil.copy2(ROOT/'native/fonts/LICENSE.txt', licenses/'FONTS-LICENSE.txt')
    shutil.copy2(ROOT/'native/textures/LICENSE.md', licenses/'TEXTURES-LICENSE.md')
    run(editor, '--headless', '--path', ROOT/'native', '--editor', '--import', '--quit')
    if not args.skip_tests:
        run(sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v')
        run(editor, '--headless', '--path', ROOT/'native', '--script', 'res://tests/streaming.gd')
    run(editor, '--headless', '--path', ROOT/'native', '--export-pack', 'macOS', assets/'CartografilerMap.pck')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', ROOT/'packaging/macos/Cartografiler.spec')
    app = ROOT/'dist/Cartografiler.app'
    executable = app/'Contents/MacOS/Cartografiler'
    run(executable, '--package-check', ROOT/'dist/package-check.json')
    # The exported release runtime parses and loads the PCK without a local editor.
    # --smoke validates tile loading and application lifecycle; CI has no interactive GPU session.
    run(executable, '/', '--headless', '--smoke')
    run('codesign', '--verify', '--deep', '--strict', app)
    output = ROOT/'dist/Cartografiler-macos-arm64.zip'
    run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, output)
    print(f'Ready: {output}', flush=True)


if __name__ == '__main__':
    main()
