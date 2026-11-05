"""Daily public diary: aggregate own activities, draft first, publish once."""
from __future__ import annotations

from datetime import datetime,timezone,timedelta
import json
import logging
from pathlib import Path
import re
import threading
import time
import hashlib

from .agent_runtime import RUNTIME
from .config_store import _atomic_write_json as atomic_write_json
from .defaults import default_stickers
from .model_audit import model_call
from .model_client import make_client
from .paths import DATA,ROOT
from .prompting import build_compact_system_prompt
from .sticker_policy import allowed as sticker_allowed, fingerprint

log=logging.getLogger(__name__)
CHINA=timezone(timedelta(hours=8))


def diary_now():
    return datetime.now(CHINA)


class DailyDiary:
    def __init__(self,service,path=None):
        self.service=service
        self.path=Path(path or DATA/'diary_state.json')
        self.folder=self.path.parent/'diaries'
        self.lock=threading.Lock()
        self.retry_after=0

    def load(self):
        if not self.path.exists():return {}
        # Corrupt state must stop posting, rather than risking a duplicate.
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):raise ValueError('日记状态损坏，请核对备份')
        return data

    def activity(self,now):
        start=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        counts=dict(replies=0,voices=0,news=0,shares=0,web_queries=0,moments_comments=0)
        private=set()
        memory=self.service.memory
        with memory._lock:
            rows=memory._conn.execute('SELECT role,chat,sender,meta FROM messages WHERE created_at>=? AND created_at<=?',(start,now.timestamp())).fetchall()
        for row in rows:
            private.update(str(v) for v in (row['chat'],row['sender']) if v)
            if row['role']!='assistant':continue
            try:meta=json.loads(row['meta'] or '{}')
            except (ValueError,TypeError):meta={}
            counts['news']+=bool(meta.get('news'))
            counts['shares']+=bool(meta.get('proactive'))
            counts['replies']+=not meta.get('news') and not meta.get('proactive')
            counts['voices']+=len(meta.get('voice_bubbles') or []) or bool(meta.get('voice_sent'))
            counts['web_queries']+=bool((meta.get('web_search') or {}).get('searched'))
        try:
            reader=getattr(self.service,'moments_reader',None)
            if reader:
                counts['moments_comments']=sum(r.get('status')=='sent' and start<=r.get('at',0)<=now.timestamp()
                                               for r in reader.load().values())
        except Exception:
            log.debug('日记暂未计入朋友圈评论统计')
        return counts,private

    def sticker_options(self, records, key):
        store=getattr(self.service,'stickers',None)
        items=store.enabled() if store else default_stickers()
        choices={}
        for item in items:
            path=store.path_for(item) if store else ROOT/item['file']
            if not sticker_allowed(item,path):continue
            digest=fingerprint(path)
            choices.setdefault(digest,{**item,'_path':path,'_digest':digest})
        history=[r for date,r in sorted(records.items()) if date!=key and r.get('status') in ('published','uncertain','publishing')
                 and r.get('image_attached')]
        recent={r.get('sticker_digest') for r in history[-3:]}
        pool=[s for digest,s in choices.items() if digest not in recent] or list(choices.values())
        last_style=history[-1].get('sticker_style') if history else None
        def rank(item):
            uses=sum(r.get('sticker_digest')==item['_digest'] for r in history)
            tie=hashlib.sha256((key+item['id']).encode()).hexdigest()
            return uses,item.get('style')==last_style,tie
        return sorted(pool,key=rank)[:8]

    def prepare(self,now=None):
        now=now or diary_now();key=now.date().isoformat()
        records=self.load()
        facts,private=self.activity(now)
        if key in records and (records[key].get('sealed') or records[key].get('status')!='draft'
                               or records[key].get('facts')==facts):return records[key]
        s=self.service;persona=s.personas.get('arona')
        options=self.sticker_options(records,key)
        profile=dict(s.config.active_profile());profile.update(max_tokens=700,think=False)
        system=build_compact_system_prompt(persona,[],max_chars=600,max_sentence_chars=60,stickers_enabled=False)
        system+=('\n你在写阿洛娜的今日日记，发到自己的朋友圈。用第一人称、温柔元气的中文写80到300字，'
                 '这是日记，不受普通聊天气泡限制。仅根据当天活动统计自然回顾，不写流水账或机械报数字。'
                 '没有发生的事不能编造；安静的一天也可以简单写。不要虚构线下经历、身体动作或人类身份。'
                 '不写群名、微信昵称、个人经历、聊天原文、密码、API Key、模型名和内部配置。'
                 '只输出日记正文，不输出分析。')
        if options:
            system+=('\n配图仅从以下已审核画风的候选中选一张，结合日记语气；'
                     '正文末尾另起一行 [DIARY_STICKER:候选id]，程序会去掉标记。候选：'
                     +json.dumps([{'id':i['id'],'name':i['name'],'emotion':i.get('emotion'),
                                   'description':i.get('description','')[:100]} for i in options],ensure_ascii=False))
        with RUNTIME.task('今日日记','朋友圈日记'):
            with RUNTIME.context('provided'):
                result=model_call(make_client(profile),'chat','今日日记：当天活动总结',
                    [{'role':'user','content':json.dumps({'date':key,'activities':facts},ensure_ascii=False)}],system)
            s._record_usage(result,'朋友圈日记','diary')
        text=str(result.text or '').strip()
        chosen=re.findall(r'\[DIARY_STICKER:([\w-]+)\]',text)
        text=re.sub(r'\[DIARY_STICKER:[^\]\n]*\]','',text).strip()
        if 'DIARY_STICKER' in text:raise ValueError('日记配图标记不完整')
        sticker=next((s for s in options if chosen and s['id']==chosen[-1]),options[0] if options else None)
        if not 10<=len(text)<=1000:raise ValueError('日记正文长度不合格')
        for name in sorted(private,key=len,reverse=True):
            if len(name)>=2:text=text.replace(name,'老师')
        if re.search(r'gh[pousr]_|sk-[\w-]{12,}|wxid_|@chatroom|https?://|\b1[3-9]\d{9}\b|<appmsg|API\s*key',text,re.I):
            raise ValueError('日记包含不适合公开的信息，已取消发布')
        text=f'【阿洛娜的今日日记 · {key}】\n'+text
        record={'date':key,'text':text,'status':'draft','facts':facts,'created_at':now.isoformat()}
        if sticker:record['sticker_id']=sticker['id']
        self.folder.mkdir(parents=True,exist_ok=True)
        (self.folder/(key+'.md')).write_text(text+'\n',encoding='utf-8')
        records[key]=record;atomic_write_json(self.path,records)
        return record

    def preview(self):
        if not self.lock.acquire(False):return {'ok':False,'message':'日记任务正在运行'}
        try:
            record=self.prepare()
            return {'ok':True,'text':record['text'],'message':'日记已保存，预览不会发布朋友圈。','status':record['status']}
        except Exception as exc:
            log.warning('日记预览失败：%s',type(exc).__name__)
            return {'ok':False,'message':'日记生成失败，请查看日志。'}
        finally:self.lock.release()

    def publish_now(self,attach_sticker=None):
        return self.tick(manual=True,attach_sticker=attach_sticker) or {'ok':False,'message':'日记尚未发布，请先启动机器人并连接微信。'}

    def tick(self,now=None,*,manual=False,attach_sticker=None):
        now=now or diary_now();s=self.service
        if not s._running:return {'ok':False,'message':'请先启动机器人并连接微信。'}
        if not manual and (not s.config.bot.get('diary_enabled',False) or time.monotonic()<self.retry_after):return
        if s._reply_busy or not s._incoming.empty():return {'ok':False,'message':'机器人正在处理消息，请稍后点击发布。'}
        if not manual and now.strftime('%H:%M')<s.config.bot.get('diary_time','22:00'):return
        if not self.lock.acquire(False):return {'ok':False,'message':'日记任务正在运行，请稍候。'}
        try:
            records=self.load();key=now.date().isoformat()
            status=records.get(key,{}).get('status')
            if status=='published':return {'ok':True,'already_published':True,'message':'今天的日记已发布，不会重复发表。'}
            if status in ('publishing','uncertain'):
                return {'ok':False,'message':'上次发表结果尚未确认，请先核对朋友圈；已阻止重复发表。','status':status}
            adapter=s.adapter
            if not adapter or not adapter.connected:return {'ok':False,'message':'微信尚未连接，日记未发布。'}
            record=self.prepare(now)
            options=self.sticker_options(records,key)
            image=None
            if attach_sticker if attach_sticker is not None else s.config.bot.get('diary_attach_sticker',True):
                chosen=next((item for item in options if item['id']==record.get('sticker_id')),options[0] if options else None)
                if chosen:
                    image=chosen['_path']
                    record.update(sticker_id=chosen['id'],sticker_digest=chosen['_digest'],sticker_style=chosen.get('style'))
                    log.info('日记配图 · %s · %s · 画风=%s',key,chosen['name'],chosen.get('style'))
            record['image_attached']=bool(image)
            # Flush intent before any network/UI commit. Crash recovery never
            # automatically resends a post with an uncertain outcome.
            records=self.load();record['status']='publishing';record['sealed']=True;records[key]=record
            atomic_write_json(self.path,records)
            with s._adapter_lock:
                if adapter is not s._adapter or not s._running:
                    record['status']='draft';records[key]=record;atomic_write_json(self.path,records);return
                if image and fingerprint(image)!=record.get('sticker_digest'):
                    image=None;record['image_attached']=False
                    records[key]=record;atomic_write_json(self.path,records)
                result=adapter.publish_moment(record['text'],image)
            record['status']='published' if result.ok else ('uncertain' if result.detail=='uncertain' else 'draft')
            record['message']=result.message;record['updated_at']=now.isoformat()
            records[key]=record;atomic_write_json(self.path,records)
            self.retry_after=time.monotonic()+300
            log.info('今日日记 · %s · %s',key,record['status'])
            if not manual:s.emit('diary_status',{'message':result.message,'ok':result.ok,'status':record['status']})
            return {'ok':result.ok,'message':result.message,'status':record['status'],'text':record['text']}
        except Exception as exc:
            self.retry_after=time.monotonic()+300
            log.warning('今日日记任务未完成：%s',type(exc).__name__)
            return {'ok':False,'message':'日记发布未完成，草稿已保留，请查看日志。'}
        finally:self.lock.release()
