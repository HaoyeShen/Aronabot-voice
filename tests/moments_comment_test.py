"""Exact post matching, no screenshots, draft preservation, one commit."""
import sys
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.moments_publish_test import Node
from app.moments_comment import submit, time_matches


def fixture():
    main=Node('微信');main.NativeWindowHandle=123;main.ProcessId=123
    Node('发现','mmui::XTabBarItem','ButtonControl',parent=main)
    Node('朋友圈','mmui::ExtensionDiscoverContentCell',parent=main)
    root=Node('朋友圈',parent=main)
    cell=Node('动态',parent=root);more=Node('更多',kind='ButtonControl',parent=cell)
    Node('评论',kind='ButtonControl',parent=root)
    panel=Node('评论编辑',parent=root)
    edit=Node('评论',kind='EditControl',parent=panel)
    button=Node('发送',kind='ButtonControl',parent=panel)
    post=dict(id='1',tid='1',username='friend',nickname='朋友',text='今天完成了一件期待很久的事情。',create_time=time.time(),comments=[])
    rows=[deepcopy(post)]
    db=SimpleNamespace(get_moments=lambda **_:deepcopy(rows))
    item=SimpleNamespace(publisher='朋友',text=post['text'],timestamp='刚刚',control=cell)
    moment=SimpleNamespace(_api=root,_read_visible_items=Mock(return_value=[item]),Comment=Mock(side_effect=AssertionError('screenshot path forbidden')))
    clicks=[]
    def click(node):
        clicks.append(node)
        if node is button:rows[0]['comments'].append({'username':'self','content':'进展很棒呀。'})
        return True
    adapter=SimpleNamespace(_uia=SimpleNamespace(_win=main,_click_ctrl=click),_db=object(),self_wxid='self',_prepare_chat_window=Mock())
    return adapter,post,rows,db,moment,edit,button,clicks


def main():
    assert time_matches(time.time()-65,'1分钟前')
    assert not time_matches(time.time()-7200,'刚刚')
    assert not time_matches(time.time(),'2025年11月5日')
    for case in ('success','cancelled','ambiguous','already','draft','unsupported','wrong_text','dry_run'):
        adapter,post,rows,db,moment,edit,button,clicks=fixture()
        if case=='ambiguous':rows.append(dict(post,id='2'))
        if case=='already':rows[0]['comments']=[{'username':'self','content':'旧评论'}]
        if case=='draft':edit.value='用户尚未发送的评论'
        if case=='unsupported':button.Name='未知控件'
        if case=='wrong_text':moment._read_visible_items.return_value[0].text='同一作者的另一条动态'
        with patch('wechatauto.moment.MomentDB',return_value=db),patch('wechatauto.moment.Moment',return_value=moment):
            result=submit(adapter,post,'进展很棒呀。',dry_run=case=='dry_run',allowed=lambda:case!='cancelled')
        if case=='success':
            assert result.ok and clicks.count(button)==1
        else:
            assert clicks.count(button)==0 and result.ok==(case=='dry_run'), (case,result)
        if case=='draft':assert edit.value=='用户尚未发送的评论'
        if case in ('cancelled','unsupported'):assert edit.value==''
        assert not moment.Comment.called
    print('MOMENTS COMMENT TESTS PASSED')


if __name__=='__main__':main()
