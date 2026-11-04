"""Daily public diary: aggregate own activities, draft first, publish once."""
from __future__ import annotations

from datetime import datetime,timezone,timedelta
import json
import logging
from pathlib import Path
import re
import threading
import time

from .agent_runtime import RUNTIME
from .config_store import _atomic_write_json as atomic_write_json
from .defaults import default_stickers
from .model_audit import model_call
from .model_client import make_client
from .paths import DATA,ROOT
from .prompting import build_compact_system_prompt

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
        counts=dict(replies=0,voices=0,news=0,shares=0,web_queries=0)
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
        return counts,private

    def prepare(self,now=None):
        now=now or diary_now();key=now.date().isoformat()
        records=self.load()
        facts,private=self.activity(now)
        if key in records and (records[key].get('sealed') or records[key].get('status')!='draft'
                               or records[key].get('facts')==facts):return records[key]
        s=self.service;persona=s.personas.get('arona')
        profile=dict(s.config.active_profile());profile.update(max_tokens=700,think=False)
        system=build_compact_system_prompt(persona,[],max_chars=600,max_sentence_chars=60,stickers_enabled=False)
        system+=('\n你在写阿洛娜的今日日记，发到自己的朋友圈。用第一人称、温柔元气的中文写80到300字，'
                 '这是日记，不受普通聊天气泡限制。仅根据当天活动统计自然回顾，不写流水账或机械报数字。'
                 '没有发生的事不能编造；安静的一天也可以简单写。不要虚构线下经历、身体动作或人类身份。'
                 '不写群名、微信昵称、个人经历、聊天原文、密码、API Key、模型名和内部配置。'
                 '只输出日记正文，不输出分析。')
        with RUNTIME.task('今日日记','朋友圈日记'):
            with RUNTIME.context('provided'):
                result=model_call(make_client(profile),'chat','今日日记：当天活动总结',
                    [{'role':'user','content':json.dumps({'date':key,'activities':facts},ensure_ascii=False)}],system)
            s._record_usage(result,'朋友圈日记','diary')
        text=str(result.text or '').strip()
        if not 10<=len(text)<=1000:raise ValueError('日记正文长度不合格')
        for name in sorted(private,key=len,reverse=True):
            if len(name)>=2:text=text.replace(name,'老师')
        if re.search(r'gh[pousr]_|sk-[\w-]{12,}|wxid_|@chatroom|https?://|\b1[3-9]\d{9}\b|<appmsg|API\s*key',text,re.I):
            raise ValueError('日记包含不适合公开的信息，已取消发布')
        text=f'【阿洛娜的今日日记 · {key}】\n'+text
        record={'date':key,'text':text,'status':'draft','facts':facts,'created_at':now.isoformat()}
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
            # Flush intent before any network/UI commit. Crash recovery never
            # automatically resends a post with an uncertain outcome.
            records=self.load();record['status']='publishing';record['sealed']=True;records[key]=record
            atomic_write_json(self.path,records)
            image=None
            if attach_sticker if attach_sticker is not None else s.config.bot.get('diary_attach_sticker',True):
                image=ROOT/default_stickers()[0]['file']
                if not image.is_file():image=None
            with s._adapter_lock:
                if adapter is not s._adapter or not s._running:
                    record['status']='draft';records[key]=record;atomic_write_json(self.path,records);return
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
