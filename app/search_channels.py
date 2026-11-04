"""Public search adapters. No cookies, private credentials or challenge bypass."""
from __future__ import annotations

import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET


# One channel per service, not one per language or Bing URL format.
CHANNELS = {
    'bing':'Bing', 'duck':'DuckDuckGo', 'baidu':'百度搜索', 'sogou':'搜狗搜索',
    'so':'360 搜索', 'google':'Google', 'brave':'Brave Search', 'yahoo':'Yahoo',
    'wikipedia':'维基百科', 'wikidata':'Wikidata', 'moegirl':'萌娘百科',
    'prts':'PRTS 明日方舟 Wiki', 'bwiki':'哔哩哔哩蔚蓝档案 Wiki',
    'github':'GitHub', 'stackexchange':'Stack Overflow', 'mdn':'MDN',
    'crossref':'Crossref', 'arxiv':'arXiv', 'openlibrary':'Open Library',
    'archive':'Internet Archive', 'npm':'npm', 'hackernews':'Hacker News',
}


class SearchHit(tuple):
    """Keep the legacy URL/title/snippet protocol; optional real API evidence."""
    def __new__(cls, url, title, snippet='', content=''):
        obj = super().__new__(cls,(url,title,snippet))
        obj.content = content
        return obj


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>',' ',str(value or ''))).strip()


class Links(HTMLParser):
    def __init__(self, base):
        super().__init__(); self.base=base; self.current=None; self.items=[]

    def handle_starttag(self, tag, attrs):
        if tag=='a': self.current=[urljoin(self.base,dict(attrs).get('href','')),'']

    def handle_data(self, data):
        if self.current is not None: self.current[1]+=data

    def handle_endtag(self, tag):
        if tag=='a' and self.current is not None:
            url,title=self.current
            if len(title.strip())>=3 and urlsplit(url).scheme in ('http','https'):
                self.items.append(SearchHit(url,plain(title)))
            self.current=None


def discover(query, channel, timeout, fetch):
    q=quote(query)
    engines={
        'duck':f'https://html.duckduckgo.com/html/?q={q}',
        'baidu':f'https://www.baidu.com/s?wd={q}',
        'sogou':f'https://www.sogou.com/web?query={q}',
        'so':f'https://www.so.com/s?q={q}',
        'google':f'https://www.google.com/search?q={q}',
        'brave':f'https://search.brave.com/search?q={q}',
        'yahoo':f'https://search.yahoo.com/search?p={q}',
    }
    if channel in engines:
        raw=fetch(engines[channel],timeout)
        if re.search(r'captcha|人机验证|百度安全验证|access denied|unusual traffic',raw[:12000],re.I):
            raise PermissionError('搜索入口验证或拒绝访问')
        parser=Links(engines[channel]);parser.feed(raw)
        host=urlsplit(engines[channel]).hostname
        return [h for h in parser.items if urlsplit(h[0]).hostname!=host or '/link?' in h[0]][:15]
    wiki={
        'wikipedia':'https://zh.wikipedia.org/w/api.php',
        'moegirl':'https://zh.moegirl.org.cn/api.php',
        'prts':'https://prts.wiki/api.php',
        'bwiki':'https://wiki.biligame.com/ba/api.php',
    }
    if channel in wiki:
        params=urlencode(dict(action='query',generator='search',gsrsearch=query,gsrlimit=3,
                              prop='extracts|info',exintro=1,explaintext=1,inprop='url',format='json'))
        data=json.loads(fetch(wiki[channel]+'?'+params,timeout))
        if data.get('error'): raise ValueError('来源 API 不可用')
        return [SearchHit(p.get('fullurl') or urljoin(wiki[channel],'/wiki/'+quote(p['title'])),
                          p['title'],plain(p.get('extract')),plain(p.get('extract')))
                for p in data.get('query',{}).get('pages',{}).values()]
    if channel=='wikidata':
        data=json.loads(fetch('https://www.wikidata.org/w/api.php?'+urlencode(dict(
            action='wbsearchentities',search=query,language='zh',format='json',limit=3)),timeout))
        return [SearchHit(p['concepturi'],p.get('label',''),p.get('description','')) for p in data.get('search',[])]
    if channel=='github':
        data=json.loads(fetch(f'https://api.github.com/search/repositories?q={q}&per_page=3',timeout))
        return [SearchHit(p['html_url'],p['full_name'],p.get('description') or '') for p in data.get('items',[])]
    if channel=='stackexchange':
        data=json.loads(fetch(f'https://api.stackexchange.com/2.3/search/advanced?site=stackoverflow&q={q}&pagesize=3&filter=withbody',timeout))
        if data.get('error_id'):raise ValueError('来源 API 不可用')
        return [SearchHit(p['link'],plain(p['title']),plain(p.get('body')),plain(p.get('body'))) for p in data.get('items',[])]
    if channel=='mdn':
        data=json.loads(fetch(f'https://developer.mozilla.org/api/v1/search?q={q}&size=3',timeout))
        return [SearchHit(urljoin('https://developer.mozilla.org',p['mdn_url']),p['title'],p.get('summary','')) for p in data.get('documents',[])[:3]]
    if channel=='crossref':
        data=json.loads(fetch(f'https://api.crossref.org/works?query={q}&rows=3',timeout))
        return [SearchHit(p.get('URL',''),' '.join(p.get('title',[])),plain(p.get('abstract')),plain(p.get('abstract')))
                for p in data.get('message',{}).get('items',[])]
    if channel=='arxiv':
        raw=fetch(f'https://export.arxiv.org/api/query?search_query=all:{q}&max_results=3',timeout)
        if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():return []
        root=ET.fromstring(raw);ns={'a':'http://www.w3.org/2005/Atom'}
        return [SearchHit(p.findtext('a:id','',ns).replace('http:','https:',1),p.findtext('a:title','',ns),
                          p.findtext('a:summary','',ns),p.findtext('a:summary','',ns)) for p in root.findall('a:entry',ns)]
    if channel=='openlibrary':
        data=json.loads(fetch(f'https://openlibrary.org/search.json?q={q}&limit=3',timeout))
        return [SearchHit('https://openlibrary.org'+p['key'],p.get('title',''),' '.join(p.get('author_name',[]))) for p in data.get('docs',[])]
    if channel=='archive':
        params=urlencode({'q':query,'output':'json','rows':3,'fl[]':['identifier','title','description']},doseq=True)
        data=json.loads(fetch('https://archive.org/advancedsearch.php?'+params,timeout))
        return [SearchHit('https://archive.org/details/'+quote(p['identifier']),str(p.get('title','')),plain(p.get('description')))
                for p in data.get('response',{}).get('docs',[])]
    if channel=='npm':
        data=json.loads(fetch(f'https://registry.npmjs.org/-/v1/search?text={q}&size=3',timeout))
        return [SearchHit(p['package']['links']['npm'],p['package']['name'],p['package'].get('description','')) for p in data.get('objects',[])]
    if channel=='hackernews':
        data=json.loads(fetch(f'https://hn.algolia.com/api/v1/search?query={q}&hitsPerPage=3',timeout))
        return [SearchHit(p.get('url') or 'https://news.ycombinator.com/item?id='+p['objectID'],p.get('title') or '',plain(p.get('story_text')))
                for p in data.get('hits',[])]
    raise ValueError('未知搜索渠道')
