"""One chat owns one Agent; nested AI steps, workers and views stay isolated."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.agent_runtime import AgentRuntime, RUNTIME, task_entry
from app.agent import _bounded
from app.model_audit import model_call, register_secret


def main():
    registry=AgentRuntime()
    first=registry.agent_for('群备注','group@chatroom')
    assert registry.agent_for('原群名','group@chatroom') is first
    assert registry.agent_for('好友','friend_wxid') is not first
    preview=registry.agent_for('先预览的群')
    assert registry.agent_for('先预览的群','later@chatroom') is preview
    assert registry.agent_for('先预览的群') is preview
    fake=SimpleNamespace(api_key='private_key_12345',model='fixture',chat=lambda *a,**k:SimpleNamespace(text='答案'))
    register_secret('private_key_12345')
    with RUNTIME.task('同群完整任务','群A','group_a@chatroom') as task:
        data=[{'role':'user','content':'原始内容 private_key_12345'}]
        model_call(fake,'chat','对象判断',data)
        data[0]['content']='后续修改，不得污染先前记录'
        status,_=_bounded(lambda:model_call(fake,'chat','回复消息',[{'role':'user','content':'接上文'}]),time.monotonic()+2,lambda:False)
        assert status=='ok'
    selected=[e for e in RUNTIME.snapshot() if e['task_id']==task['id']]
    assert len({e['agent_id'] for e in selected})==1
    assert any('原始内容' in str(e['content']) for e in selected)
    assert all('private_key_12345' not in str(e) for e in selected)
    with RUNTIME.task('该群新闻','群A','group_a@chatroom') as next_task:
        model_call(fake,'chat','新闻',[{'role':'user','content':'新闻任务'}])
    assert next_task['agent'] is task['agent'] and next_task['id']!=task['id']
    with RUNTIME.task('其他私聊','私聊B','friend_b') as other:
        model_call(fake,'chat','普通回复',[{'role':'user','content':'独立消息'}])
    assert other['agent'] is not task['agent']
    assert all('新闻任务' not in str(e['content']) for e in RUNTIME.snapshot() if e['agent_id']==other['agent'].id)
    try:
        with registry.task('禁止跨聊天','A','a'):
            registry.bind('B','b')
    except ValueError:
        pass
    else:
        raise AssertionError('one task cannot silently change chat ownership')
    print('AGENT RUNTIME TESTS PASSED')


if __name__=='__main__':main()
