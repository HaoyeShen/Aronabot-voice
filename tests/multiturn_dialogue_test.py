"""Persisted native dialogue: follow-ups, old turns, restart and draft exclusion."""
import copy
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore,PersonaStore,StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.model_client import LLMResult
from app.model_audit import model_call
from app.agent_runtime import RUNTIME
from app.prompting import build_context_messages


def main():
    long='这是一段保留完整原文的回复。'*180
    assert build_context_messages([{'role':'assistant','sender':'阿洛娜','content':long}]) == [
        {'role':'assistant','content':long}]
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);cfg=ConfigStore(root/'config.json');cfg.set_active_profile('demo-local')
        cfg.bot.update(auto_sticker=False,voice_enabled=False,web_search_enabled=False)
        people=PersonaStore(root/'personas.json');stickers=StickerStore(root/'stickers.json')
        memory=MemoryStore(root/'memory.db');training=TrainingStore(root/'training.db',root/'files')
        service=BotService(cfg,people,stickers,memory,training,admin_state_path=root/'admin.json')
        requests=[]
        class Client:
            model='fixture';api_key=''
            answer='家里有什么食材？'
            def chat(self,messages,system=''):
                requests.append(copy.deepcopy(messages))
                return LLMResult(self.answer,'fixture','mock',1)
        client=Client()
        chat='原文多轮验证'
        RUNTIME.reset(chat)
        try:
            with patch('app.service.make_client',return_value=client):
                first=service.debug_chat('我想做炒饭。',chat_name=chat)
                assert requests[-1]==[{'role':'user','content':'我想做炒饭。'}]
                # The reply timeout is not the lifetime of conversation memory.
                with memory._lock:
                    memory._conn.execute('UPDATE messages SET created_at=? WHERE chat=?',(time.time()-1200,chat))
                    memory._conn.commit()
                client.answer='鸡蛋和米饭够用。'
                service.debug_chat('有鸡蛋和米饭。',chat_name=chat)
                assert requests[-1]==[
                    {'role':'user','content':'我想做炒饭。'},
                    {'role':'assistant','content':first['text']},
                    {'role':'user','content':'有鸡蛋和米饭。'}]
                client.answer='那就换成玉米。'
                service.debug_chat('把鸡蛋换成玉米。',chat_name=chat)
                # An internal tool/rewrite/API draft is visible in audit, but
                # must not become an assistant utterance in the next turn.
                client.answer='没发送的草稿和内部JSON'
                with RUNTIME.task('内部工作',chat):
                    model_call(client,'chat','仅供程序判断',[{'role':'user','content':'程序判断请求'}])
                assert all('草稿' not in str(m) for m in RUNTIME.session_snapshot(chat))
            memory.close();training.close();RUNTIME.reset(chat)
            memory=MemoryStore(root/'memory.db');training=TrainingStore(root/'training.db',root/'files')
            service=BotService(cfg,people,stickers,memory,training,admin_state_path=root/'admin.json')
            client.answer='米饭和玉米。'
            with patch('app.service.make_client',return_value=client):
                service.debug_chat('那现在有哪些食材？',chat_name=chat)
                sent=requests[-1]
                assert [m['role'] for m in sent]==['user','assistant','user','assistant','user','assistant','user']
                assert '把鸡蛋换成玉米。' in str(sent) and '草稿' not in str(sent) and '程序判断请求' not in str(sent)
                assert not any('历史任务' in m['content'] or '机器人此前回复' in m['content'] for m in sent)
                service._record_news_dialogue(chat,'新活动将于周五开始。',test=True)
                service.debug_chat('新闻里的活动哪天开始？',chat_name=chat)
                assert {'role':'assistant','content':'新活动将于周五开始。'} in requests[-1]
                client.answer='你好。'
                service.debug_chat('你好。',chat_name='另一个聊天')
                assert requests[-1]==[{'role':'user','content':'你好。'}]
            service.clear_context(chat)
            assert not memory.recent(chat) and not RUNTIME.session_snapshot(chat)
        finally:
            memory.close();training.close();RUNTIME.reset(chat);RUNTIME.reset('另一个聊天')
    print('NATIVE MULTITURN DIALOGUE TESTS PASSED')


if __name__=='__main__':main()
