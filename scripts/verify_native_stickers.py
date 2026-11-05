"""Native-sticker integration checks; the only permitted target is test."""
from pathlib import Path
import sys
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.wechat_adapter import WeChatAutoAdapter
from app.config_store import ConfigStore
from app.native_sticker_panel import NativeStickerPanel


def main():
    config=ConfigStore();adapter=WeChatAutoAdapter(config.wechat,lambda _:None)
    panel=None
    try:
        adapter.start([{'name':'test','type':'group','enabled':True}])
        if not adapter.ensure_chat_open('test'):raise RuntimeError('无法准确打开 test 群，取消')
        if not adapter.is_same_chat(adapter._uia.current_chat(),'test'):raise RuntimeError('当前不是 test 群，取消')
        if '--send' in sys.argv:
            from app.config_store import PersonaStore,StickerStore
            from app.memory import MemoryStore
            from app.training import TrainingStore
            from app.service import BotService
            from app.model_client import make_client
            from app.wechat_adapter import IncomingMessage
            from app.prompting import build_system_prompt
            from app.agent_runtime import RUNTIME
            service=BotService(config,PersonaStore(),StickerStore(),MemoryStore(),TrainingStore())
            service._adapter=adapter;service._running=True
            target=config.target_by_name('test') or {}
            persona=service.personas.get(target.get('persona_id') or config.data.get('active_persona_id'))
            request='我们终于把项目做完啦，谢谢你陪着！请检索所有已安装页，预览候选后选一张最合适的原生动画表情庆祝，不要选emoji或普通图片。'
            incoming=IncomingMessage('test','Harry昊昊',request,chat_type='group')
            profile=config.active_profile()
            with RUNTIME.task('原生动画表情端到端验证','test',adapter.chat_identity('test')):
                answer,usage,system,info=service._agent_reply(make_client(profile),[{'role':'user','content':request}],
                    build_system_prompt(persona,[],max_chars=25,max_sentence_chars=25),profile,incoming,request,
                    persona=persona,kind='debug',require_running=True,network_enabled=False)
                selected=info.get('native_sticker')
                if not selected:
                    Path('data/temp/native-sticker-selection-failure.json').write_text(json.dumps({'agent':info.get('agent'),'reply':answer.text},ensure_ascii=False),encoding='utf-8')
                    raise RuntimeError('模型未选择经过完整目录/画面核对的原生动画表情')
                if not adapter.is_same_chat(adapter._uia.current_chat(),'test'):raise RuntimeError('当前会话不是test，取消')
                result=adapter.send_native_sticker('test',selected)
                report={'ok':result.ok,'message':result.message,'detail':result.detail,'selected':selected['name'],
                        'reason':selected['reason'],'agent':info.get('agent'),'target':'test'}
                Path('data/temp/native-sticker-send-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=False))
            service._running=False;service.memory.close();service.training.close()
            return
        panel=NativeStickerPanel(adapter._uia).open()
        result=panel.inspect()
        catalog=adapter.native_stickers().catalog()
        page_report=[]
        for page in catalog['pages']:
            root=panel.page('自定义表情' if page['id']=='favorite' else page['name'])
            from app.native_sticker_panel import nodes
            cells=[n for n in nodes(root) if 'EmoticonItemView' in n.ClassName]
            page_report.append({'page':page['name'],'expected':len(page['items']),'visible_items':len(cells),
                                'classes':sorted(set(n.ClassName for n in cells))})
        path=Path('data/temp/native-sticker-panel-probe.json')
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'probe_only':True,'target':'test','controls':len(result),'pages':page_report},ensure_ascii=False))
    finally:
        if panel:panel.close()
        adapter.stop()


if __name__=='__main__':main()
