"""WeChat's native sticker popup, opened with a held Alt+E chord."""
import ctypes
import time

from .moments_publish import read_edit


def nodes(root,limit=4000):
    pending=[root];seen=0
    while pending and seen<limit:
        node=pending.pop();seen+=1
        try:
            yield node
            pending.extend(reversed(node.GetChildren()))
        except Exception:continue


def key(node):
    try:return tuple(node.GetRuntimeId())
    except Exception:return (node.ClassName,node.AutomationId,node.Name,node.NativeWindowHandle)


def alt_e():
    user=ctypes.windll.user32
    user.keybd_event(0x12,0,0,0)
    try:
        time.sleep(.08)
        user.keybd_event(0x45,0,0,0)
        time.sleep(.06)
    finally:
        user.keybd_event(0x45,0,2,0)
        user.keybd_event(0x12,0,2,0)


class NativeStickerPanel:
    def __init__(self,uia):self.uia=uia;self.before=set()

    def roots(self):
        import uiautomation as auto
        main=self.uia._win;pid=main.ProcessId
        result=[main]
        for root in auto.GetRootControl().GetChildren():
            try:
                if root.ProcessId==pid and root.NativeWindowHandle!=main.NativeWindowHandle:result.append(root)
            except Exception:pass
        return result

    def open(self):
        uia=self.uia;main=uia._win
        edit=uia._chat_input()
        if edit is None or read_edit(edit):raise ValueError('聊天输入框已有草稿或不可核对，未打开表情面板')
        if not uia._force_foreground(main.NativeWindowHandle):raise ValueError('微信未在前台，取消表情快捷键')
        self.before={key(n) for root in self.roots() for n in nodes(root)}
        edit.SetFocus()
        alt_e();time.sleep(.5)
        return self

    def new_nodes(self):
        return [n for root in self.roots() for n in nodes(root) if key(n) not in self.before]

    def inspect(self):
        return [{'class':n.ClassName,'name':n.Name,'id':n.AutomationId,'type':n.ControlTypeName}
                for n in self.new_nodes()]

    def root(self):
        return next((n for root in self.roots() for n in nodes(root)
                     if n.ClassName=='mmui::EmoticonPopover'),None)

    def page(self,name):
        root=self.root()
        if root is None:raise ValueError('没有找到原生表情面板')
        matches=[n for n in nodes(root) if n.ClassName=='mmui::EmoticonToolbarItem' and n.Name==name]
        if len(matches)!=1 or not self.uia._click_ctrl(matches[0]):raise ValueError('表情包页签不可唯一定位')
        time.sleep(.25)
        return self.root()

    def target(self,entry,page):
        root=self.page(page['name'] if page['id']!='favorite' else '自定义表情')
        expected=[s['name'] for s in page['items']]
        if expected.count(entry['name'])!=1:raise ValueError('同页表情名称重复，未猜测发送')
        for _ in range(12):
            items=[n for n in nodes(root) if n.ClassName in ('mmui::StoreEmoticonItemView','mmui::CustomEmoticonItemView','mmui::FavEmoticonItemView')]
            names=[n.Name for n in items]
            # Bind the item to the selected pack, not another page's same name.
            if names and all(name in expected for name in names):
                matches=[n for n in items if n.Name==entry['name'] and not n.IsOffscreen]
                if len(matches)==1:
                    images=[n for n in nodes(matches[0],30) if n.ClassName=='mmui::XImage' and n.ControlTypeName=='ButtonControl']
                    if len(images)==1:return images[0]
                    raise ValueError('原生表情图片按钮不可唯一核对')
            grid=next((n for n in nodes(root) if n.ClassName=='mmui::EmoticonGridView'),None)
            if grid is None:break
            try:
                import uiautomation as auto
                pattern=grid.GetScrollPattern()
                if not pattern.VerticallyScrollable:break
                pattern.Scroll(auto.ScrollAmount.NoAmount,auto.ScrollAmount.LargeIncrement)
            except Exception:break
            time.sleep(.2)
        raise ValueError('完整翻页后仍未唯一定位选中的原生表情')

    def foreground(self):
        # Refocusing the main window here dismisses the popup. Verify the
        # popup's owning process without switching focus away from it.
        pid=ctypes.c_ulong()
        handle=ctypes.windll.user32.GetForegroundWindow()
        ctypes.windll.user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
        return pid.value==self.uia._win.ProcessId and self.root() is not None

    def close(self):
        try:self.uia._win.SendKeys('{Esc}',waitTime=.05)
        except Exception:pass
