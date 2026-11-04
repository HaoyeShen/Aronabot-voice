"""Task views, intact conversation, provider cache counts and stable prefixes."""
import json
import sys
import tempfile
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.agent_runtime import AgentRuntime
from app.model_client import cache_usage,ModelClient
from app.memory import MemoryStore
from app.evidence_text import evidence_excerpt
from app.prompting import build_system_prompt,build_compact_system_prompt
from app.defaults import ARONA_PERSONA,PLANA_PERSONA


def main():
    runtime=AgentRuntime();requests=[]
    transcript=[{'role':'user' if i%2==0 else 'assistant','content':str(i)+'，完整聊天内容。'*25} for i in range(30)]
    client=SimpleNamespace(model='fixture',api_key='')
    def call(purpose,messages=None):
        return runtime.invoke(client,'chat',purpose,(messages or [{'role':'user','content':'当前任务'}],'固定规则'),{},
            lambda args,kwargs:requests.append(args) or SimpleNamespace(text='结果'))
    with runtime.task('同群共享任务','测试群') as task:
        runtime.set_dialogue(transcript,'重要的旧约定')
        for purpose in ['关键词是否对机器人说话','群聊回复对象判断','上下文摘要']:
            call(purpose)
            assert requests[-1]==([{'role':'user','content':'当前任务'}],'固定规则')
        call('回复消息：Agent 决策及回答')
        assert requests[-1][0][:-1]==transcript and '重要的旧约定' in requests[-1][1]
        # Wrapping a text turn with an image must not duplicate the transcript.
        image_messages=[dict(m) for m in transcript]
        image_messages[-1]['content']=[{'type':'text','text':transcript[-1]['content']},
                                     {'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}]
        call('回复消息：Agent 决策及回答',image_messages)
        assert requests[-1][0]==image_messages
        with runtime.context('recent'):
            call('统一分享Agent：判断/检索/审核/撰写')
            assert requests[-1][0][:-1]==transcript[-8:]
        with runtime.context('provided'):
            call('统一分享Agent：判断/检索/审核/撰写')
            assert len(requests[-1][0])==1 and '重要的旧约定' not in requests[-1][1]
        call('普通回复')
        assert requests[-1][0][:-1]==transcript,'context view must not leak to the next call'
        assert runtime.session_snapshot('测试群')==transcript,'views must not alter stored dialogue'
    assert len({e['agent_id'] for e in runtime.snapshot()})==1
    assert cache_usage({})=={}
    assert cache_usage({'prompt_tokens':100,'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20})=={'cache_hit':80,'cache_miss':20}
    assert cache_usage({'prompt_tokens':100,'prompt_tokens_details':{'cached_tokens':30}})=={'cache_hit':30,'cache_miss':70}
    assert cache_usage({'prompt_tokens':100,'prompt_cache_hit_tokens':'invalid'})=={}
    assert cache_usage({'prompt_tokens_details':['invalid']})=={}
    transport=ModelClient({'provider':'openai','model':'fixture','base_url':'https://invalid.test'})
    payload={'choices':[{'message':{'content':'你好'}}],
             'usage':{'prompt_tokens':100,'completion_tokens':10,'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20}}
    with patch.object(transport,'_with_retry',return_value=(payload,1)):
        answer=transport.chat([{'role':'user','content':'你好'}],'固定规则')
        assert answer.usage=={'prompt':100,'completion':10,'cache_hit':80,'cache_miss':20}
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'memory.db'
        # Real pre-migration usage table, including a historical row.
        db=sqlite3.connect(path)
        db.execute('''CREATE TABLE token_usage(id INTEGER PRIMARY KEY AUTOINCREMENT,chat TEXT,kind TEXT,
          provider TEXT,model TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,
          created_at REAL,created_iso TEXT)''')
        db.execute("INSERT INTO token_usage VALUES(1,'','reply','openai','fixture',100,10,110,0,'2026-01-01')")
        db.commit();db.close()
        store=MemoryStore(path)
        try:
            assert store.usage_totals()['total']==110 and store.usage_totals()['cache_hit'] is None
            store.record_usage('test','openai','fixture',100,10,cache_hit_tokens=80,cache_miss_tokens=20)
            totals=store.usage_totals()
            assert totals['total']==220 and totals['cache_hit']==80 and totals['cache_miss']==20
            store.record_usage('test','openai','fixture',50,5)
            assert store.usage_recent(1)[0]['cache_hit_tokens'] is None
        finally:store.close()
    document=('重复的导航信息。'*80)+'\nMon3tr 的关键能力是这一段。\n'+('其他资料。'*80)
    excerpt=evidence_excerpt(document,'Mon3tr 关键能力',200)
    assert 'Mon3tr 的关键能力是这一段。' in excerpt and excerpt.count('重复的导航信息。')<=1
    assert len(excerpt)<=200 and '重复的导航信息。'*80 in document
    for persona in (ARONA_PERSONA,PLANA_PERSONA):
        for build in (build_system_prompt,build_compact_system_prompt):
            a=build(persona,[],target_name='群一',recipient_name='甲',training_block='当前资料甲')
            b=build(persona,[],target_name='群二',recipient_name='乙',training_block='当前资料乙')
            prefix=0
            for x,y in zip(a,b):
                if x!=y:break
                prefix+=1
            assert prefix>len(a)*.8,'changing recipient/evidence must retain the stable prompt prefix'
    print('TOKEN EFFICIENCY TEST PASSED')


if __name__=='__main__':main()
