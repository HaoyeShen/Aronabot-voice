"""Evidence -> multimodal Agent -> validated send plan, with no extra model API."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore,PersonaStore,StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.bilibili_video import Video
from app.model_client import LLMResult
from app.news import NewsItem, news_now
from app.sharing_agent import awkward_text


def main():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);cfg=ConfigStore(root/'config.json');cfg.add_target('test','group','arona')
        memory=MemoryStore(root/'memory.db');training=TrainingStore(root/'training.db',root/'files')
        s=BotService(cfg,PersonaStore(root/'personas.json'),StickerStore(root/'stickers.json'),memory,training,admin_state_path=root/'admin.json');s._running=True
        try:
            memory.add_user('test','这次蔚蓝档案活动剧情挺有意思',sender='甲')
            video=Video('BV1btH36uEru',1,'蔚蓝档案活动剧情','蔚蓝档案',0,'https://www.bilibili.com/zh-Hans/video/BV1btH36uEru/',description='新活动剧情整理')
            def reply(action):return LLMResult(json.dumps(action,ensure_ascii=False),'fixture','openai',1)
            fake=Mock()
            fake.chat.side_effect=[reply({'action':'tool','tool':'search_videos','arguments':{'query':'蔚蓝档案 活动剧情'}}),
                reply({'action':'final','text':json.dumps({'kind':'VIDEO','bvid':video.bvid,'comments':['这段剧情有点意思。']},ensure_ascii=False)})]
            with patch.object(cfg,'active_profile',return_value={'provider':'openai','model':'fixture'}), \
                 patch('app.sharing_agent.make_client',return_value=fake),patch.object(s.idle_videos,'pick',return_value=video), \
                 patch.object(s,'_approve_video',side_effect=AssertionError('extra approval call')), \
                 patch.object(s,'_enrich_video_cover',side_effect=AssertionError('extra vision call')), \
                 patch.object(s,'_idle_video_comments',side_effect=AssertionError('extra comment call')):
                plan=s._sharing_agent('test',cfg.target_by_name('test'),mode='VIDEO')
                assert plan['video'] is video and fake.chat.call_count==2
                assert plan['comments']==['这段剧情有点意思。']
            for anchor,text,expected in [('不在群里的事','这剧情有点意思。','WAIT'),('活动剧情','最近有开心的事吗？','WAIT'),('活动剧情','这段剧情确实挺细。','TEXT')]:
                fake=Mock();fake.chat.return_value=reply({'action':'final','text':json.dumps({'kind':'TEXT','text':text,'anchor':anchor},ensure_ascii=False)})
                with patch.object(cfg,'active_profile',return_value={'provider':'openai','model':'fixture'}),patch('app.sharing_agent.make_client',return_value=fake):
                    plan=s._sharing_agent('test',cfg.target_by_name('test'))
                    assert plan['kind']==expected and fake.chat.call_count==1
            assert awkward_text('群里好安静，聊点什么吧')
            # News collection receives model-generated queries, not another model invocation.
            fake=Mock();fake.chat.side_effect=[reply({'action':'tool','tool':'collect_news','arguments':{'queries':json.dumps({'国服':'蔚蓝档案 公告','日服':'ブルーアーカイブ','国际服':'Blue Archive news'})}}),
                reply({'action':'final','text':json.dumps({'kind':'NEWS','text':'【国服】没有符合条件的新消息。\n【日服】没有符合条件的新消息。\n【国际服】没有符合条件的新消息。'},ensure_ascii=False)})]
            empty={r[0]:[] for r in s.news.REGIONS}
            with patch.object(cfg,'active_profile',return_value={'provider':'openai','model':'fixture'}),patch('app.sharing_agent.make_client',return_value=fake), \
                 patch.object(s.news,'fetch',return_value=empty) as fetch,patch.object(s.news,'_filter_seen',return_value=empty), \
                 patch.object(s.news,'_model_message',side_effect=AssertionError('separate summary call')):
                grouped,text,_=s._news_agent_payload({'name':'test'},news_now())
                assert fake.chat.call_count==2 and fetch.call_args.kwargs['model_queries']['国服']=='蔚蓝档案 公告'
                assert '没' in text and not any(grouped.values())
        finally:s._running=False;memory.close();training.close()
    print('SHARING AGENT TESTS PASSED')


if __name__=='__main__':main()
