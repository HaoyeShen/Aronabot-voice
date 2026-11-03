"""Attach local decrypted images to the same reply/Agent request."""
import base64
import io
from pathlib import Path


def attach_images(messages, paths, profile):
    from PIL import Image, ImageOps
    images = []
    for path in dict.fromkeys(str(p) for p in paths if p):
        if len(images) >= 3:
            break
        file = Path(path)
        if not file.is_file() or file.stat().st_size > 20 * 1024 * 1024:
            continue
        try:
            with Image.open(file) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1280, 1280))
                image = image.convert('RGB')
                output = io.BytesIO()
                image.save(output, 'JPEG', quality=85)
            images.append(base64.b64encode(output.getvalue()).decode('ascii'))
        except (OSError, ValueError):
            continue
    if not images:
        return 0
    index = next((i for i in range(len(messages)-1, -1, -1) if messages[i].get('role') == 'user'), None)
    if index is None:
        messages.append({'role': 'user', 'content': '[图片]'})
        index = len(messages)-1
    message = dict(messages[index])
    text = message.get('content', '')
    if not isinstance(text, str):
        return 0  # Already attached in this reply chain.
    if profile.get('provider') == 'ollama':
        message.update(content=text, images=images)
    else:
        message['content'] = [{'type': 'text', 'text': text}] + [
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + data}}
            for data in images]
    messages[index] = message
    return len(images)
