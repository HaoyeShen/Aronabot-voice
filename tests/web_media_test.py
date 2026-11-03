"""Public image decoding and isolated webpage-only rendering."""
import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import app.web_media as media

test_parent = media.DATA / 'temp'
test_parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=test_parent) as tmp:
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), 'cyan').save(buffer, 'JPEG')
    with patch.object(media, 'DATA', Path(tmp)), patch.object(media, 'fetch_public', return_value=(buffer.getvalue(), 'utf-8')):
        path = media.download_image('https://example.org/photo.jpg')
        with Image.open(path) as image:
            assert image.format == 'PNG' and image.size == (64, 48)
    with patch.object(media, 'DATA', Path(tmp)), patch.object(media, 'fetch_public', return_value=(b'not an image', 'utf-8')):
        try:
            media.download_image('https://example.org/fake.png')
        except Exception:
            pass
        else:
            raise AssertionError('invalid file accepted as image')
    # Browser requests cannot reach these embedded local/remote resources.
    markup = ('<script>fetch("http://127.0.0.1/private")</script>'
              '<iframe src="file:///C:/private.txt"></iframe>'
              '<article><h1>网页内容测试</h1><p>' + '这是公开网页正文，只能截取网页内容。'*12 + '</p></article>')
    with patch.object(media, 'DATA', Path(tmp)), patch.object(media, 'fetch_public', return_value=(markup.encode(), 'utf-8')) as fetch:
        path = media.capture_page('https://example.org/article')
        fetch.assert_called_once_with('https://example.org/article')
        with Image.open(path) as image:
            assert image.format == 'PNG' and image.width == 960 and 150 < image.height < 4000
print('WEB MEDIA TEST PASSED')
