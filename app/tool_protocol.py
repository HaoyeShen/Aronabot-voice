"""Recognize model-internal tools without treating them as chat text."""
import json
import re


def internal_output(text):
    value=str(text or '')
    return bool(re.search(r'<\/?(?:tool_call|function_call|function=)|\[(?:SEARCH|搜索)\s*[:：]|"action"\s*:\s*"tool"',value,re.I)
                or re.search(r'"(?:tool|name)"\s*:\s*"(?:search_web|read_webpage|search_baike|search_knowledge|list_native_stickers|preview_native_stickers)"',value))


def parse_action(raw):
    text=str(raw or '').strip()
    wrappers=re.findall(r'<(?:tool_call|function_call)\s*>(.*?)</(?:tool_call|function_call)\s*>',text,re.I|re.S)
    if wrappers:
        if len(wrappers)!=1:return {'action':'invalid'}
        text=wrappers[0].strip()
    if text.startswith('```') and text.endswith('```'):
        text=text.split('\n',1)[-1].rsplit('```',1)[0].strip()
    if text.startswith(('{','[')):
        try:value=json.loads(text)
        except ValueError:return {'action':'invalid'}
        if not isinstance(value,dict):return {'action':'invalid'}
        if 'action' not in value and isinstance(value.get('function'),dict):value=value['function']
        if 'action' not in value and isinstance(value.get('name'),str):
            arguments=value.get('arguments',value.get('parameters',{}))
            if isinstance(arguments,str):
                try:arguments=json.loads(arguments)
                except ValueError:return {'action':'invalid'}
            return {'action':'tool','tool':value['name'],'arguments':arguments}
        return value
    if internal_output(text):return {'action':'invalid'}
    return None
