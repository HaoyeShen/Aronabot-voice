"""Complete installed WeChat sticker pages and exact local artwork previews."""
from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
import threading

from .paths import DATA
from .wechat_emojis import image_bytes, WeChatEmojiReader

log=logging.getLogger(__name__)


class NativeStickers:
    def __init__(self,db,folder=None):
        self.db=db;self.folder=Path(folder or DATA/'images'/'native_stickers')
        self.entries={};self.pages=[];self.signature='';self._lock=threading.RLock()

    def catalog(self):
        with self._lock:
            conn=self.db._open('emoticon\\emoticon.db')
            entries={};pages=[];unsupported=[]
            try:
                favorites=conn.execute('SELECT o.md5,e.caption FROM kFavEmoticonOrderTable o LEFT JOIN kNonStoreEmoticonTable e ON e.md5=o.md5 ORDER BY o.rowid').fetchall()
                groups=[('favorite','收藏的表情',[(str(r[0]),str(r[1] or ''),i+1,0,0,0,0) for i,r in enumerate(favorites)])]
                packages=conn.execute('SELECT package_id_,package_name_ FROM kStoreEmoticonPackageTable WHERE download_status_=1 AND COALESCE(remove_time_,0)=0 ORDER BY sort_order_,package_id_').fetchall()
                for package_id,name in packages:
                    rows=conn.execute('SELECT f.md5_,(SELECT caption_ FROM kStoreEmoticonCaptionsTable c WHERE c.md5_=f.md5_ AND c.package_id_=f.package_id_ ORDER BY CASE WHEN language_ LIKE \'zh%\' THEN 0 ELSE 1 END LIMIT 1),f.sort_order_,f.emoticon_offset_,f.emoticon_size_,f.thumb_offset_,f.thumb_size_ FROM kStoreEmoticonFilesTable f WHERE f.package_id_=? ORDER BY f.sort_order_,f.md5_',(package_id,)).fetchall()
                    groups.append((str(package_id),str(name),rows))
                for package,name,rows in groups:
                    items=[]
                    for row in rows:
                        digest=str(row[0] or '').lower()
                        if len(digest)!=32 or any(c not in '0123456789abcdef' for c in digest):
                            unsupported.append({'page':name,'reason':'特殊互动/管理项没有固定图片MD5，不作为普通动画表情候选'})
                            continue
                        identity='wxemote_'+digest
                        location={'page':package,'page_name':name,'order':int(row[2]),
                                  'offset':int(row[3]),'size':int(row[4]),'thumb_offset':int(row[5]),'thumb_size':int(row[6])}
                        entry=entries.setdefault(identity,{'id':identity,'md5':digest,'name':str(row[1] or '') or '未命名表情',
                                                           'kind':'wechat_native','enabled':True,'locations':[]})
                        entry['locations'].append(location)
                        items.append({'id':identity,'name':entry['name'],'order':int(row[2])})
                    pages.append({'id':package,'name':name,'items':items})
            finally:conn.close()
            self.entries,self.pages=entries,pages
            self.signature=hashlib.sha256(json.dumps(pages,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            log.info('微信动画表情全页目录 · 页面=%d · 条目=%d · 去重=%d',len(pages),sum(len(p['items']) for p in pages),len(entries))
            return {'complete':True,'signature':self.signature,'page_count':len(pages),
                    'count':len(entries),'pages':pages,'unavailable':unsupported,'note':'这些是微信原生表情包，不是Unicode emoji或普通图片。目录含全部已安装页，选择前必须用预览工具查看实际画面。'}

    def _package_bytes(self,location,thumb=False):
        from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
        if self.db.cfg_dword is None:raise ValueError('当前会话没有表情资源密钥材料')
        key=hashlib.md5(f'{self.db.cfg_dword}{self.db.wxid}EMOTICON'.encode()).digest()
        name=hashlib.md5(location['page'].encode()).hexdigest()
        path=Path(self.db.account_dir)/'business'/'emoticon'/('ThumbStore' if thumb else 'PersistStore')/name[:2]/name
        if path.stat().st_size>64*1024*1024:raise ValueError('表情资源容器过大')
        content=path.read_bytes()
        if len(content)%16:raise ValueError('表情资源容器格式不完整')
        decoder=Cipher(algorithms.AES(key),modes.CBC(key)).decryptor()
        plain=decoder.update(content)+decoder.finalize()
        start=location['thumb_offset'] if thumb else location['offset']
        size=location['thumb_size'] if thumb else location['size']
        if start<0 or not 0<size<=8*1024*1024 or start+size>len(plain):raise ValueError('表情资源偏移无效')
        return plain[start:start+size]

    def preview(self,identity):
        with self._lock:
            entry=self.entries.get(identity)
            if not entry:raise ValueError('动画表情不在本次全页目录中')
            self.folder.mkdir(parents=True,exist_ok=True)
            path=self.folder/(entry['md5']+'.image')
            if path.is_file():
                try:image_bytes(path.read_bytes(),expected=entry['md5']);return str(path),entry,False
                except (OSError,ValueError):pass
            for location in entry['locations']:
                if location['page']=='favorite':
                    # Resolve this favorite by exact MD5, not a nearby item.
                    reader=WeChatEmojiReader(self.db,self.folder)
                    image,label=reader.read({'content':f'<msg><emoji md5="{entry["md5"]}"/></msg>'})
                    if image:return image,entry,'仅缩略图' in label
                    continue
                for thumb in (False,True):
                    try:
                        data=self._package_bytes(location,thumb)
                        data=image_bytes(data,expected='' if thumb else entry['md5'])
                        dest=path if not thumb else self.folder/(entry['md5']+'.thumb.image')
                        dest.write_bytes(data)
                        return str(dest),entry,thumb
                    except Exception:continue
            raise ValueError('该动画表情画面暂不可读取，不能盲选发送')

    def preview_strip(self,identity):
        from PIL import Image
        image,entry,thumb=self.preview(identity)
        dest=self.folder/(entry['md5']+'.preview.png')
        with Image.open(image) as source:
            count=min(600,getattr(source,'n_frames',1));frames=list(dict.fromkeys((0,count//2,count-1)))
            strip=Image.new('RGB',(240*len(frames),240),'white')
            for number,frame in enumerate(frames):
                source.seek(frame);part=source.convert('RGBA');part.thumbnail((240,240))
                strip.paste(part,(number*240+(240-part.width)//2,(240-part.height)//2),part)
            strip.save(dest)
        return str(dest),entry,thumb
