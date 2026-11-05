"""Approved style pool, diary rotation and exact native emoji decoding."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app import config_store
from app.config_store import StickerStore
from app.defaults import default_stickers
from app.diary import DailyDiary
from app.multimodal import attach_images
from app.paths import ROOT
from app.sticker_policy import allowed,fingerprint,reviewed_style,BLOCKED_HASHES
from app.wechat_emojis import WeChatEmojiReader,payload_text,emoji_node,inline_emoji_names,fetch_emoji,image_bytes
from app.prompting import build_context_messages
from app.wechat_adapter import WeChatAutoAdapter


def main():
    assert all('arona_official_' in s['file'] and s['style']=='official_portrait' for s in default_stickers())
    assert all((ROOT/s['file']).is_file() for s in default_stickers())
    assert inline_emoji_names('难受[流泪] [微笑] [流泪]')==['流泪','微笑']
    assert emoji_node('<!DOCTYPE x><msg><emoji/></msg>') is None
    for value in ('动画表情','emoji',47,11000,(17<<32)|11000):
        assert WeChatAutoAdapter._message_type_name(None,value)=='emotion'
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);store=StickerStore(root/'stickers.json')
        # At least eight distinct default choices before any repeats; history
        # comes from durable publish receipts, not process-local randomness.
        diary=DailyDiary(SimpleNamespace(stickers=store),root/'diary.json');records={};picked=[]
        for day in range(1,9):
            key=f'2026-11-{day:02}'
            item=diary.sticker_options(records,key)[0];picked.append(item['_digest'])
            records[key]={'status':'published','image_attached':True,'sticker_digest':item['_digest'],'sticker_style':item['style']}
        assert len(set(picked))==8
        # Recognize and reject retired artwork by bytes, including renamed copies.
        retired=root/'renamed.png';Image.new('RGB',(10,10),'white').save(retired)
        index=root/'old-index.json';index.write_text(json.dumps([{'id':'old','name':'old','file':str(retired),'enabled':True,'style':'official_portrait'}]))
        with patch('app.sticker_policy._digest',return_value=next(iter(BLOCKED_HASHES))):
            assert fingerprint(retired) in BLOCKED_HASHES and not allowed({'style':'chibi'},retired)
            assert not StickerStore(index).enabled()
        # New imports need an explicit style confirmation; scanning cannot
        # silently approve arbitrary images.
        imported=root/'incoming';imported.mkdir()
        plain=root/'new.png';Image.new('RGB',(20,20),'red').save(plain)
        with patch.object(config_store,'STICKER_DIR',imported):
            item=store.add_from_file(plain,'新导入')
            assert item['style']=='pending' and item['id'] not in {s['id'] for s in store.enabled()}
            store.update(item['id'],{'style':'chibi'})
            assert item['id'] in {s['id'] for s in store.enabled()}
        # A real multi-frame GIF is attached to the original model request.
        gif=root/'animated.gif';frames=[Image.new('RGB',(40,40),c) for c in ('red','green','blue')]
        frames[0].save(gif,save_all=True,append_images=frames[1:],duration=80,loop=0)
        messages=[{'role':'user','content':'这个表情是什么意思'}]
        assert attach_images(messages,[gif],{'provider':'openai'})==3
        assert len(messages[0]['content'])==4
        content=gif.read_bytes();digest=hashlib.md5(content).hexdigest()
        xml=f'<msg><emoji md5="{digest}" caption="测试表情" cdnurl="https://example.com/emoji?auth=private" /></msg>'
        old_context=build_context_messages([{'role':'user','content':xml,'sender':'群友'}])
        assert 'auth=private' not in str(old_context) and '微信表情包' in str(old_context)
        from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
        key=bytes(range(16));pad=16-len(content)%16
        encrypt=Cipher(algorithms.AES(key),modes.CBC(key)).encryptor()
        encrypted=encrypt.update(content+bytes([pad])*pad)+encrypt.finalize()
        assert image_bytes(encrypted,key.hex(),digest)==content
        import zstandard
        compressed=zstandard.ZstdCompressor().compress(xml.encode())
        assert '<emoji' in payload_text(compressed)
        db=SimpleNamespace(get_message_row=Mock(return_value={'content':compressed}),_open=Mock(side_effect=RuntimeError('no local caption table')))
        reader=WeChatEmojiReader(db,root/'cache')
        msg={'username':'test','local_id':12,'shard':'message_1.db','content':'[动画表情]'}
        with patch('app.wechat_emojis.fetch_emoji',return_value=content) as fetch:
            path,label=reader.read(msg)
            assert Path(path).read_bytes()==content and '测试表情' in label and 'auth' not in label
            assert reader.read(msg)[0]==path and fetch.call_count==1
            assert db.get_message_row.call_args.kwargs['shard']=='message_1.db'
        # No raw XML, credentials or wrong cached pictures reach the model.
        bad=WeChatEmojiReader(db,root/'failed')
        with patch('app.wechat_emojis.fetch_emoji',return_value=b'not an image'):
            path,label=bad.read(msg)
            assert not path and '<emoji' not in label and 'auth' not in label
        with patch('app.wechat_emojis.urllib.request.build_opener') as network:
            try:fetch_emoji('http://127.0.0.1/private')
            except ValueError:pass
            else:raise AssertionError('private URL accepted')
            network.assert_not_called()
    print('STICKER STYLE, DIARY ROTATION AND NATIVE EMOJI TESTS PASSED')


if __name__=='__main__':main()
