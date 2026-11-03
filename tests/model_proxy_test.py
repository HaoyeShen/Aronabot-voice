"""Proxy routing must not trigger Windows' unbounded reverse-DNS lookup."""
import sys
from pathlib import Path
import urllib.request
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model_client import _NoLookupProxyHandler


with patch('socket.getfqdn', side_effect=AssertionError('unexpected DNS lookup')), \
        patch('socket.gethostbyname', side_effect=AssertionError('unexpected DNS lookup')):
    handler = _NoLookupProxyHandler({'https':'http://127.0.0.1:7890'}, ['*.internal.example', '<local>'])
    request = urllib.request.Request('https://api.example.com/v1/chat/completions')
    handler.proxy_open(request, 'http://127.0.0.1:7890', 'https')
    assert request.host == '127.0.0.1:7890' and request._tunnel_host == 'api.example.com'
    for url in ('https://api.internal.example/path', 'http://localhost:8000/v1',
                'http://127.2.3.4:8000/v1', 'http://[::1]:8000/v1', 'http://localservice/v1'):
        request = urllib.request.Request(url)
        before = request.host
        handler.proxy_open(request, 'http://127.0.0.1:7890', request.type)
        assert request.host == before
    request = urllib.request.Request('https://api.example.com/v1')
    _NoLookupProxyHandler({'https':'http://127.0.0.1:7890','no':'*'}).proxy_open(
        request, 'http://127.0.0.1:7890', 'https')
    assert request.host == 'api.example.com'
print('MODEL PROXY TESTS PASSED')
