"""Old context policy and prompt layout, with current transport/style fixes."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.reasoning import level,agent_budget,output_tokens
from app.agent_runtime import AgentRuntime
from app.prompting import build_system_prompt,build_compact_system_prompt
from app.defaults import ARONA_PERSONA
from app.reply_fallbacks import LINES,vague_non_answer
from app.model_client import ModelClient


def main():
    assert level('high_consumption')=='high_consumption'
    assert agent_budget({'reasoning_intensity':'high_consumption'})==(4,90)
    assert output_tokens(1200,'high_consumption')==1200
    legacy=build_system_prompt(ARONA_PERSONA,[],legacy=True,recipient_name='甲')
    normal=build_system_prompt(ARONA_PERSONA,[],recipient_name='甲')
    assert '13. 说人话' in legacy and '13. 说人话' not in normal
    assert '先给明确的结论' in legacy and '先给明确的结论' in normal
    assert '语气词能省则省' in build_compact_system_prompt(ARONA_PERSONA,[],legacy=True)
    runtime=AgentRuntime();seen=[]
    transcript=[{'role':'user' if i%2==0 else 'assistant','content':str(i)+' 完整历史'} for i in range(30)]
    client=SimpleNamespace(effort='high_consumption',model='fixture',api_key='')
    def call(purpose):
        runtime.invoke(client,'chat',purpose,([{'role':'user','content':'本轮任务'}],'规则'),{},
            lambda args,kwargs:seen.append(args) or SimpleNamespace(text='结果'))
    with runtime.task('本群任务','A'):
        runtime.set_dialogue(transcript,'更早的约定')
        for purpose in ('群聊回复对象判断','上下文摘要','关键词是否对机器人说话'):
            call(purpose)
            assert seen[-1][0][:-1]==transcript and '更早的约定' in seen[-1][1]
        with runtime.context('recent'):
            call('统一分享Agent：判断/检索/审核/撰写')
            assert seen[-1][0][:-1]==transcript
        with runtime.context('provided'):
            call('统一分享Agent：判断/检索/审核/撰写')
            assert seen[-1][0][:-1]==transcript
            call('今日日记：当天活动总结')
            assert len(seen[-1][0])==1, 'public diary must not gain private history'
        client.effort='medium'
        call('群聊回复对象判断')
        assert len(seen[-1][0])==1, 'switching back restores efficient view'
    with runtime.task('另一群','B'):
        client.effort='high_consumption';call('群聊回复对象判断')
        assert len(seen[-1][0])==1,'high consumption must never mix chat histories'
    assert vague_non_answer('Harry昊昊老师，这题让我有点迷糊啦')
    assert not vague_non_answer('这份文件没有注明发布日期。')
    assert all(not vague_non_answer(t.format(t='老师')) for group in LINES.values() for rows in group.values() for t in rows)
    client=ModelClient({'provider':'openai','base_url':'https://api.deepseek.com','model':'deepseek-chat',
                        '_reasoning_intensity':'high_consumption','max_tokens':1200})
    with patch.object(client,'_with_retry',return_value=({'choices':[{'message':{'content':'你好'}}]},1)) as request:
        client.chat([{'role':'user','content':'你好'}])
        assert 'thinking' not in request.call_args.args[1]
    print('HIGH CONSUMPTION AND CLEAR SPEECH TESTS PASSED')


if __name__=='__main__':main()
