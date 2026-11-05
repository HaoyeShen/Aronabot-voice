"""Model decisions, auditable rejection reasons and safe failure boundaries."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import web_search as web
from app.web_selection import ModelWebSelector
from app.agent_runtime import RUNTIME


def main():
    candidates = [('https://example.com/venti', 'Barbatos character archive', 'Venti biography'),
                  ('https://example.com/venti', '重复条目', ''),
                  ('https://example.com/unrelated', '软件发布公告', ''),
                  ('https://example.com/company', '温迪有限公司', ''),
                  ('https://example.com/blocked', '原神温迪资料', '')]
    body = ('Barbatos is also known as Venti, a bard from Mondstadt. ' * 8).encode()
    stages = []
    def select(query, stage, rows, limit, remaining):
        stages.append(stage)
        assert query == '原神温迪' and remaining > 0
        return [{'id':r['id'], 'selected':r['id'] in ((1,4,5) if stage=='候选初筛' else (1,)),
                 'reason':('角色别名与检索目标一致' if r['id']==1 else '公司同名，正文不是游戏人物' if r['id']==4
                           else '可读取的角色资料' if r['id']==5 else '软件公告与角色不相关')} for r in rows]
    select.cache_key = 'fixture-model-one'
    def fetch(url, **kwargs):
        if url.endswith('/blocked'):raise HTTPError(url,403,'Forbidden',{},None)
        return b'<main>'+body+b'</main>', 'utf-8'
    with patch.object(web,'_discover',side_effect=lambda q,k,t:candidates if k=='html' else []), \
            patch.object(web,'fetch_public',side_effect=fetch) as network, \
            patch.object(web,'validate_url'), patch.object(web,'_relevant',side_effect=AssertionError('local relevance must not decide')), \
            patch.object(web.log,'info') as logs:
        search=web.WebSearch()
        pages=search.search('原神温迪',selector=select)
        assert len(pages)==1 and pages[0].url.endswith('/venti')
        assert stages==['候选初筛','正文复核'] and network.call_count==3
        decisions=search.status()['decisions']
        assert any(d['id']==2 and '重复' in d['reason'] for d in decisions)
        assert any(d['id']==3 and d['by']=='模型' and not d['selected'] for d in decisions)
        assert any(d['id']==4 and d['stage']=='正文复核' and not d['selected'] for d in decisions)
        assert any(d['id']==5 and '403' in d['reason'] and d['by']=='程序' for d in decisions)
        assert search.status()['blocked']==1 and search.status()['selection_mode']=='model'
        assert sum(call.args[0].startswith('网页候选筛选') for call in logs.call_args_list)==len(decisions)
        assert search.search('原神温迪',selector=select)==pages and network.call_count==3
        select.cache_key='fixture-model-two'
        assert search.search('原神温迪',selector=select) and network.call_count==6
    with patch.object(web,'_discover',side_effect=lambda q,k,t:candidates[:1] if k=='html' else []), \
            patch.object(web,'fetch_public') as network:
        failed=web.WebSearch()
        assert not failed.search('原神温迪',selector=lambda *args:[])
        assert failed.status()['selection_errors']==1
        assert '不采用未审核内容' in failed.status()['decisions'][0]['reason']
        network.assert_not_called()
    # A short actual biography is still judged by the model, not length.
    with patch.object(web,'_discover',side_effect=lambda q,k,t:candidates[:1] if k=='html' else []), \
            patch.object(web,'fetch_public',return_value=('<main>温迪是蒙德的吟游诗人。</main>'.encode(),'utf-8')), \
            patch.object(web,'validate_url'):
        short=web.WebSearch().search('原神温迪',selector=select)
        assert len(short)==1 and len(short[0].content)<100
    # Schema parsing and worker execution retain the owning Agent session.
    calls=[];usage=[]
    rows=[{'id':1,'url':'https://example.com/venti','title':'Venti'}]
    def chat(messages,system):
        calls.append(RUNTIME.session_id())
        return SimpleNamespace(text=json.dumps({'decisions':[[1,True,'角色别名匹配']]}))
    with RUNTIME.task('候选筛选测试','test','web-selection-test'):
        session=RUNTIME.session_id()
        with patch('app.web_selection.make_client',return_value=SimpleNamespace(chat=chat,model='fixture',api_key='')):
            result=ModelWebSelector({'provider':'openai'},usage.append)('温迪','候选初筛',rows,3,5)
            assert result[0]['selected'] and calls==[session] and len(usage)==1
        for output in ('{"decisions":[]}', '{"decisions":[[1,true,""]]}', '{"decisions":[[2,true,"未知编号"]]}'):
            client=SimpleNamespace(chat=lambda *args:SimpleNamespace(text=output),model='fixture',api_key='')
            with patch('app.web_selection.make_client',return_value=client):
                try:ModelWebSelector({'provider':'openai'})('温迪','候选初筛',rows,3,5)
                except ValueError:pass
                else:raise AssertionError('invalid model selection accepted')
    print('MODEL WEB SELECTION TESTS PASSED')


if __name__=='__main__':main()
