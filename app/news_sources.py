"""Individually queried publishers, not duplicate search-engine endpoints."""
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class NewsSource:
    name: str
    host: str
    region: str
    topic: str

    def accepts(self, url):
        host = (urlsplit(url).hostname or '').lower()
        return host == self.host or host.endswith('.' + self.host)


SOURCES = tuple(NewsSource(*row) for row in (
    ('TapTap', 'taptap.cn', '国服', '蔚蓝档案'),
    ('17173', '17173.com', '国服', '蔚蓝档案'),
    ('3DM', '3dmgame.com', '国服', '蔚蓝档案'),
    ('游民星空', 'gamersky.com', '国服', '蔚蓝档案'),
    ('游侠网', 'ali213.net', '国服', '蔚蓝档案'),
    ('机核', 'gcores.com', '国服', '蔚蓝档案'),
    ('游戏时光', 'vgtime.com', '国服', '蔚蓝档案'),
    ('IT之家', 'ithome.com', '国服', '蔚蓝档案'),
    ('哔哩哔哩游戏', 'biligame.com', '国服', '蔚蓝档案'),
    ('4Gamer', '4gamer.net', '日服', 'ブルーアーカイブ'),
    ('Gamer', 'gamer.ne.jp', '日服', 'ブルーアーカイブ'),
    ('GAME Watch', 'game.watch.impress.co.jp', '日服', 'ブルーアーカイブ'),
    ('Famitsu', 'famitsu.com', '日服', 'ブルーアーカイブ'),
    ('电击在线', 'dengekionline.com', '日服', 'ブルーアーカイブ'),
    ('AUTOMATON', 'automaton-media.com', '日服', 'ブルーアーカイブ'),
    ('gamebiz', 'gamebiz.jp', '日服', 'ブルーアーカイブ'),
    ('INSIDE', 'inside-games.jp', '日服', 'ブルーアーカイブ'),
    ('Pocket Gamer', 'pocketgamer.com', '国际服', 'Blue Archive'),
    ('GamingonPhone', 'gamingonphone.com', '国际服', 'Blue Archive'),
    ('Siliconera', 'siliconera.com', '国际服', 'Blue Archive'),
    ('Gematsu', 'gematsu.com', '国际服', 'Blue Archive'),
    ('QooApp', 'qoo-app.com', '国际服', 'Blue Archive'),
    ('GamerBraves', 'gamerbraves.com', '国际服', 'Blue Archive'),
    ('MMORPG', 'mmorpg.com', '国际服', 'Blue Archive'),
    ('MMOBomb', 'mmobomb.com', '国际服', 'Blue Archive'),
    ('Games Press', 'gamespress.com', '国际服', 'Blue Archive'),
    ('Inven', 'inven.co.kr', '国际服', '블루 아카이브'),
    ('This Is Game', 'thisisgame.com', '国际服', '블루 아카이브'),
    ('Ruliweb', 'ruliweb.com', '国际服', '블루 아카이브'),
    ('Game Focus', 'gamefocus.co.kr', '国际服', '블루 아카이브'),
))

# Publisher-owned feeds verified as XML, independent of search indexing.
FEEDS = {
    '4gamer.net': 'https://www.4gamer.net/rss/index.xml',
    'automaton-media.com': 'https://automaton-media.com/feed/',
    'inside-games.jp': 'https://www.inside-games.jp/rss/index.rdf',
    'gamingonphone.com': 'https://gamingonphone.com/feed/',
    'gematsu.com': 'https://www.gematsu.com/feed/',
    'ithome.com': 'https://www.ithome.com/rss/',
    'gcores.com': 'https://www.gcores.com/rss',
}


class SourceCandidates(list):
    def __init__(self, rows=(), feed_state='not_configured'):
        super().__init__(rows)
        self.feed_state = feed_state
