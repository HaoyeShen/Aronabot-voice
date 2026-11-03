"""Compare API routes using synthetic text; never logs keys or response bodies."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import _bounded
from app.config_store import ConfigStore
from app.model_client import ModelClient
from app.secretstore import unprotect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    profile=ConfigStore().active_profile()
    client=ModelClient(profile)
    key=unprotect(profile.get('api_key_enc',''))
    results=[]
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    for size in ('short','long'):
        for route in ('direct','system'):
            started=time.monotonic()
            def probe():
                content=('连接测试文本。'*1500 if size=='long' else '')+'只回复 OK。'
                payload={'model':profile['model'],'messages':[{'role':'user','content':content}],
                         'max_tokens':16,'stream':False,'temperature':0}
                request=urllib.request.Request(client._chat_endpoint(),data=json.dumps(payload).encode(),
                    method='POST',headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if route=='direct' else client._opener()
                try:
                    with opener.open(request,timeout=8) as response:
                        data=response.read(100000)
                        return {'http':response.status,'server':response.headers.get('Server',''),
                                'content_type':response.headers.get_content_type(),
                                'valid_chat':bool(json.loads(data).get('choices'))}
                except urllib.error.HTTPError as exc:
                    data=exc.read(2048)
                    return {'http':exc.code,'server':exc.headers.get('Server',''),
                            'content_type':exc.headers.get_content_type(),
                            'html_error':b'<html' in data.lower(),'json_error':data.lstrip().startswith(b'{')}
            state, result=_bounded(probe,time.monotonic()+12,lambda:False)
            row={'route':route,'input':size,'state':state,'elapsed_ms':int((time.monotonic()-started)*1000)}
            if state=='ok':row.update(result)
            elif state=='error':row['error_kind']=type(result).__name__
            results.append(row)
            target.write_text(json.dumps({'executable':Path(sys.executable).name,'results':results},indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
