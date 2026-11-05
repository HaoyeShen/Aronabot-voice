"""Reviewed sticker styles shared by chat replies and diary images."""
from functools import lru_cache
import hashlib
from pathlib import Path

STYLES = {'pending': '待确认画风', 'chibi': 'Q版手绘', 'official_portrait': '官方立绘'}
APPROVED_HASHES = {'8102cf96f2b9307cd52204d6fad86f358664e2d06760b3b4c20da8077a34af64': 'official_portrait', '68e9c9dcf48ee66fd2fc19983a0c8b7021ff62c9ba917bb7a9a32163e5bbab1f': 'official_portrait', 'd8fef91d7d26be7545eaa10a12057cc412392ced5c816bb315868773d4fadb03': 'official_portrait', '070646bf5d53d48dfc119bd6ad98a06694d86c13f8002da8b46cc951c7322eda': 'official_portrait', '7b4b98dfcf7e692d08e4def04e161bcd61fded966f330add97ff958472349146': 'official_portrait', '778928a8d12a6573ba3b15421a7a7f819793f0e332e3a4de1a2d20b155b50d07': 'official_portrait', 'c84663e2353594da76d3b4e5e4bd2bc16cd89b6ffbd59d0a6ce652c1868fd6c6': 'official_portrait', 'a91360b27c275479d43854d26f0b7b25acc679ecb765be0c37c895dd26ae90a3': 'official_portrait', 'f6f393695b7891a70e83fe98daf98c721c3bd043efe188953e9d037bf32bc48b': 'chibi', 'c5421dde3da11f412786dd866225c1bbfd386b5043d915f141b26742152c4583': 'chibi', '9825bbfbf474eb808aa545675fe8f7760f6c28e9455bf7e111eba682e70cb08f': 'chibi', 'd8c5aa0b8cdd87a4f871e898da94178f8e2d03addd06ba40f56b947893cfedf1': 'chibi', '5866ebccfb247a61645a30576ac1b09e57a05cce13b5d576563b55e778572093': 'chibi', '03292d9c94de0af2c278b5cae19105fa9c8f1f59fae6b1f7e9ada4a2c6fdd5f5': 'chibi', '9ccdf1c7c6a5752c349660cdb7417c1dcfc4544d202398209aef34a057b690db': 'chibi', '7ba768863e24e515d25bc4378871e99ec4bf6c11d2dc3aebfa961e2089e1386f': 'chibi', 'b873dbd9a70d7e0e941174af807b8e91f0fccc154f205ae0b5d21c556f9c5df6': 'chibi'}
BLOCKED_HASHES = {'2a989cc73025dc6fbe023f19c2948dcc2d60714912c108fbd6b4901c6b37bb94', '9e0d00e21e5547046264ca6814f82651d9d42ab8c58e0c8a7e37a689d8508926', '1c788cc248f8bf58f2baba71ec2e5c32013ac15562b5da8260273185d3d8eb02', 'e3d8587cc630906e5b5e62d361303ffc569fc298489d69708ef4f9ca932db0eb', 'd91d801d8f39b4610fb2de8d672662562baf8946d9645d4996832f68882d3cf3', '0411cc9c9d94482c4a450adb9d4e0fe63d6711af7d9607118d68c3722dc82c6e', '88ccd28f44e5135ec14e6c493213b505cc56c1ae2838b0e842cd7ce84c818bfe', 'a1be3b6d50741d91279ca9fb4fbc31451fdf5183a20c9d4dbc6c0a8a2beaa9e0'}

@lru_cache(maxsize=512)
def _digest(path, modified, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def fingerprint(path):
    try:
        file=Path(path);stat=file.stat()
        if not file.is_file() or stat.st_size>20*1024*1024:return ''
        return _digest(str(file.resolve()),stat.st_mtime_ns,stat.st_size)
    except OSError:return ''

def reviewed_style(path, declared='pending'):
    digest=fingerprint(path)
    if not digest or digest in BLOCKED_HASHES:return 'pending'
    return APPROVED_HASHES.get(digest,declared if declared in ('chibi','official_portrait') else 'pending')

def allowed(item,path):
    return reviewed_style(path,item.get('style','pending')) in ('chibi','official_portrait')
