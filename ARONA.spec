# PyInstaller build: only explicit application resources, never local user data.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [('assets', 'assets'), ('scripts/character_voice_worker.py', 'scripts')]
binaries = []
hiddenimports = ['win32timezone']
for package in ('wecvoice_auto', 'edge_tts', 'comtypes', 'pycaw', 'uiautomation', 'miniaudio', '_sounddevice_data', 'playwright'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules('wechatauto')
a = Analysis(['run.py'], pathex=['.'], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest', 'wxauto', 'wxautox'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='什亭之匣', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon='assets/arona_icon.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='什亭之匣')
