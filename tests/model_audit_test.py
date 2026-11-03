"""Full model input/output audit, raw invalid outputs and secret masking."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model_client import ModelClient, ModelError
from app.model_audit import model_call, redact, register_secret


def main():
    case = unittest.TestCase()
    client = ModelClient({'provider': 'openai', 'model': 'audit-test', 'base_url': 'https://invalid.test'})
    client.api_key = 'private-runtime-key'
    register_secret('admin-secret-test')
    request = [{'role': 'user', 'content': '上下文开始 ' + '正文' * 3000 + ' 输入末尾 private-runtime-key admin-secret-test'}]
    output = '不合格的超长回答' * 2000 + ' 输出末尾 private-runtime-key'
    def response(data):
        return io.BytesIO(json.dumps(data, ensure_ascii=False).encode())
    with patch.object(client, '_opener') as opener:
        opener.return_value.open.return_value = response({'choices': [{'message': {'content': output}}]})
        with case.assertLogs('app.model_audit', level='INFO') as capture:
            result = model_call(client, 'chat', '回复消息：不合格回答重写', request, '人格设定')
        log = '\n'.join(capture.output)
        assert result.text == output and request[0]['content'].endswith('admin-secret-test')
        assert all(s in log for s in ['不合格回答重写', '输入末尾', '输出末尾', '人格设定', '接口原始输出'])
        assert 'private-runtime-key' not in log and 'admin-secret-test' not in log
        opener.return_value.open.return_value = response({'choices': [{'message': {'content': ''}}]})
        with case.assertLogs('app.model_audit', level='INFO') as capture:
            with case.assertRaises(ModelError):
                model_call(client, 'chat', '回复消息：生成回答', request)
        assert '接口原始输出' in '\n'.join(capture.output) and '调用失败' in '\n'.join(capture.output)
    media = redact({'images': ['A' * 10000], 'url': 'data:image/jpeg;base64,' + 'B' * 10000})
    assert len(str(media)) < 200
    assert redact({'password': 'secret', 'api_key': 'value'}) == {'password': '[已隐藏]', 'api_key': '[已隐藏]'}
    print('MODEL AUDIT TESTS PASSED')


if __name__ == '__main__':
    main()
