"""UIA-only comment submission; never fall back to screenshot matching."""
from types import SimpleNamespace
from datetime import datetime, timedelta
import re
import time

from .moments_publish import find, composer, read_edit
from .moments_reader import valid_comment


def normal(text):
    return re.sub(r'\s+', '', str(text or ''))


def same_post(left, right):
    return (str(left.get('id')) == str(right.get('id'))
            and left.get('username') == right.get('username')
            and left.get('create_time') == right.get('create_time')
            and normal(left.get('text')) == normal(right.get('text')))


def time_matches(stamp, shown, now=None):
    """Reject ambiguous dates instead of confusing repeated captions."""
    now = time.time() if now is None else now
    age = now - float(stamp or 0)
    shown = normal(shown)
    if not 0 <= age <= 86400:return False
    if shown == '刚刚':return age <= 300
    match = re.fullmatch(r'(\d+)(分钟|小时)前',shown)
    if match:
        unit = 60 if match[2] == '分钟' else 3600
        return abs(age-int(match[1])*unit) <= unit + 60
    match = re.fullmatch(r'(今天|昨天)?(\d{1,2}):(\d{2})',shown)
    if match:
        expected = datetime.fromtimestamp(now).replace(hour=int(match[2]),minute=int(match[3]),second=0,microsecond=0)
        if match[1] == '昨天':expected -= timedelta(days=1)
        return abs(float(stamp)-expected.timestamp()) < 90
    return False


def locate_post(moment,post,allowed):
    """Find the exact caption/author/time in a bounded UIA-only viewport scan."""
    for attempt in range(8):
        if not allowed():return None
        matches=[i for i in moment._read_visible_items(refresh=True)
                 if i.publisher==post.get('nickname') and normal(i.text)==normal(post.get('text'))
                 and time_matches(post.get('create_time'),i.timestamp)]
        if len(matches)>1:return None
        if matches:return matches[0]
        scroll=getattr(moment,'_scroll',None)
        if not callable(scroll) or not scroll(delta=-120,times=2):break
        time.sleep(.2)
    return None


def submit(adapter, post, text, *, dry_run=False, allowed=lambda:True):
    from .wechat_adapter import SendResult
    from wechatauto.moment import Moment, MomentDB
    uia = adapter._uia
    if not uia or not valid_comment(text):return SendResult(False,'微信未连接或评论内容不合格。')
    main = uia._win;edit = None;attempted = False
    try:
        db = MomentDB(adapter._db)
        rows = db.get_moments(limit=200,username=post.get('username'),keyword=post.get('text'),
                              since=int(time.time())-7*86400)
        exact = [r for r in rows if r.get('username') == post.get('username')
                 and normal(r.get('text')) == normal(post.get('text'))]
        if len(rows) >= 200 or len(exact) != 1 or not same_post(exact[0],post):
            return SendResult(False,'朋友圈目标不唯一或已变化，未评论。')
        if post.get('username') == adapter.self_wxid or any(
                c.get('username') == adapter.self_wxid for c in exact[0].get('comments',[])):
            return SendResult(False,'这是自己的动态或已经评论过，未重复评论。')
        if composer(main)[0] is not None or find(main,lambda c:c.ClassName=='#32770') is not None:
            return SendResult(False,'微信已有编辑窗口，请先处理；本次未评论。')
        adapter._prepare_chat_window(uia)
        discover = find(main,lambda c:c.Name=='发现' and c.ClassName=='mmui::XTabBarItem')
        if discover is None or not uia._click_ctrl(discover):return SendResult(False,'无法打开微信发现页。')
        time.sleep(.5)
        entry = find(main,lambda c:c.Name=='朋友圈' and c.ClassName=='mmui::ExtensionDiscoverContentCell')
        if entry is not None:uia._click_ctrl(entry);time.sleep(.5)
        gui = SimpleNamespace(main_hwnd=main.NativeWindowHandle,pid=main.ProcessId)
        moment = Moment(SimpleNamespace(_gui=gui,SwitchToMoments=lambda:None))
        # No fuzzy upstream find_moment fallback: require full caption and author.
        item=locate_post(moment,post,allowed if not dry_run else lambda:True)
        if item is None:return SendResult(False,'朋友圈未找到唯一的完整目标正文/作者/时间，未猜测或评论。')
        if dry_run:return SendResult(True,'朋友圈数据库与UIA目标核对通过，未打开评论或发送。')
        buttons = [c for c in item.control.GetChildren() if c.ControlTypeName=='ButtonControl'
                   and c.Name in ('','更多','操作','…','...') and not getattr(c,'IsOffscreen',False)]
        if len(buttons) != 1:return SendResult(False,'当前微信未暴露唯一的朋友圈操作按钮，未评论。')
        uia._click_ctrl(buttons[0]);time.sleep(.3)
        root = moment._api or main
        comment = find(root,lambda c:c.Name=='评论' and c.ControlTypeName in ('ButtonControl','MenuItemControl'))
        if comment is None:return SendResult(False,'朋友圈评论菜单未提供可访问控件，未评论。')
        uia._click_ctrl(comment);time.sleep(.3)
        edit = find(root,lambda c:c.ControlTypeName=='EditControl' and ('评论' in c.Name or c.Name==''))
        if edit is None or read_edit(edit):return SendResult(False,'评论输入框不可核对或已有草稿，未覆盖。')
        edit.GetValuePattern().SetValue(text)
        if read_edit(edit)!=text:return SendResult(False,'朋友圈评论输入校验失败，未提交。')
        node = edit;button = None
        for _ in range(4):
            node = node.GetParentControl()
            if node is None:break
            button = find(node,lambda c:c.Name=='发送' and c.ControlTypeName=='ButtonControl',100)
            if button is not None:break
        if button is None:return SendResult(False,'当前微信未暴露评论发送按钮；保持禁止截图读取，未发送评论。')
        # Recheck the editor and live caption immediately before the only commit.
        if normal(item.text)!=normal(post.get('text')) or read_edit(edit)!=text:
            return SendResult(False,'提交前朋友圈目标变化，已取消。')
        if not allowed():
            return SendResult(False,'朋友圈自动评论已关闭，取消发送。')
        attempted = True
        uia._click_ctrl(button)
        for _ in range(8):
            for current in db.get_moments(limit=30,username=post.get('username'),keyword=post.get('text')):
                if same_post(current,post) and any(c.get('username')==adapter.self_wxid and
                        c.get('content')==text for c in current.get('comments',[])):
                    return SendResult(True,'朋友圈评论已发送并核对。')
            time.sleep(.5)
        return SendResult(False,'已提交评论，数据库尚未确认；不会自动重复发送。','uncertain')
    except Exception as exc:
        return SendResult(False,'朋友圈评论未完成：'+type(exc).__name__,'uncertain' if attempted else '')
    finally:
        try:
            if edit is not None and not attempted and read_edit(edit)==text:
                edit.GetValuePattern().SetValue('')
            if edit is None or not attempted or not read_edit(edit):adapter._prepare_chat_window(uia)
        except Exception:pass
