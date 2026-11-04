"""Adapter contracts, independent failure handling and bounded public evidence."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import search_channels as channels
from app import web_search as web


def main():
    assert len(channels.CHANNELS)>=20
    assert web._url('https://html.duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle')=='https://example.com/article'
    assert web._url('https://r.search.yahoo.com/_ylt=x/RU=https%3A%2F%2Fexample.com%2Farticle/RK=2')=='https://example.com/article'
    body='明日方舟 Mon3tr 是游戏角色。'+('这是该角色的公开介绍和资料。'*15)
    wiki={'query':{'pages':{'1':{'title':'Mon3tr','fullurl':'https://prts.wiki/w/Mon3tr','extract':body}}}}
    fixtures={
        'wikidata':{'search':[{'concepturi':'https://www.wikidata.org/wiki/Q1','label':'Mon3tr','description':body}]},
        'github':{'items':[{'html_url':'https://github.com/example/mon3tr','full_name':'Mon3tr','description':body}]},
        'stackexchange':{'items':[{'link':'https://stackoverflow.com/questions/1','title':'Mon3tr','body':body}]},
        'mdn':{'documents':[{'mdn_url':'/en-US/docs/Mon3tr','title':'Mon3tr','summary':body}]},
        'crossref':{'message':{'items':[{'URL':'https://doi.org/10.1/mon3tr','title':['Mon3tr'],'abstract':body}]}},
        'openlibrary':{'docs':[{'key':'/works/1','title':'Mon3tr','author_name':['作者']}]},
        'archive':{'response':{'docs':[{'identifier':'mon3tr','title':'Mon3tr','description':body}]}},
        'npm':{'objects':[{'package':{'name':'Mon3tr','links':{'npm':'https://www.npmjs.com/package/mon3tr'},'description':body}}]},
        'hackernews':{'hits':[{'objectID':'1','title':'Mon3tr','story_text':body}]},
    }
    seen=[]
    for channel in channels.CHANNELS:
        if channel=='bing':continue  # Existing HTML/RSS adapter tests cover Bing.
        def fetch(url,timeout):
            seen.append((channel,url))
            assert url.startswith('https://') and timeout<=3
            if channel in ('wikipedia','moegirl','prts','bwiki'):return json.dumps(wiki)
            if channel=='arxiv':return '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1</id><title>Mon3tr</title><summary>'+body+'</summary></entry></feed>'
            if channel in fixtures:return json.dumps(fixtures[channel])
            return '<a href="https://example.com/mon3tr">Mon3tr 资料</a>'
        hits=channels.discover('明日方舟 Mon3tr',channel,3,fetch)
        assert hits and len(hits[0])==3,channel
    assert len({name for name,_ in seen})>=20
    def discover(query,kind,timeout):
        if kind!='prts':raise HTTPError('https://example.com',403,'Forbidden',{},None)
        return [channels.SearchHit('https://prts.wiki/w/Mon3tr','Mon3tr',body,body)]
    with patch.object(web,'_discover',side_effect=discover),patch.object(web,'fetch_public') as page_fetch:
        s=web.WebSearch();pages=s.search('明日方舟 Mon3tr')
        assert len(pages)==1 and '公开 API 摘要（非全文）' in pages[0].content
        assert s.status()['channels_attempted']==len(channels.CHANNELS)
        assert s.status()['channels']['bing']['state']=='blocked'
        assert s.status()['channels']['prts']['state']=='results'
        page_fetch.assert_not_called()
    # Short title/snippet metadata is discovery only, never fabricated as body.
    with patch.object(web,'_discover',return_value=[channels.SearchHit('https://example.com/x','Mon3tr',body,'短摘要')]), \
         patch.object(web,'fetch_public') as page_fetch:
        assert not web.WebSearch().search('Mon3tr')
        page_fetch.assert_not_called()
    with patch.object(web,'_discover') as network:
        assert not web.WebSearch().search('Mon3tr',cancelled=lambda:True)
        assert not web.WebSearch().search('sk-'+'a'*24)
        network.assert_not_called()
    print('SEARCH CHANNELS TEST PASSED')


if __name__=='__main__':main()
