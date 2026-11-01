"""Build and audit the Windows distribution from the tracked source snapshot."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import __version__

OUT = ROOT / 'release'
SOURCE_DIRS = ('app', 'assets', 'tests', 'scripts', 'tools', 'docs', 'vendor')
SOURCE_FILES = ('run.py', 'README.md', 'CHANGELOG.md', 'LICENSE', 'index.html',
                'requirements.txt', 'requirements-wechatauto.txt', '.gitignore', 'ARONA.spec',
                '.github/workflows/release.yml')
SECRET = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{25,})')


def source_files():
    for name in SOURCE_FILES:
        if (ROOT/name).is_file():
            yield ROOT/name
    for name in SOURCE_DIRS:
        for path in (ROOT/name).rglob('*'):
            if (path.is_file() and '__pycache__' not in path.parts and
                    path.suffix.lower() not in ('.pyc', '.log', '.db') and '.bak-' not in path.name):
                yield path
    for path in ROOT.glob('*.bat'):
        yield path
    # Only built-in stickers, never a user's imported sticker library.
    from app.defaults import default_stickers
    for item in default_stickers():
        path = ROOT / item['file']
        if path.is_file():
            yield path


def audit(path: Path, payload: bytes):
    if SECRET.search(payload):
        raise ValueError(f'Credential-like content in {path.name}; build cancelled')


def source_zip():
    OUT.mkdir(exist_ok=True)
    target = OUT/f'ARONA-Voice-v{__version__}-source.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(source_files())):
            payload = path.read_bytes()
            audit(path, payload)
            archive.writestr(f'ARONA-Voice/{path.relative_to(ROOT).as_posix()}', payload)
    return target


def build_windows():
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', str(ROOT/'ARONA.spec')],
                   cwd=ROOT, check=True)
    dist = ROOT/'dist'/'ARONA'
    sticker_dir = dist/'data'/'stickers'
    sticker_dir.mkdir(parents=True, exist_ok=True)
    from app.defaults import default_stickers
    for item in default_stickers():
        path = ROOT/item['file']
        if path.is_file():
            shutil.copy2(path, sticker_dir/path.name)
    for name in ('README.md', 'LICENSE', 'CHANGELOG.md'):
        shutil.copy2(ROOT/name, dist/name)
    # Smoke the clean build before publishing any artifact.
    subprocess.run([str(dist/'ARONA.exe'), '--ui-smoke'], cwd=dist, check=True, timeout=120)
    # Smoke creates local defaults and empty DBs. Never ship generated runtime data.
    target = OUT/f'ARONA-Voice-v{__version__}-windows-x64.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in dist.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(dist)
            if rel.parts[0] in ('data', 'logs') and not (len(rel.parts) == 3 and rel.parts[0:2] == ('data', 'stickers') and path.suffix == '.png'):
                continue
            archive.write(path, 'ARONA/'+rel.as_posix())
    return target


def main():
    source = source_zip()
    files = [source]
    if '--windows' in sys.argv:
        files.append(build_windows())
    checksums = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in files]
    (OUT/'SHA256SUMS.txt').write_text('\n'.join(checksums)+'\n', encoding='utf-8')
    print(json.dumps({'version': __version__, 'artifacts': [p.name for p in files]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
