"""Reply age across generation, duplicate deliveries, independent roleplay toggle."""
import json
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore,PersonaStore,StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.model_client import LLMResult
from app.model_audit import model_call
from app.wechat_adapter import IncomingMessage,WeChatAutoAdapter
from app.reply_control import message_fresh,reply_current,fresh_reply,ReplyExpired
from app.prompting import build_system_prompt,build_context_messages
from app.defaults import ARONA_PERSONA
from tests.service_test import FakeAdapter


def main():
    persona=deepcopy(ARONA_PERSONA);saved=deepcopy(persona)
    for legacy in (False,True):
        normal=build_system_prompt(persona,[],legacy=legacy)
        free=build_system_prompt(persona,[],legacy=legacy,free_mode=True)
        assert '不假装自己是人类' in normal and '不假装自己是人类' not in free
        assert '始终保持当前选定角色' in free and '不自动切换人格' in free
        assert '新闻' in free and '不冒认现实中特定真人身份' in free
    assert persona==saved,'roleplay must not mutate persisted persona or voice'
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);cfg=ConfigStore(root/'config.json');cfg.add_target('test','group','arona')
        cfg.set_active_profile('demo-local')
        cfg.bot.update(auto_sticker=False,voice_enabled=False,reply_delay_min=0,reply_delay_max=0,
                       max_reply_chars=25,max_sentence_chars=25,stale_reply_minutes=1)
        memory=MemoryStore(root/'m.db');training=TrainingStore(root/'t.db',root/'files')
        service=BotService(cfg,PersonaStore(root/'p.json'),StickerStore(root/'s.json'),memory,training,admin_state_path=root/'admin.json')
        adapter=FakeAdapter(cfg.wechat,lambda _:None);service._adapter=adapter;service._running=True
        now=time.time();clock=[now]
        incoming=IncomingMessage('test','甲','@阿洛娜 怎么处理',chat_type='group',received_at=now)
        def reply(text):return LLMResult(text,'fixture','openai',1),{},'规则',{}
        try:
            assert message_fresh(incoming,cfg.bot,now+59)
            assert not message_fresh(incoming,cfg.bot,now+61)
            cfg.bot['roleplay_free_mode']=True
            assert not message_fresh(incoming,cfg.bot,now+61),'free mode cannot bypass freshness'
            cfg.bot['roleplay_free_mode']=False
            old=IncomingMessage('test','甲','旧问题',chat_type='group',received_at=now-100)
            old_id=service._store_user(old,old.content)
            assert abs(memory.recent('test',1)[0]['created_at']-old.received_at)<.01
            context=build_context_messages(memory.recent('test',10),target_type='group')
            assert '过期消息' in str(context)
            with patch.object(service,'_model_reply') as model:
                service._generate_and_send(old,cfg.target_by_name('test'),old_id,old.content,True)
                assert not model.called and not adapter.sent
            row=service._store_user(incoming,incoming.content)
            def delayed(*args,**kwargs):clock[0]=now+61;return reply('生成时尚未过期的回答。')
            with patch('app.reply_control.time.time',side_effect=lambda:clock[0]),patch.object(service,'_model_reply',side_effect=delayed):
                service._generate_and_send(incoming,cfg.target_by_name('test'),row,incoming.content,True)
            assert not adapter.sent,'a reply that expires during model work must not send'
            clock[0]=now
            with patch.object(service,'_model_reply',return_value=reply('这一步先把开关关掉。\n这一步先把开关关掉。')) as model:
                service._generate_and_send(incoming,cfg.target_by_name('test'),row,incoming.content,True)
                assert len(adapter.sent)==1,'duplicate model bubbles must send only once'
                service._generate_and_send(incoming,cfg.target_by_name('test'),row,incoming.content,True)
                assert model.call_count==1 and len(adapter.sent)==1,'same input row must not reply twice'
            assert service._unique_reply_bubbles(['甲','甲'],IncomingMessage('test','甲','再说一遍'))==['甲','甲']
            assert service._reply_key('3.14') != service._reply_key('314')
            assert service._reply_recently_delivered('test','这一步先把开关关掉。\n这一步先把开关关掉。',persona)
            other=IncomingMessage('test','甲','接着呢',chat_type='group',received_at=now)
            next_id=service._store_user(other,other.content)
            def send_then_age(chat,text):adapter.sent.append((chat,text));clock[0]=now+61;return SimpleNamespace(ok=True,message='已发送')
            with patch('app.reply_control.time.time',side_effect=lambda:clock[0]),patch.object(service,'_model_reply',return_value=reply('第一条新信息。\n第二条新信息。')),patch.object(adapter,'send_text',side_effect=send_then_age):
                service._generate_and_send(other,cfg.target_by_name('test'),next_id,other.content,True)
            assert len(adapter.sent)==2 and memory.recent('test',1)[0]['content']=='第一条新信息。'
            # A stale nested API request must stop without touching the client.
            clock[0]=now
            @fresh_reply
            def task(s,msg):
                clock[0]=now+61
                model_call(client,'chat','回复消息测试',[{'role':'user','content':'问题'}],'规则')
            client=Mock()
            with patch('app.reply_control.time.time',side_effect=lambda:clock[0]):
                try:task(service,incoming);raise AssertionError('expired API call allowed')
                except ReplyExpired:pass
            assert not client.chat.called and reply_current(),'freshness guard must not leak between tasks'
            native=WeChatAutoAdapter(cfg.wechat,lambda _:None);native.self_wxid='self'
            native._db=SimpleNamespace(get_messages=lambda *args,**kwargs:[{
                'sort_seq':8,'sender_username':'self','content':'<msg><appmsg><title>已发送的具体回复</title><refermsg><content>旧引用</content></refermsg></appmsg></msg>'}])
            assert native._exact_outgoing_after('group',7,'已发送的具体回复')
        finally:
            service._running=False;memory.close();training.close()
    print('REPLY AGE, ROLEPLAY AND DEDUPLICATION TESTS PASSED')


if __name__=='__main__':main()
