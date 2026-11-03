"""Prepare public web images and isolated article-content screenshots."""
from __future__ import annotations

import html
import io
import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .link_reader import fetch_public, PageText
from .paths import DATA, ROOT


def _output() -> Path:
    folder = DATA / 'temp' / 'web_media'
    folder.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(suffix='.png', dir=folder)
    os.close(handle)
    return Path(name)


def download_image(url: str) -> Path:
    """Decode and re-encode a public image; never forward arbitrary files."""
    from PIL import Image, ImageOps
    raw, _ = fetch_public(url, image=True)
    output = _output()
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.width * original.height > 20_000_000:
                raise ValueError('网页图片尺寸过大')
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((2400, 6000))
            image.save(output, 'PNG')
        return output
    except Exception:
        output.unlink(missing_ok=True)
        raise


def capture_page(url: str) -> Path:
    """Render an article excerpt offline and capture only its DOM element.

    This is a readable content screenshot, not a capture of the source site's
    original layout. No desktop capture, login profile or remote scripts.
    """
    raw, encoding = fetch_public(url)
    parser = PageText()
    parser.feed(raw.decode(encoding, 'replace'))
    text = '\n'.join(parser.main_parts or parser.parts).strip()
    if len(text) < 30:
        raise ValueError('网页正文不足，不能截图')
    title = '网页正文' if len(text) <= 5000 else '网页正文节选'
    document = ('<!doctype html><meta charset="utf-8">'
                '<style>body{margin:0;background:white}article{box-sizing:border-box;width:960px;'
                'padding:32px;color:#172337;font:22px/1.65 "Microsoft YaHei",sans-serif;'
                'overflow-wrap:anywhere;white-space:pre-wrap}small{font-size:15px;color:#567}</style>'
                '<article><h2>' + title + '</h2><small>' + html.escape(url[:350]) +
                '</small><hr>' + html.escape(text[:5000]) + '</article>')
    output = _output()
    document_path = output.with_suffix('.json')
    try:
        env = os.environ.copy()
        folder = str(output.parent.resolve())
        env.update(TEMP=folder, TMP=folder, TMPDIR=folder)
        document_path.write_text(json.dumps(document), encoding='utf-8')
        env['ARONA_WEB_RENDER_DOCUMENT'] = str(document_path)
        command = ([sys.executable, '--web-render-worker', str(output)] if getattr(sys, 'frozen', False)
                   else [sys.executable, '-m', 'app.web_media', str(output)])
        subprocess.run(command, cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=50,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return output
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        document_path.unlink(missing_ok=True)


def render_worker(path: str) -> None:
    from playwright.sync_api import sync_playwright
    source = Path(os.environ['ARONA_WEB_RENDER_DOCUMENT'])
    if source.stat().st_size > 200000:
        raise ValueError('网页渲染内容过大')
    document = json.loads(source.read_text(encoding='utf-8'))
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel='msedge', headless=True, timeout=20000)
        try:
            context = browser.new_context(viewport={'width': 960, 'height': 1280},
                                          java_script_enabled=False, service_workers='block', accept_downloads=False)
            context.set_offline(True)
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_content(document, wait_until='domcontentloaded', timeout=10000)
            page.locator('article').screenshot(path=path, timeout=15000)
        finally:
            browser.close()


if __name__ == '__main__':
    render_worker(sys.argv[1])
