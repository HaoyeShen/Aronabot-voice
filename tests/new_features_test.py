"""Depth protocol, real audio validation, comment idempotency and UI construction."""
import json
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.model_client import ModelClient, LLMResult
from app.reasoning import agent_budget
from app.moments_reader import MomentsReader
from app.wechat_adapter import SendResult
from app.news_sources import SOURCES


def main():
    from app.news import NewsService
    feed_source=next(s for s in SOURCES if s.host=='4gamer.net')
    with patch('app.news._http_text',return_value='<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>ブルーアーカイブ 新イベント</title><link>https://www.4gamer.net/news/1</link><dc:date>2026-11-05T09:00:00+09:00</dc:date><description>新しいイベント</description></item><item><title>Blue Archive update</title><link>https://4gamer.net.evil.example/1</link></item></rdf:RDF>'):
        feed=NewsService._publisher_feed(SimpleNamespace(_video_url=lambda _:False),feed_source,4)
        assert feed.feed_state=='read' and len(feed)==1
        assert feed[0].published_at=='2026-11-05T08:00:00' and not feed[0].body_read
    assert len(SOURCES) >= 25 and len({s.host for s in SOURCES}) == len(SOURCES)
    assert SOURCES[0].accepts('https://www.taptap.cn/moment/1')
    assert not SOURCES[0].accepts('https://taptap.cn.evil.example/article')
    for effort, expected in (('light',600),('medium',1000),('high',2000),('extreme',4000)):
        client=ModelClient({'provider':'openai','base_url':'https://api.deepseek.com',
            'model':'deepseek-chat','max_tokens':1000,'_reasoning_intensity':effort})
        with patch.object(client,'_with_retry',return_value=({'choices':[{'message':{'content':'你好'}}]},1)) as call:
            assert client.chat([{'role':'user','content':'你好'}]).text=='你好'
            payload=call.call_args.args[1]
            assert payload['max_tokens']==expected
            if effort=='medium':assert 'thinking' not in payload and len(payload['messages'])==1
            else:assert payload['thinking']['type']==('disabled' if effort=='light' else 'enabled')
        with patch.object(client,'_with_retry',return_value=({'choices':[{'message':{'content':'你好'}}]},1)) as call:
            client.base_url='https://relay.example'
            client.chat([{'role':'user','content':'你好'}])
            assert 'thinking' not in call.call_args.args[1], 'no unsupported provider fields on relay'
    assert agent_budget({})==(4,90)
    assert agent_budget({'reasoning_intensity':'light'})[0]==2
    assert agent_budget({'reasoning_intensity':'extreme'})==(8,180)
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);config=ConfigStore(root/'config.json')
        assert config.bot['reasoning_intensity']=='medium' and not config.bot['moments_comment_enabled']
        config.bot['reasoning_intensity']='high'
        assert config.active_profile()['_reasoning_intensity']=='high'
        config.save()
        assert '_reasoning_intensity' not in json.loads((root/'config.json').read_text())['model_profiles'][0]
        # Separate preview and external submission; pending receipts survive failures.
        post={'id':'p1','tid':'1','username':'friend','nickname':'friend',
              'text':'今天终于完成了一个小目标。','create_time':time.time(),'comments':[]}
        adapter=SimpleNamespace(self_wxid='self',read_moments=Mock(return_value=[post]),
            comment_moment=Mock(return_value=SendResult(True,'已核对')))
        service=SimpleNamespace(config=config,adapter=adapter,_running=True,_reply_busy=False,_admin_sessions={},emit=Mock())
        reader=MomentsReader(service,root/'moments.json')
        with patch.object(reader,'decide',return_value={'action':'comment','text':'这个进度真不错呀。'}) as decide:
            reader.tick();assert not decide.called and not adapter.read_moments.called
            assert reader.tick(preview=True)['ok'];assert not adapter.comment_moment.called
            config.bot['moments_comment_enabled']=True;reader.next_at=0
            assert reader.tick()['ok'];assert adapter.comment_moment.call_count==1
            reader.next_at=0;reader.tick();assert adapter.comment_moment.call_count==1
            # Unknown results must not be repeated on restart.
            post['id']='p2';adapter.comment_moment.return_value=SendResult(False,'结果未知','uncertain')
            reader.next_at=0;assert not reader.tick()['ok']
            assert any(r['status']=='uncertain' for r in reader.load().values())
            fresh=MomentsReader(service,root/'moments.json');fresh.tick()
            assert adapter.comment_moment.call_count==2
        assert not reader.eligible([dict(post,username='self')],'self',time.time())
        assert not reader.eligible([dict(post,create_time=time.time()-90000)],'self',time.time())
        reader.path.write_text('broken',encoding='utf-8');reader.next_at=0
        assert not reader.tick()['ok'] and adapter.comment_moment.call_count==2
    print('NEW FEATURE TESTS PASSED')


if __name__=='__main__':main()
