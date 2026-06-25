"""共享 fixtures 和测试工具"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest

# 确保 scraper 模块可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper  # noqa: E402


@pytest.fixture
def temp_dir():
    """创建临时目录，测试后自动清理"""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_page():
    """返回一个模拟的 Playwright Page 对象"""
    page = MagicMock()
    # 默认返回值
    page.query_selector_all.return_value = []
    page.query_selector.return_value = None   # 无登录弹窗等
    page.url = "https://www.xiaohongshu.com/search_result?keyword=test"
    return page


@pytest.fixture
def sample_notes():
    """返回一组模拟笔记数据"""
    return [
        {
            "title": "超美的黄金手镯分享✨",
            "image_url": "https://sns-img.xhs.com/img_001.jpg",
            "likes": 15200,
            "link": "https://www.xiaohongshu.com/explore/abc123",
            "note_url": "https://www.xiaohongshu.com/search_result/abc123?xsec_token=test",
        },
        {
            "title": "金手镯选购攻略",
            "image_url": "https://sns-img.xhs.com/img_002.png",
            "likes": 8300,
            "link": "https://www.xiaohongshu.com/explore/def456",
            "note_url": "https://www.xiaohongshu.com/search_result/def456?xsec_token=test",
        },
        {
            "title": "周大福金镯子开箱",
            "image_url": "https://sns-img.xhs.com/img_003.webp",
            "likes": 500,
            "link": "https://www.xiaohongshu.com/explore/ghi789",
            "note_url": "",
        },
    ]


@pytest.fixture
def mock_response():
    """模拟成功的 HTTP 响应（>10KB 以通过最低文件大小过滤）"""
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"\xff\xd8\xff\xe0" + b"\x00" * 11264  # ~11KB 假 JPEG
    return resp
