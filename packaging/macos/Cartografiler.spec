# Build with tools/build_macos.py on an arm64 Mac.
from pathlib import Path
import os

root = Path(SPECPATH).parents[1]
assets = root/'build/macos-assets'
a = Analysis(
    [str(root/'packaging/macos/entry.py')],
    pathex=[str(root)],
    binaries=[(str(assets/'CartografilerMap'), 'runtime')],
    datas=[(str(assets/'CartografilerMap.pck'), 'runtime'),
           (str(root/'native/textures'), 'native/textures'),
           (str(root/'packaging/macos/icon'), 'licenses/icon'),
           (str(assets/'licenses'), 'licenses/runtime')],
    hiddenimports=['branchfm.package_checks'],
    hookspath=[], runtime_hooks=[],
    excludes=['matplotlib', 'IPython', 'pytest', 'tkinter'],
    # Numba's cache locators need real source files, not just a PYZ code object.
    module_collection_mode={'branchfm': 'py'},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Cartografiler',
          debug=False, strip=False, upx=False, console=False, target_arch='arm64',
          codesign_identity=os.environ.get('CARTOGRAFILER_SIGN_IDENTITY'),
          entitlements_file=str(root/'packaging/macos/entitlements.plist'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Cartografiler')
app = BUNDLE(coll, name='Cartografiler.app',
             icon=str(root/'packaging/macos/icon/Cartografiler.icns'),
             bundle_identifier='com.cartografiler.desktop',
             info_plist={'CFBundleDisplayName': 'Cartografiler',
                         'CFBundleShortVersionString': '0.1.0',
                         'LSMinimumSystemVersion': '14.0',
                         'NSHighResolutionCapable': True})
