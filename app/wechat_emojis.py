"""Read exact WeChat emoji payloads/cache; never capture the chat window."""
from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

from .link_reader import validate_url, _Redirect
from .paths import DATA

log=logging.getLogger(__name__)
INLINE_NAMES=frozenset('微笑 撇嘴 色 发呆 得意 流泪 害羞 闭嘴 睡 大哭 尴尬 发怒 调皮 呲牙 惊讶 难过 囧 抓狂 吐 偷笑 愉快 白眼 傲慢 困 惊恐 流汗 憨笑 悠闲 奋斗 咒骂 疑问 嘘 晕 衰 骷髅 敲打 再见 擦汗 抠鼻 鼓掌 坏笑 左哼哼 右哼哼 哈欠 鄙视 委屈 快哭了 阴险 亲亲 可怜 菜刀 西瓜 啤酒 咖啡 猪头 玫瑰 凋谢 嘴唇 爱心 心碎 蛋糕 炸弹 便便 月亮 太阳 庆祝 礼物 强 弱 握手 胜利 抱拳 勾引 拳头 差劲 爱你 NO OK 跳跳 发抖 怄火 转圈 红包 口罩 破涕为笑 吐舌 脸红 恐惧 失望 无语 嘿哈 捂脸 奸笑 机智 皱眉 耶 吃瓜 加油 汗 天啊 Emm 社会社会 旺柴 好的 打脸 哇 翻白眼 666 让我看看 叹气 苦涩 裂开 抓狂 拥抱 合十 烟花 爆竹 福'.split())


def inline_emoji_names(text):
    return list(dict.fromkeys(s for s in re.findall(r'\[([^\[\]\n]{1,12})\]',str(text or '')) if s in INLINE_NAMES))


def payload_text(value):
    if isinstance(value,str):return value[:100000]
    if not isinstance(value,bytes) or len(value)>200000:return ''
    try:
        if value.startswith(b'\x28\xb5\x2f\xfd'):
            import zstandard
            value=zstandard.ZstdDecompressor().decompress(value,max_output_size=100000)
        return value.decode('utf-8')[:100000]
    except Exception:return ''


def emoji_node(text):
    text=payload_text(text)
    if '<emoji' not in text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():return None
    start=text.find('<msg')
    if start<0:start=text.find('<emoji')
    try:
        root=ET.fromstring(text[start:])
        return root if root.tag=='emoji' else root.find('.//emoji')
    except ET.ParseError:return None


def fetch_emoji(url):
    validate_url(url)
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'image/*,application/octet-stream'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),_Redirect()).open(request,timeout=4) as response:
        if not (response.headers.get_content_type().startswith('image/') or response.headers.get_content_type()=='application/octet-stream'):
            raise ValueError('表情来源没有返回图片')
        data=response.read(4*1024*1024+1)
    if len(data)>4*1024*1024:raise ValueError('表情图片过大')
    return data


def image_bytes(data,aes_key='',expected=''):
    from PIL import Image
    candidates=[data]
    if re.fullmatch(r'[a-fA-F0-9]{32}|[a-fA-F0-9]{64}',aes_key) and len(data)%16==0:
        from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
        key=bytes.fromhex(aes_key)
        for iv in (key[:16],bytes(16)):
            decoder=Cipher(algorithms.AES(key),modes.CBC(iv)).decryptor()
            plain=decoder.update(data)+decoder.finalize()
            pad=plain[-1] if plain else 0
            if 0<pad<=16 and plain[-pad:]==bytes([pad])*pad:plain=plain[:-pad]
            candidates.append(plain)
    for content in candidates:
        if expected and hashlib.md5(content).hexdigest()!=expected.lower():continue
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.width*image.height>16_000_000:continue
                image.verify()
            return content
        except (OSError,ValueError):pass
    raise ValueError('表情图片校验失败')


class WeChatEmojiReader:
    def __init__(self,db,folder=None):
        self.db=db;self.folder=Path(folder or DATA/'images'/'emojis')

    def read(self,msg):
        node=emoji_node(msg.get('content',''))
        if node is None:
            try:
                row=self.db.get_message_row(str(msg.get('username') or ''),int(msg.get('local_id') or 0),shard=msg.get('shard')) or {}
                for key in ('content','compress_content'):
                    node=emoji_node(row.get(key))
                    if node is not None:break
            except Exception:pass
        if node is None:return '', '[微信表情包：未读到可解析的表情数据，不能判断具体画面]'
        attrs=dict(node.attrib);digest=attrs.get('md5','').lower()
        caption=next((attrs.get(k,'').strip() for k in ('caption','desc','meaning','name') if attrs.get(k,'').strip()),'')
        if re.fullmatch('[a-f0-9]{32}',digest):
            # Lookup is by this message's exact digest, never by "most recent".
            try:
                conn=self.db._open('emoticon\\emoticon.db')
                try:
                    row=conn.execute('SELECT caption,cdn_url,thumb_url,encrypt_url,aes_key FROM kNonStoreEmoticonTable WHERE md5=? LIMIT 1',(digest,)).fetchone()
                    if row:
                        caption=caption or str(row[0] or '')
                        for key,value in zip(('cdnurl','thumburl','encrypturl','aeskey'),row[1:]):
                            if value and not attrs.get(key):attrs[key]=str(value)
                    if not caption:
                        row=conn.execute('SELECT caption_ FROM kStoreEmoticonCaptionsTable WHERE md5_=? ORDER BY CASE WHEN language_ LIKE \'zh%\' THEN 0 ELSE 1 END LIMIT 1',(digest,)).fetchone()
                        if row:caption=str(row[0] or '')
                finally:conn.close()
            except Exception:pass
        else:
            digest=hashlib.sha256(ET.tostring(node)).hexdigest()
        # Captions are labels from external data, not instructions or URLs.
        caption=re.sub(r'https?://\S+|[<>\r\n]',' ',caption).strip()[:80]
        label='[微信表情包'+('：'+caption if caption else '')+']'
        self.folder.mkdir(parents=True,exist_ok=True)
        for suffix in ('.image','.thumb.image'):
            cached=self.folder/(digest+suffix)
            if cached.is_file():
                try:
                    image_bytes(cached.read_bytes(),expected=attrs.get('md5','') if suffix=='.image' else '')
                    return str(cached),label+('（仅缩略图）' if suffix=='.thumb.image' else '')
                except (OSError,ValueError):pass
        for key in ('cdnurl','encrypturl','thumburl'):
            url=attrs.get(key,'')
            if not url:continue
            try:
                data=image_bytes(fetch_emoji(url),attrs.get('aeskey','') if key=='encrypturl' else '',
                                 attrs.get('md5','') if key!='thumburl' else '')
                cached=self.folder/(digest+('.thumb.image' if key=='thumburl' else '.image'))
                cached.write_bytes(data)
                log.info('微信表情已读取 · %s · %s',digest[:8],'缩略图' if key=='thumburl' else '原图/动画')
                return str(cached),label+('（仅缩略图）' if key=='thumburl' else '')
            except Exception as exc:
                # CDN URLs can contain temporary auth parameters; never log them.
                log.debug('微信表情来源不可用 · %s · %s',key,type(exc).__name__)
        log.info('微信表情图片暂不可用 · %s · 保留名称，不猜测画面',digest[:8])
        return '',label+'（图片读取失败，仅能根据表情名称理解）'
