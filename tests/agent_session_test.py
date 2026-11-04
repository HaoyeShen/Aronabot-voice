"""All tasks in a chat continue the actual model conversation, without leaks."""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.agent_runtime import AgentRuntime, RUNTIME
from app.model_audit import model_call
from app.model_client import ModelClient


def main():
    # Native persisted turns are shared, while API drafts never become speech.
    client = ModelClient({'provider':'openai','model':'session-test','base_url':'https://invalid.test'})
    client.api_key = 'session-private-key'
    def response(text):
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':text}}]}).encode())
    RUNTIME.reset('连续会话测试','session_group')
    with patch.object(client,'_opener') as opener:
        opener.return_value.open.return_value = response('已核实的新闻事实')
        with RUNTIME.task('发送新闻','连续会话测试','session_group') as first:
            model_call(client,'chat','新闻搜集与总结',[{'role':'user','content':'整理新闻 session-private-key'}],'新闻格式')
        assert not RUNTIME.session_snapshot('连续会话测试','session_group'), 'API output is not a delivered chat turn'
        delivered = [{'role':'assistant','content':'已核实的新闻事实'}]
        request = [{'role':'user','content':'你刚才的新闻说了什么？'}]
        opener.return_value.open.return_value = response('承接刚才的新闻')
        with RUNTIME.task('群聊回复','原群名','session_group') as second:
            RUNTIME.set_dialogue(delivered + request)
            result = model_call(client,'chat','回复',request,'当前角色与回复格式')
        sent = json.loads(opener.return_value.open.call_args.args[0].data)['messages']
        assert first['agent'] is second['agent'] and first['id'] != second['id']
        assert any(m['role']=='assistant' and m['content']=='已核实的新闻事实' for m in sent)
        assert 'session-private-key' not in json.dumps(sent,ensure_ascii=False)
        assert sent[0] == {'role':'system','content':'当前角色与回复格式'}
        assert sent[1:] == delivered + request and len(request)==1, 'no duplicate history or task recap'
        assert result.text=='承接刚才的新闻'
        opener.return_value.open.return_value = response('另一群独立回答')
        with RUNTIME.task('回复','其他群','other_session_group'):
            client.chat([{'role':'user','content':'独立问题'}],'另一群角色')
        separate = json.loads(opener.return_value.open.call_args.args[0].data)['messages']
        assert '已核实的新闻事实' not in str(separate)
        # Reset by an alias clears the same underlying session.
        old_id=first['agent'].id
        RUNTIME.reset('原群名')
        assert not RUNTIME.session_snapshot('连续会话测试','session_group')
        assert RUNTIME.agent_for('原群名').id==old_id

    runtime=AgentRuntime()
    captured=[]
    fake=SimpleNamespace(api_key='',model='fixture')
    def call(text='答案',messages=None):
        messages=messages or [{'role':'user','content':'当前问题'}]
        return runtime.invoke(fake,'chat','回复',(messages,),{},
                              lambda args,kwargs: (captured.append(args[0]) or SimpleNamespace(text=text)))
    with runtime.task('旧角色','A','a'):
        runtime.set_persona('old-role')
        runtime.set_dialogue([{'role':'assistant','content':'旧角色专属称呼'}])
        call('旧角色草稿')
    with runtime.task('新角色','A','a'):
        runtime.set_persona('new-role')
        runtime.set_dialogue([{'role':'user','content':'你好'},{'role':'assistant','content':'新角色回答'}])
        call('新角色草稿')
    assert '旧角色专属称呼' not in str(captured[-1])
    with runtime.task('后续分享','A','a'):
        runtime.set_persona('new-role');call('分享结果')
        # Same task's tool loop already contains its intermediate messages.
        call('最后结果',[{'role':'user','content':'当前问题'},
                       {'role':'assistant','content':'分享结果'},
                       {'role':'user','content':'工具结果'}])
        assert str(captured[-1]).count('分享结果')==1
        assert '新角色回答' in str(captured[-1])
    # Failed requests and results arriving after reset/end are never promoted
    # into future session context; reset cannot be undone by a worker race.
    with runtime.task('并发清空','A','a'):
        def reset_during_call(args,kwargs):
            runtime.reset('A');return SimpleNamespace(text='清空前已在执行的旧回答')
        runtime.invoke(fake,'chat','旧请求',([{'role':'user','content':'旧问题'}],),{},reset_during_call)
    assert not runtime.session_snapshot('A')
    with runtime.task('失败','A','a'):
        try:
            runtime.invoke(fake,'chat','失败',([],),{},lambda *args: (_ for _ in ()).throw(OSError('offline')))
        except OSError:pass
        else:raise AssertionError('failure must propagate')
    assert not runtime.session_snapshot('A')
    for i in range(60):
        with runtime.task('有界会话','A','a'):
            call('答'*4000,[{'role':'user','content':'问'*4000}])
    turns=runtime.call_snapshot('A')
    assert len(turns)<=48 and sum(len(t['input'])+len(t['output']) for t in turns)<=96000
    assert sum(len(m['content']) for m in captured[-1][:-1])<8500
    print('AGENT SESSION TESTS PASSED')


if __name__=='__main__':main()
