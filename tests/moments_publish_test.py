"""No real WeChat: owned composer, exact text and single irreversible commit."""
import sys,tempfile,itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.moments_publish import publish


class Node:
    def __init__(self,name='',cls='',kind='GroupControl',ident='',parent=None,visible=True):
        self.Name=name;self.ClassName=cls;self.ControlTypeName=kind;self.AutomationId=ident
        self.parent=parent;self.children=[];self.visible=visible;self.value=''
        if parent:parent.children.append(self)
    @property
    def IsOffscreen(self):return not self.visible or bool(self.parent and self.parent.IsOffscreen)
    def GetChildren(self):return self.children
    def GetParentControl(self):return self.parent
    def GetValuePattern(self):
        node=self
        class Pattern:
            @property
            def Value(self):return node.value
            def SetValue(self,value):node.value=value
        return Pattern()


def fixture():
    main=Node(cls='mmui::MainWindow');Node('发现','mmui::XTabBarItem','ButtonControl',parent=main)
    Node('发表','mmui::XTabBarItem','ButtonControl',parent=main)
    dialog=Node('选择文件','#32770','WindowControl',parent=main,visible=False)
    path=Node(kind='EditControl',ident='1148',parent=dialog)
    Node('打开','Button','ButtonControl','1',dialog)
    compose=Node(cls='Composer',parent=main,visible=False)
    editor=Node(cls='mmui::XValidatorTextEdit',kind='EditControl',parent=compose)
    Node('取消','mmui::XOutlineButton','ButtonControl',parent=compose)
    Node('发表','mmui::XOutlineButton','ButtonControl',parent=compose)
    state=SimpleNamespace(commits=0,confirmed=True,truncated=False)
    def click(c,**kwargs):
        if c.ClassName=='mmui::XTabBarItem' and c.Name=='发表':dialog.visible=True
        elif c.AutomationId=='1':dialog.visible=False;compose.visible=True;editor.value=''
        elif c.Name=='取消':compose.visible=False
        elif c.ClassName=='mmui::XOutlineButton' and c.Name=='发表':state.commits+=1;compose.visible=False
        return True
    uia=SimpleNamespace(_win=main,_click_ctrl=click)
    adapter=SimpleNamespace(_uia=uia,_db=object(),self_wxid='self',_prepare_chat_window=lambda u:None)
    db=SimpleNamespace(get_moments=lambda **kw:[{'text':'日记正文'}] if state.commits and state.confirmed else [])
    return adapter,state,compose,editor,path,db


def main():
    with tempfile.TemporaryDirectory() as folder:
        image=Path(folder)/'image.png';image.write_bytes(b'fixture')
        adapter,state,compose,editor,path,db=fixture()
        with patch('wechatauto.moment.MomentDB',return_value=db),patch('app.moments_publish.time.sleep'):
            assert publish(adapter,'日记正文',image,dry_run=True).ok
            assert state.commits==0 and path.value==str(image.resolve()) and not compose.visible
            assert publish(adapter,'日记正文',image).ok and state.commits==1
            assert publish(adapter,'日记正文',image).ok and state.commits==1,'confirmed duplicate must not be posted'
        adapter,state,compose,editor,path,db=fixture();compose.visible=True;editor.value='用户未发表的草稿'
        with patch('wechatauto.moment.MomentDB',return_value=db):
            assert not publish(adapter,'日记正文',image).ok and editor.value=='用户未发表的草稿' and state.commits==0
        adapter,state,compose,editor,path,db=fixture();state.confirmed=False
        clock=itertools.count()
        with patch('wechatauto.moment.MomentDB',return_value=db),patch('app.moments_publish.time.sleep'), \
             patch('app.moments_publish.time.monotonic',side_effect=lambda:next(clock)):
            result=publish(adapter,'日记正文',image)
            assert not result.ok and result.detail=='uncertain' and state.commits==1
    print('MOMENTS PUBLISH TEST PASSED')


if __name__=='__main__':main()
