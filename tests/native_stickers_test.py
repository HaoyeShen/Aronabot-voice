"""All-page enumeration, exact artwork previews and model choice gates."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app.native_stickers import NativeStickers
from app.native_sticker_panel import NativeStickerPanel,alt_e
from app.agent import AgentRunner,AgentTool
from app.model_client import LLMResult


def main():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);path=root/'emote.db'
        conn=sqlite3.connect(path)
        conn.executescript('''
        CREATE TABLE kFavEmoticonOrderTable(md5 TEXT);
        CREATE TABLE kNonStoreEmoticonTable(md5 TEXT,caption TEXT);
        CREATE TABLE kStoreEmoticonPackageTable(package_id_ TEXT,package_name_ TEXT,download_status_ INTEGER,remove_time_ INTEGER,sort_order_ INTEGER);
        CREATE TABLE kStoreEmoticonFilesTable(package_id_ TEXT,md5_ TEXT,sort_order_ INTEGER,emoticon_offset_ INTEGER,emoticon_size_ INTEGER,thumb_offset_ INTEGER,thumb_size_ INTEGER);
        CREATE TABLE kStoreEmoticonCaptionsTable(package_id_ TEXT,md5_ TEXT,language_ TEXT,caption_ TEXT);
        ''')
        buffer=io.BytesIO();Image.new('RGB',(30,30),'cyan').save(buffer,format='GIF');image=buffer.getvalue();digest=hashlib.md5(image).hexdigest()
        for number in range(6):
            conn.execute('INSERT INTO kStoreEmoticonPackageTable VALUES(?,?,1,0,?)',(f'pack{number}',f'第{number+1}页',number))
            value=digest if number==5 else hashlib.md5(str(number).encode()).hexdigest()
            conn.execute('INSERT INTO kStoreEmoticonFilesTable VALUES(?,?,1,0,?,0,?)',(f'pack{number}',value,len(image),len(image)))
            conn.execute('INSERT INTO kStoreEmoticonCaptionsTable VALUES(?,?,?,?)',(f'pack{number}',value,'zh_CN',f'语义{number}'))
        conn.execute('INSERT INTO kFavEmoticonOrderTable VALUES(?)',('interactive_not_a_fixed_image',));conn.commit();conn.close()
        db=SimpleNamespace(_open=lambda _:sqlite3.connect(path))
        store=NativeStickers(db,root/'cache');catalog=store.catalog()
        assert catalog['complete'] and catalog['page_count']==7 and catalog['count']==6
        assert len(catalog['pages'][-1]['items'])==1 and catalog['unavailable']
        last=catalog['pages'][-1]['items'][0]['id']
        with patch.object(store,'_package_bytes',return_value=image):
            preview,entry,thumb=store.preview_strip(last)
            assert Path(preview).is_file() and entry['name']=='语义5' and not thumb
        try:store.preview('wxemote_unknown')
        except ValueError:pass
        else:raise AssertionError('unknown item accepted')
    # The held chord must release both keys, including failure paths.
    user=SimpleNamespace(keybd_event=Mock())
    with patch('app.native_sticker_panel.ctypes.windll',SimpleNamespace(user32=user)),patch('app.native_sticker_panel.time.sleep'):
        alt_e()
    assert [c.args for c in user.keybd_event.call_args_list]==[(0x12,0,0,0),(0x45,0,0,0),(0x45,0,2,0),(0x12,0,2,0)]
    outputs=iter([LLMResult('{"action":"tool","tool":"all_pages","arguments":{"query":"all"}}','fixture','mock',1),
                  LLMResult('{"action":"final","text":"恭喜呀。","sticker":{"id":"from_last_page","style":"chibi","reason":"庆祝语境"}}','fixture','mock',1)])
    run=AgentRunner().run(lambda *args:next(outputs),[], '',[AgentTool('all_pages','query','读取全部页面',lambda *args:'全部6页')])
    assert run.sticker['id']=='from_last_page' and run.result.text=='恭喜呀。'
    # A duplicate caption within a page must never choose an arbitrary cell.
    panel=NativeStickerPanel(SimpleNamespace())
    with patch.object(panel,'page',return_value=SimpleNamespace()):
        try:panel.target({'name':'重复'},{'id':'pack','name':'包','items':[{'name':'重复'},{'name':'重复'}]})
        except ValueError:pass
        else:raise AssertionError('ambiguous native sticker accepted')
    print('NATIVE STICKER ALL-PAGE AND SELECTION TESTS PASSED')


if __name__=='__main__':main()
