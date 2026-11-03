"""Explicit real image/screenshot transport check, restricted to test only."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore
from app.wechat_adapter import WeChatAutoAdapter
from app.web_media import capture_page, download_image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['image', 'screenshot'])
    args = parser.parse_args()
    config = ConfigStore()
    path = None
    adapter = WeChatAutoAdapter(dict(config.wechat), lambda _: None)
    try:
        path = (download_image('https://www.python.org/static/img/python-logo.png') if args.mode == 'image'
                else capture_page('https://docs.python.org/3/tutorial/index.html'))
        adapter.start([{'name':'test', 'type':'group', 'enabled':True}])
        if not adapter.ensure_chat_open('test') or adapter._uia.current_chat() != 'test':
            raise RuntimeError('Cannot verify test chat; cancelled')
        user = next(iter(adapter.target_usernames()))
        before = adapter._last_seq(user)
        result = adapter.send_sticker('test', path)
        print('WEB_MEDIA_KIND', args.mode, 'SEND_OK', result.ok)
        assert result.ok
        rows = adapter._db.get_messages(user, limit=10)
        assert any(int(row.get('sort_seq',0)) > before
                   and str(row.get('sender_username') or '') == adapter.self_wxid
                   and adapter._message_type_name(row.get('type')) == 'image' for row in rows)
        print('REAL_IMAGE_BUBBLE_DB_CONFIRMED', True)
        return 0
    finally:
        adapter.stop()
        if path:
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
