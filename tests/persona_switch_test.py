"""Role-defined addressing and switching isolation; no WeChat/network I/O."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.model_client import LLMResult
from app.wechat_adapter import IncomingMessage, SendResult
from app.prompting import build_system_prompt, build_compact_system_prompt, build_context_messages, parse_reply, address_name
from app.reply_fallbacks import fallback_reply
from app.ui.persona_labels import persona_labels


def main():
    ami = {'id':'ami', 'name':'阿米娅', 'system_prompt':'你是阿米娅，称呼对方为博士。', 'description':'罗德岛的领袖。'}
    chen = {'id':'chen', 'name':'陈千语', 'system_prompt':'你是陈千语，称呼对方为朋友。', 'description':'温和的朋友。'}
    for builder in (build_system_prompt, build_compact_system_prompt):
        prompt = builder(ami, [], target_type='group', recipient_name='小明')
        assert '称呼对方为博士' in prompt and '小明' in prompt
        assert all(w not in prompt for w in ['都要用「对方微信昵称+老师」', '不要只叫「老师」', '不要只叫老师'])
    assert address_name('小明', {**ami,'address_template':'{nickname}博士'}) == '小明博士'
    assert '老师' not in build_context_messages([{'role':'user','sender':'小明','content':'你好'}], persona=ami)[0]['content']
    assert parse_reply('老师，告诉老师吧。', [], persona={'name':'教师角色'})[0] == '老师，告诉老师吧。'
    assert parse_reply('阿米娅：博士，我在。', [], persona=ami)[0] == '博士，我在。'
    for reason in ('network','unknown','greeting','comfort','help'):
        text = fallback_reply('你好','ami',reason,sender='小明',persona=ami)
        assert '老师' not in text and '阿洛娜' not in text
    assert '小明博士' in fallback_reply('你好','ami','greeting',sender='小明',persona={**ami,'address_template':'{nickname}博士'})
    assert len(set(persona_labels([ami,{**ami,'id':'ami2'}]))) == 2
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);config=ConfigStore(root/'config.json');config.set_active_profile('demo-local')
        config.bot.update(web_search_enabled=False,agent_enabled=False,voice_enabled=False,auto_sticker=False,
                          reply_delay_min=0,reply_delay_max=0,global_cooldown=0)
        people=PersonaStore(root/'personas.json');people.upsert(ami);people.upsert(chen)
        config.data['active_persona_id']='ami'
        memory=MemoryStore(root/'memory.db');training=TrainingStore(root/'training.db',root/'files')
        service=BotService(config,people,StickerStore(root/'stickers.json'),memory,training,admin_state_path=root/'admin.json')
        try:
            adapter=Mock();adapter.connected=True;adapter.self_name='ARONA';adapter.send_text.return_value=SendResult(True)
            service._adapter=adapter;service._running=True
            incoming=IncomingMessage('test','小明','你好',chat_type='group')
            row=service._store_user(incoming,'你好');calls=[]
            def chat(messages,system=''):
                calls.append(system)
                if len(calls)==1:
                    config.data['active_persona_id']='chen'
                    return LLMResult('博士你好。','fake','mock',0)
                return LLMResult('朋友你好。','fake','mock',0)
            fake=Mock();fake.chat.side_effect=chat
            with patch('app.service.make_client',return_value=fake):
                service._generate_and_send(incoming,{'name':'test','type':'group'},row,'你好',force_reply=True)
            assert len(calls)==2 and adapter.send_text.call_count==1
            assert '朋友' in adapter.send_text.call_args.args[1] and '博士' not in adapter.send_text.call_args.args[1]
            assert memory.recent('test',1)[0]['meta']['persona_id']=='chen'
            assert '陈千语' in service._role_names('test')
            config.data['targets']=[{'name':'test','type':'group','persona_id':'ami','enabled':True}]
            assert '阿米娅' in service._role_names('test')  # Per-group selection wins.
            config.bot['agent_enabled']=True
            agent=Mock();agent.chat.return_value=LLMResult('{"action":"final","text":"我在。"}','fake','openai',0)
            profile={'provider':'openai'}
            def run(persona):
                return service._model_reply(agent,[{'role':'user','content':'你好'}],'',profile,incoming,'你好',
                                            persona=persona,require_running=False)[3]
            first=run(ami);memory.add_assistant('test','我在。',meta={'web_search':first,'persona_id':'ami'})
            next_role=run(chen)
            assert next_role['agent']['turn']==1
            assert first['agent']['conversation_id']!=next_role['agent']['conversation_id']
            assert first['agent']['persona_key']!=next_role['agent']['persona_key']
            context=build_context_messages(memory.recent('test',8),persona=chen)
            assert any('不沿用其身份或称呼' in m['content'] for m in context)
        finally:
            service._running=False;memory.close();training.close()
    print('PERSONA SWITCH TESTS PASSED')


if __name__=='__main__':
    main()
