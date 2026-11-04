"""WeChat 4.x native Moments composer, with verified controls and one commit."""
from pathlib import Path
import time


def find(root,predicate,limit=3000):
    pending=[root];count=0
    while pending and count<limit:
        node=pending.pop();count+=1
        try:
            if getattr(node,'IsOffscreen',False):continue
            if predicate(node):return node
            pending.extend(node.GetChildren())
        except Exception:pass
    return None


def composer(root):
    edit=find(root,lambda c:c.ClassName=='mmui::XValidatorTextEdit' and c.ControlTypeName=='EditControl')
    if edit is None:return None,None
    node=edit
    for _ in range(10):
        node=node.GetParentControl()
        if node is None:break
        publish=find(node,lambda c:c.Name=='发表' and c.ClassName=='mmui::XOutlineButton',300)
        cancel=find(node,lambda c:c.Name=='取消' and c.ClassName=='mmui::XOutlineButton',300)
        if publish is not None and cancel is not None:return node,edit
    return None,None


def read_edit(edit):
    try:return edit.GetValuePattern().Value.strip()
    except Exception:return edit.GetTextPattern().DocumentRange.GetText(-1).strip()


def cancel_own_composer(uia,created):
    cancel=find(created,lambda c:c.Name=='取消' and c.ClassName=='mmui::XOutlineButton')
    if cancel is None:return
    uia._click_ctrl(cancel);time.sleep(.4)
    discard=find(uia._win,lambda c:c.Name=='不保留' and c.ClassName=='mmui::XOutlineButton')
    if discard is not None:uia._click_ctrl(discard);time.sleep(.4)


def publish(adapter,text,image_path=None,*,dry_run=False):
    from .wechat_adapter import SendResult
    from wechatauto.moment import MomentDB
    uia=adapter._uia
    if not uia:return SendResult(False,'微信未连接，日记草稿已保存。')
    if not text.strip():return SendResult(False,'日记正文为空，已取消发布。')
    main=uia._win;created=None;attempted=False;dialog=None
    def posted():
        return any(str(p.get('text','')).strip()==text.strip() for p in MomentDB(adapter._db).get_moments(
            limit=5,username=adapter.self_wxid,keyword=text.splitlines()[0],since=int(time.time())-86400*2))
    try:
        if not dry_run:
            try:
                if posted():return SendResult(True,'今天这篇日记已在朋友圈，不重复发表。')
            except Exception:pass
        existing,_=composer(main)
        if existing is not None:return SendResult(False,'微信已有朋友圈编辑窗口，请先处理；日记草稿已保留。')
        if find(main,lambda c:c.ClassName=='#32770') is not None:
            return SendResult(False,'微信已有文件选择窗口，请先处理；日记草稿已保留。')
        adapter._prepare_chat_window(uia)
        discover=find(main,lambda c:c.Name=='发现' and c.ClassName=='mmui::XTabBarItem')
        if discover is None or not uia._click_ctrl(discover):return SendResult(False,'无法进入微信发现页，日记草稿已保存。')
        time.sleep(.7)
        entry=find(main,lambda c:c.Name=='发表' and c.ClassName=='mmui::XTabBarItem')
        if entry is None:
            moments=find(main,lambda c:c.Name=='朋友圈' and c.ClassName=='mmui::ExtensionDiscoverContentCell')
            if moments is not None:uia._click_ctrl(moments);time.sleep(.7)
            entry=find(main,lambda c:c.Name=='发表' and c.ClassName=='mmui::XTabBarItem')
        if entry is None:return SendResult(False,'当前微信未提供朋友圈发表入口，日记草稿已保存。')
        if image_path:
            image=Path(image_path).resolve()
            if not image.is_file() or image.suffix.lower() not in ('.png','.jpg','.jpeg'):
                return SendResult(False,'日记配图不可用，已取消发布。')
            uia._click_ctrl(entry)
            deadline=time.monotonic()+6;dialog=None
            while time.monotonic()<deadline:
                # The native file dialog is an owned child in this WeChat
                # build, not necessarily a desktop-root child.
                dialog=find(main,lambda c:c.ClassName=='#32770' and c.Name=='选择文件')
                if dialog is not None:break
                time.sleep(.2)
            if dialog is None:return SendResult(False,'微信选择配图窗口未打开，日记草稿已保存。')
            edit=find(dialog,lambda c:c.AutomationId=='1148' and c.ControlTypeName=='EditControl')
            button=find(dialog,lambda c:c.AutomationId=='1' and c.ControlTypeName=='ButtonControl')
            if edit is None or button is None:return SendResult(False,'微信配图控件不兼容，日记草稿已保存。')
            edit.GetValuePattern().SetValue(str(image))
            if read_edit(edit)!=str(image):return SendResult(False,'日记配图路径校验失败，已取消发布。')
            uia._click_ctrl(button)
        else:
            uia._click_ctrl(entry,right=True);time.sleep(.3)
            option=find(main,lambda c:c.Name in ('发表文字','仅文字') and c.ControlTypeName in ('MenuItemControl','ButtonControl'))
            if option is None:return SendResult(False,'当前微信没有可识别的纯文字发表入口，请开启内置表情包配图；日记已保存。')
            uia._click_ctrl(option)
        deadline=time.monotonic()+6;edit=None
        while time.monotonic()<deadline:
            created,edit=composer(main)
            if created is not None:break
            time.sleep(.2)
        if created is None:return SendResult(False,'微信日记编辑窗口未打开，草稿已保留。')
        if read_edit(edit):return SendResult(False,'微信编辑窗口已有文字，已取消覆盖。')
        edit.GetValuePattern().SetValue(text)
        if read_edit(edit)!=text.strip():return SendResult(False,'微信日记正文校验失败，未发表。')
        if dry_run:
            cancel_own_composer(uia,created)
            return SendResult(True,'日记编辑与配图验证通过，未点击发表。')
        button=find(created,lambda c:c.Name=='发表' and c.ClassName=='mmui::XOutlineButton')
        if button is None:return SendResult(False,'日记发表按钮不可用，未发表。')
        # Commit exactly once. A timeout after this point is uncertain and
        # cannot trigger an automatic retry, including after a restart.
        attempted=True
        if not uia._click_ctrl(button):
            attempted=False;return SendResult(False,'日记发表控件无法点击，草稿已保留。')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            try:
                if posted():return SendResult(True,'阿洛娜的今日日记已发布到朋友圈。')
            except Exception:pass
            time.sleep(.5)
        return SendResult(False,'微信已执行发表，结果尚未确认，请核对朋友圈；不会自动重复发表。','uncertain')
    except Exception as exc:
        return SendResult(False,'日记发布未完成：'+type(exc).__name__+'；草稿已保留。','uncertain' if attempted else '')
    finally:
        # Leave an unsubmitted editor intact for review; otherwise restore the
        # chat page so ordinary sends do not inherit the Moments surface.
        try:
            if dialog is not None and not getattr(dialog,'IsOffscreen',True):
                cancel=find(dialog,lambda c:c.AutomationId=='2' and c.ControlTypeName=='ButtonControl')
                if cancel is not None:uia._click_ctrl(cancel)
            pending,_=composer(main)
            if pending is None:adapter._prepare_chat_window(uia)
        except Exception:pass
