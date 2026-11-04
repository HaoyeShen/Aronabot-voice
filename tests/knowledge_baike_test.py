"""Entity recall, source-scoped continuation, mandatory lookup and Baike fallback."""
import json
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.training import TrainingStore
from app.config_store import ConfigStore,PersonaStore,StickerStore
from app.memory import MemoryStore
from app.service import BotService
from app.model_client import LLMResult
from app.wechat_adapter import IncomingMessage
from app.agent import AgentRunner,AgentTool
import app.web_search as web


def result(text):return LLMResult(text,'fixture','mock',1)
def action(tool,query):return json.dumps({'action':'tool','tool':tool,'arguments':{'query':query}})


def main():
    query='检索一下这次不用把命令行发出来检索一下你的知识库里所有关于Mon3tr的知识群里的那位Mon3tr就是她'
    assert 'mon3tr' in [t.casefold() for t in TrainingStore._terms(query)[0]]
    assert web.normalize_query('关于三体的资料')=='三体'
    assert web.normalize_query('三体相关资料')=='三体'
    assert web.explicit_search('那你可以上网搜啊')
    assert web.explicit_search('去百度百科查三体')
    assert not web.explicit_search('不要去百度百科查三体')
    assert web.baike_forbidden('不用百度百科，直接上网查三体')
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);store=TrainingStore(root/'training.db',root/'files')
        doc=root/'明日方舟主线剧情与人物资料.txt'
        doc.write_text('普通剧情的介绍和背景。'*55+'\nMon3tr是凯尔希身边的重要存在，相关人物资料记录了二者的关联。\n'+'其他剧情。'*20,encoding='utf-8')
        store.add_file(doc)
        other=root/'其他文档.txt';other.write_text('检索一下你的知识库里的所有资料。'*60+'\nMon3tr也在这份其他资料被提到。',encoding='utf-8');store.add_file(other)
        for text in (query,'Ｍｏｎ３ｔｒ','Mon3tr 《明日方舟主线剧情与人物资料》'):
            hits=store.search(text,max_chars=200)
            assert hits and 'mon3tr' in hits[0]['content'].casefold(),text
            if '《' in text:assert all(h['name']==doc.name for h in hits)
        cfg=ConfigStore(root/'config.json');cfg.bot.update(auto_sticker=False,voice_enabled=False)
        memory=MemoryStore(root/'memory.db')
        service=BotService(cfg,PersonaStore(root/'personas.json'),StickerStore(root/'stickers.json'),memory,store,admin_state_path=root/'admin.json')
        try:
            history=[{'role':'user','content':'请检索 Mon3tr 的资料。'}]
            block,count,_=service._training_context('检索你知识库里22段那个《明日方舟主线剧情与人物资料》',history)
            assert count and 'Mon3tr' in block
            fake=Mock();fake.chat.side_effect=[result('没有相关资料。'),result(action('search_knowledge','Mon3tr')),result('Mon3tr与凯尔希有关。')]
            incoming=IncomingMessage('test','群友',query,chat_type='group')
            answer,_,_,info=service._model_reply(fake,[],'角色设定',{'provider':'openai'},incoming,query,require_running=False)
            assert answer.text=='Mon3tr与凯尔希有关。' and info['knowledge_searched'] and info['knowledge_hits']
            assert fake.chat.call_count==3
            assert info['agent']['steps'][0]['tool']=='search_knowledge'
            fake=Mock();fake.chat.side_effect=[result('我已经查过百科。'),result(action('search_baike','三体')),result('三体是科幻小说。')]
            page=web.WebPage('https://baike.baidu.com/item/三体','三体', '三体是一部科幻小说。'*30,'2026-11-04')
            with patch.object(service.web_search,'search',return_value=[page]) as lookup:
                request=IncomingMessage('test','群友','去百度百科查三体',chat_type='group')
                answer,_,_,info=service._model_reply(fake,[],'',{'provider':'openai'},request,request.content,require_running=False)
                assert info['searched'] and lookup.call_args.kwargs['only_baike']
                assert fake.chat.call_count==3 and answer.text=='三体是科幻小说。'
            # The Baike tool follows both the source switch and master network switch.
            for master,baike,expected in ((True,True,True),(True,False,False),(False,True,False)):
                cfg.bot.update(web_search_enabled=master,baidu_baike_enabled=baike)
                fake=Mock();fake.chat.return_value=result('你好。')
                with patch('app.service.AgentRunner.run',wraps=AgentRunner().run) as runner:
                    service._model_reply(fake,[],'',{'provider':'openai'},IncomingMessage('test','群友','你好'), '你好',require_running=False)
                assert ('search_baike' in {t.name for t in runner.call_args.args[3]})==expected
            legacy=root/'old.json';legacy.write_text('{"bot":{"web_search_enabled":false}}',encoding='utf-8')
            migrated=ConfigStore(legacy)
            assert migrated.bot['baidu_baike_enabled'] and not migrated.bot['web_search_enabled']
            migrated.bot['baidu_baike_enabled']=False;migrated.save()
            assert not ConfigStore(legacy).bot['baidu_baike_enabled']
        finally:memory.close();store.close()

    original='https://example.com/three-body'
    baike='https://baike.baidu.com/item/三体'
    body='<main>'+('三体是刘慈欣的科幻小说系列，讲述地球文明与三体文明的故事。'*8)+'</main>'
    with patch.object(web,'_discover',return_value=[(original,'三体介绍','三体')]) as general, \
         patch.object(web,'_discover_baike',return_value=[(baike,'三体 百度百科','三体')]) as encyclopedia, \
         patch.object(web,'fetch_public',return_value=(body.encode(),'utf-8')):
        search=web.WebSearch()
        assert {p.url for p in search.search('关于三体的资料',include_baike=True)}=={original,baike}
        assert {c.args[1] for c in general.call_args_list}=={'html','rss'}| (set(web.CHANNELS)-{'bing'}),'original search plus independent channels are retained'
        assert [p.url for p in search.search('三体',include_baike=False)]==[original],'disabled source cannot leak from cache'
        assert [p.url for p in search.search('三体',only_baike=True)]==[baike]
        assert encyclopedia.call_count==2
    def blocked(url,**kwargs):
        if 'baike.baidu.com' in url:raise HTTPError(url,403,'Forbidden',{},None)
        return body.encode(),'utf-8'
    with patch.object(web,'_discover',return_value=[(original,'三体介绍','三体')]), \
         patch.object(web,'_discover_baike',return_value=[(baike,'三体 百度百科','三体')]), \
         patch.object(web,'fetch_public',side_effect=blocked):
        search=web.WebSearch()
        assert [p.url for p in search.search('三体',include_baike=True)]==[original]
        assert search.status()['blocked']==1
        assert not search.search('三体',only_baike=True) and search.status()['blocked']==1
    print('KNOWLEDGE AND BAIKE TESTS PASSED')


if __name__=='__main__':main()
