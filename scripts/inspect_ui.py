"""Open an isolated UI acceptance window; never connect to WeChat."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.paths import ASSETS
from app.ui.main_window import MainWindow
import tkinter as tk

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    config = ConfigStore(base/'config.json')
    config.set_active_profile('demo-local')
    config.bot['auto_start'] = False
    memory = MemoryStore(base/'memory.db')
    training = TrainingStore(base/'training.db', base/'files')
    service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                         memory, training, admin_state_path=base/'admin.json')
    root = tk.Tk()
    root._arona_no_autostart = True
    app = MainWindow(root, service, tk.PhotoImage(file=str(ASSETS/'app_icon.png')))
    root.title('ARONA · 新界面验收（不连接微信）')
    root.after(600000, app.close)
    root.mainloop()
    service.shutdown()
