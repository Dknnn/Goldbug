"""scraper.py 单元测试"""

import os
import re
import json
import tempfile
import hashlib
from unittest.mock import MagicMock, patch, call
import pytest
import requests

import scraper
from scraper import (
    _validate_image_url,
    _sanitize_filename,
    _parse_likes,
    _resolve_url,
    _save_state,
    _get_storage_state,
    _detect_login_wall,
    setup_image_dir,
    download_image,
    scroll_page,
    extract_notes,
    extract_note_images,
    scrape_note_all_images,
    scrape_keyword,
    main,
)


# ═══════════════════════════════════════════════════════════════
# _validate_image_url
# ═══════════════════════════════════════════════════════════════

class TestValidateImageUrl:
    def test_valid_https(self):
        assert _validate_image_url("https://example.com/img.jpg") is True

    def test_valid_http(self):
        assert _validate_image_url("http://example.com/img.jpg") is True

    def test_protocol_relative_considered_valid(self):
        """协议相对 URL 在被 _resolve_url 补全前进来，允许通过"""
        assert _validate_image_url("//example.com/img.jpg") is True

    def test_empty_string(self):
        assert _validate_image_url("") is False

    def test_data_uri(self):
        assert _validate_image_url("data:image/png;base64,abc123") is False

    def test_javascript_uri(self):
        assert _validate_image_url("javascript:void(0)") is False

    def test_relative_path(self):
        """纯路径（无协议）也应允许"""
        assert _validate_image_url("/images/photo.jpg") is True


# ═══════════════════════════════════════════════════════════════
# _sanitize_filename
# ═══════════════════════════════════════════════════════════════

class TestSanitizeFilename:
    def test_normal_chinese(self):
        assert _sanitize_filename("超美的黄金手镯分享") == "超美的黄金手镯分享"

    def test_removes_illegal_chars(self):
        result = _sanitize_filename('a*b:c<d>e"f|g?h/i\\j')
        assert result == "abcdefghij"

    def test_all_illegal_chars_returns_note(self):
        """全部为非法字符时，返回兜底值 'note'"""
        result = _sanitize_filename('*?:<>|\\/')
        assert result == "note"

    def test_empty_string_returns_note(self):
        assert _sanitize_filename("") == "note"

    def test_whitespace_only_returns_note(self):
        assert _sanitize_filename("   ") == "note"

    def test_truncates_long_text(self):
        result = _sanitize_filename("a" * 100)
        assert len(result) == 20

    def test_truncates_with_default_max_len(self):
        result = _sanitize_filename("a" * 50)
        assert len(result) <= 20

    def test_strips_whitespace_after_truncation(self):
        """截断后去除首尾空格"""
        result = _sanitize_filename("   hello world   ")
        assert result == "hello world"


# ═══════════════════════════════════════════════════════════════
# _parse_likes
# ═══════════════════════════════════════════════════════════════

class TestParseLikes:
    def test_plain_number(self):
        assert _parse_likes("12345") == 12345

    def test_number_with_wan(self):
        assert _parse_likes("1.2万") == 12000

    def test_integer_wan(self):
        assert _parse_likes("2万") == 20000

    def test_large_wan(self):
        assert _parse_likes("100.5万") == 1005000

    def test_empty_string(self):
        assert _parse_likes("") == 0

    def test_text_without_digits(self):
        assert _parse_likes("点赞") == 0

    def test_number_with_text(self):
        """应提取数字部分"""
        assert _parse_likes("123赞") == 123

    def test_wan_with_extra_spaces(self):
        assert _parse_likes(" 1.5万 ") == 15000


# ═══════════════════════════════════════════════════════════════
# _resolve_url
# ═══════════════════════════════════════════════════════════════

class TestResolveUrl:
    def test_full_url_unchanged(self):
        url = "https://www.xiaohongshu.com/explore/abc"
        assert _resolve_url(url) == url

    def test_root_relative_path(self):
        result = _resolve_url("/explore/abc123")
        assert result == "https://www.xiaohongshu.com/explore/abc123"

    def test_protocol_relative(self):
        result = _resolve_url("//sns-img.xhs.com/img.jpg")
        assert result == "https://sns-img.xhs.com/img.jpg"

    def test_empty_string(self):
        assert _resolve_url("") == ""

    def test_http_url(self):
        assert _resolve_url("http://example.com/img.jpg") == "http://example.com/img.jpg"


# ═══════════════════════════════════════════════════════════════
# setup_image_dir
# ═══════════════════════════════════════════════════════════════

class TestSetupImageDir:
    def test_creates_directory(self, temp_dir):
        with patch.object(scraper.config, "IMAGE_DIR", temp_dir):
            with patch.object(scraper.config, "ARCHIVE_BY_DATE", False):
                result = setup_image_dir()
                assert result == temp_dir
                assert os.path.isdir(result)

    def test_archive_by_date_creates_subdir(self, temp_dir):
        fake_today = "2026-06-25"
        with patch.object(scraper.config, "IMAGE_DIR", temp_dir):
            with patch.object(scraper.config, "ARCHIVE_BY_DATE", True):
                with patch.object(scraper, "datetime") as mock_dt:
                    mock_dt.now.return_value.strftime.return_value = fake_today
                    result = setup_image_dir()
                    expected = os.path.join(temp_dir, fake_today)
                    assert result == expected

    def test_already_exists_no_error(self, temp_dir):
        """目录已存在时不抛异常"""
        os.makedirs(os.path.join(temp_dir, "existing"), exist_ok=True)
        with patch.object(scraper.config, "IMAGE_DIR", temp_dir):
            with patch.object(scraper.config, "ARCHIVE_BY_DATE", True):
                with patch.object(scraper, "datetime") as mock_dt:
                    mock_dt.now.return_value.strftime.return_value = "existing"
                    result = setup_image_dir()
                    assert os.path.isdir(result)


# ═══════════════════════════════════════════════════════════════
# download_image
# ═══════════════════════════════════════════════════════════════

class TestDownloadImage:
    def test_successful_download(self, temp_dir, mock_response):
        with patch("scraper.requests.get", return_value=mock_response) as mock_get:
            result = download_image(
                "https://example.com/photo.jpg", temp_dir, "test.jpg"
            )
            assert result == os.path.join(temp_dir, "test.jpg")
            assert os.path.isfile(result)
            mock_get.assert_called_once()

    def test_skip_existing_file(self, temp_dir):
        """已存在的文件直接跳过"""
        filepath = os.path.join(temp_dir, "exists.jpg")
        with open(filepath, "wb") as f:
            f.write(b"existing")

        with patch("scraper.requests.get") as mock_get:
            result = download_image(
                "https://example.com/photo.jpg", temp_dir, "exists.jpg"
            )
            assert result == filepath
            mock_get.assert_not_called()

    def test_invalid_url_returns_none(self, temp_dir):
        result = download_image("", temp_dir)
        assert result is None

    def test_data_uri_returns_none(self, temp_dir):
        result = download_image("data:image/png;base64,abc", temp_dir)
        assert result is None

    def test_auto_generates_filename(self, temp_dir, mock_response):
        url = "https://example.com/photo.png"
        with patch("scraper.requests.get", return_value=mock_response):
            result = download_image(url, temp_dir)
            assert result is not None
            assert result.endswith(".png")
            assert os.path.isfile(result)

    def test_auto_generates_webp_filename(self, temp_dir, mock_response):
        url = "https://example.com/photo.WEBP"
        with patch("scraper.requests.get", return_value=mock_response):
            result = download_image(url, temp_dir)
            assert result is not None
            assert result.endswith(".webp")

    def test_http_404_no_retry(self, temp_dir):
        """404 错误不重试"""
        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

        with patch("scraper.requests.get", return_value=mock_404) as mock_get:
            with patch("scraper.time.sleep") as mock_sleep:
                result = download_image(
                    "https://example.com/missing.jpg", temp_dir
                )
                assert result is None
                # 404 只请求一次
                assert mock_get.call_count == 1
                mock_sleep.assert_not_called()

    def test_connection_error_retries(self, temp_dir):
        """连接错误应重试 3 次"""
        with patch(
            "scraper.requests.get",
            side_effect=requests.ConnectionError("Connection refused"),
        ) as mock_get:
            with patch("scraper.time.sleep") as mock_sleep:
                result = download_image(
                    "https://example.com/photo.jpg", temp_dir
                )
                assert result is None
                assert mock_get.call_count == 3
                assert mock_sleep.call_count >= 2  # 第1次重试前和第2次重试前各sleep一次

    def test_success_on_second_retry(self, temp_dir, mock_response):
        """第2次重试成功"""
        mock_get = MagicMock()
        mock_get.side_effect = [
            requests.ConnectionError("fail"),
            mock_response,
        ]
        with patch("scraper.requests.get", mock_get):
            with patch("scraper.time.sleep"):
                result = download_image(
                    "https://example.com/photo.jpg", temp_dir, "retry_ok.jpg"
                )
                assert result is not None
                assert mock_get.call_count == 2

    def test_timeout_retries(self, temp_dir):
        """请求超时应重试"""
        with patch(
            "scraper.requests.get",
            side_effect=requests.Timeout("Read timed out"),
        ) as mock_get:
            with patch("scraper.time.sleep"):
                result = download_image(
                    "https://example.com/photo.jpg", temp_dir, "timeout_test.jpg"
                )
                assert result is None
                assert mock_get.call_count == 3

    def test_http_403_no_retry(self, temp_dir):
        """403 禁止访问不重试"""
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with patch("scraper.requests.get", return_value=mock_403) as mock_get:
            with patch("scraper.time.sleep") as mock_sleep:
                result = download_image(
                    "https://example.com/forbidden.jpg", temp_dir
                )
                assert result is None
                assert mock_get.call_count == 1
                mock_sleep.assert_not_called()

    def test_oserror_no_retry(self, temp_dir):
        """文件写入失败不重试（不可恢复）"""
        with patch("scraper.requests.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.content = b"data"
            with patch("builtins.open", side_effect=OSError("Permission denied")):
                result = download_image(
                    "https://example.com/photo.jpg", temp_dir, "noperm.jpg"
                )
                assert result is None
                assert mock_get.call_count == 1


# ═══════════════════════════════════════════════════════════════
# scroll_page
# ═══════════════════════════════════════════════════════════════

class TestScrollPage:
    def test_scrolls_correct_number_of_times(self, mock_page):
        with patch("scraper.time.sleep"):
            with patch.object(scraper.config, "REQUEST_DELAY", 0):
                scroll_page(mock_page, times=5)
                assert mock_page.evaluate.call_count == 5
                mock_page.evaluate.assert_called_with(
                    "window.scrollBy(0, window.innerHeight)"
                )

    def test_default_scroll_times(self, mock_page):
        with patch("scraper.time.sleep"):
            with patch.object(scraper.config, "REQUEST_DELAY", 0):
                scroll_page(mock_page)
                assert mock_page.evaluate.call_count == 3


# ═══════════════════════════════════════════════════════════════
# extract_notes
# ═══════════════════════════════════════════════════════════════

class TestExtractNotes:
    def test_no_cards_returns_empty(self, mock_page):
        mock_page.query_selector_all.return_value = []
        # 模拟超时
        mock_page.wait_for_selector.side_effect = scraper.PlaywrightTimeout("timeout")
        result = extract_notes(mock_page)
        assert result == []

    def test_extracts_note_info(self, mock_page):
        """从模拟卡片中提取笔记信息"""
        # 创建模拟元素
        img_el = MagicMock()
        img_el.get_attribute.side_effect = lambda attr: {
            "src": "https://img.xhs.com/photo.jpg?x-oss-process=xxx",
        }.get(attr, "")

        title_el = MagicMock()
        title_el.inner_text.return_value = "  超美黄金手镯  "

        like_el = MagicMock()
        like_el.inner_text.return_value = "1.5万"

        a_el = MagicMock()
        a_el.get_attribute.return_value = "/explore/test123"

        card = MagicMock()
        card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
        }.get(sel)

        mock_page.query_selector_all.return_value = [card]
        mock_page.wait_for_selector.return_value = None

        result = extract_notes(mock_page)
        assert len(result) == 1
        note = result[0]
        assert "超美黄金手镯" in note["title"]
        assert note["likes"] == 15000
        assert note["link"] == "https://www.xiaohongshu.com/explore/test123"
        # URL 应该去掉了查询参数
        assert "?" not in note["image_url"]

    def test_skips_cards_without_image(self, mock_page):
        """没有图片的卡片直接跳过"""
        card = MagicMock()
        card.query_selector.return_value = None  # 无 img 元素

        mock_page.query_selector_all.return_value = [card]
        mock_page.wait_for_selector.return_value = None

        result = extract_notes(mock_page)
        assert result == []

    def test_card_parse_error_continues(self, mock_page):
        """单张卡片解析失败不影响后续卡片"""
        bad_card = MagicMock()
        bad_card.query_selector.side_effect = RuntimeError("DOM 已变化")

        img_el = MagicMock()
        img_el.get_attribute.side_effect = lambda attr: {"src": "https://ok.jpg"}.get(attr, "")
        title_el = MagicMock()
        title_el.inner_text.return_value = "正常标题"
        like_el = MagicMock()
        like_el.inner_text.return_value = "100"
        a_el = MagicMock()
        a_el.get_attribute.return_value = "/explore/good"

        good_card = MagicMock()
        good_card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
        }.get(sel)

        mock_page.query_selector_all.return_value = [bad_card, good_card]
        mock_page.wait_for_selector.return_value = None

        result = extract_notes(mock_page)
        assert len(result) == 1
        assert result[0]["title"] == "正常标题"

    def test_fallback_to_alternate_selector(self, mock_page):
        """主选择器为空时使用备用选择器"""
        # 模拟主选择器返回空
        cards = mock_page.query_selector_all.side_effect = [
            [],  # 第一次调用（note_card）返回空
            [MagicMock()],  # 第二次调用（note_card_fallback）返回一条
        ]
        # 让 fallback 的卡片也没有图片，最终返回空
        mock_page.query_selector_all.side_effect = [
            [],
            [],  # fallback 也空
        ]
        mock_page.wait_for_selector.return_value = None
        result = extract_notes(mock_page)
        assert result == []

    def test_wan_likes_format(self, mock_page):
        """测试「1.2 万」格式点赞数解析（_parse_likes 已在独立测试中覆盖）"""
        # 这个测试验证 extract_notes 内部调用了 _parse_likes
        img_el = MagicMock()
        img_el.get_attribute.return_value = "https://img.jpg"

        title_el = MagicMock()
        title_el.inner_text.return_value = "测试"

        like_el = MagicMock()
        like_el.inner_text.return_value = "3.6万"

        a_el = MagicMock()
        a_el.get_attribute.return_value = "/explore/popular"

        card = MagicMock()
        card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
        }.get(sel)

        mock_page.query_selector_all.return_value = [card]
        mock_page.wait_for_selector.return_value = None

        result = extract_notes(mock_page)
        assert result[0]["likes"] == 36000


# ═══════════════════════════════════════════════════════════════
# scrape_keyword (集成测试)
# ═══════════════════════════════════════════════════════════════

class TestScrapeKeyword:
    def test_empty_results(self, mock_page, temp_dir):
        """搜索无结果时返回空列表"""
        mock_page.wait_for_selector.side_effect = scraper.PlaywrightTimeout("timeout")
        mock_page.query_selector_all.return_value = []

        with patch("scraper.time.sleep"):
            with patch.object(scraper.config, "SCROLL_TIMES", 1):
                with patch.object(scraper.config, "REQUEST_DELAY", 0):
                    result = scrape_keyword(mock_page, "不存在的关键词12345", temp_dir)
                    assert result == []

    def test_scrapes_and_downloads(self, mock_page, temp_dir, mock_response):
        """完整流程：搜索 → 提取 → 排序 → 下载"""
        # 构造模拟卡片
        img_el = MagicMock()
        img_el.get_attribute.side_effect = lambda attr: {
            "src": "https://img.xhs.com/gold.jpg",
        }.get(attr, "")

        title_el = MagicMock()
        title_el.inner_text.return_value = "黄金手镯推荐"

        like_el = MagicMock()
        like_el.inner_text.return_value = "5000"

        a_el = MagicMock()
        a_el.get_attribute.return_value = "/explore/gold123"

        card = MagicMock()
        card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
        }.get(sel)

        mock_page.query_selector_all.return_value = [card]
        mock_page.wait_for_selector.return_value = None

        with patch("scraper.requests.get", return_value=mock_response):
            with patch("scraper.time.sleep"):
                with patch.object(scraper.config, "SCROLL_TIMES", 1):
                    with patch.object(scraper.config, "REQUEST_DELAY", 0):
                        with patch.object(scraper.config, "TOP_N", 1):
                            result = scrape_keyword(mock_page, "金手镯", temp_dir)
                            assert len(result) == 1
                            assert result[0].endswith(".jpg")
                            assert os.path.isfile(result[0])

    def test_sorts_by_likes_descending(self, mock_page, temp_dir, mock_response):
        """验证按点赞数降序排列，取 Top N"""
        cards = []
        likes_values = [100, 5000, 200, 9999, 50]
        for i, likes in enumerate(likes_values):
            img_el = MagicMock()
            img_el.get_attribute.side_effect = lambda attr, lk=likes: {
                "src": f"https://img.xhs.com/img_{lk}.jpg",
            }.get(attr, "")

            title_el = MagicMock()
            title_el.inner_text.return_value = f"笔记{i}"
            like_el = MagicMock()
            like_el.inner_text.return_value = str(likes)
            a_el = MagicMock()
            a_el.get_attribute.return_value = f"/explore/{i}"

            card = MagicMock()
            card.query_selector.side_effect = lambda sel, t=title_el, l=like_el, a=a_el, im=img_el: {
                "img": im,
                '[class*="title"]': t,
                '[class*="like"]': l,
                "a": a,
            }.get(sel)
            cards.append(card)

        mock_page.query_selector_all.return_value = cards
        mock_page.wait_for_selector.return_value = None

        with patch("scraper.requests.get", return_value=mock_response):
            with patch("scraper.time.sleep"):
                with patch.object(scraper.config, "SCROLL_TIMES", 1):
                    with patch.object(scraper.config, "REQUEST_DELAY", 0):
                        with patch.object(scraper.config, "TOP_N", 3):
                            result = scrape_keyword(mock_page, "金手镯", temp_dir)
                            assert len(result) == 3
                            filenames = [os.path.basename(r) for r in result]
                            assert "笔记3" in filenames[0]  # 9999赞
                            assert "笔记1" in filenames[1]  # 5000赞
                            assert "笔记2" in filenames[2]  # 200赞

    def test_page_load_timeout_returns_empty(self, mock_page, temp_dir):
        """页面加载超时时返回空列表"""
        mock_page.goto.side_effect = scraper.PlaywrightTimeout("timeout")

        with patch("scraper.time.sleep"):
            result = scrape_keyword(mock_page, "金手镯", temp_dir)
            assert result == []


# ═══════════════════════════════════════════════════════════════
# 覆盖率补充：config 模块
# ═══════════════════════════════════════════════════════════════

class TestConfig:
    def test_config_has_required_fields(self):
        """验证 config.py 包含所有必要配置项"""
        import config
        assert hasattr(config, "KEYWORDS")
        assert hasattr(config, "TOP_N")
        assert hasattr(config, "IMAGE_DIR")
        assert hasattr(config, "ARCHIVE_BY_DATE")
        assert hasattr(config, "HEADLESS")
        assert hasattr(config, "SLOW_MO")
        assert hasattr(config, "SCROLL_TIMES")
        assert hasattr(config, "REQUEST_DELAY")
        assert hasattr(config, "STATE_FILE")
        assert hasattr(config, "DOWNLOAD_MODE")
        assert hasattr(config, "NOTE_SCROLL_TIMES")
        assert isinstance(config.KEYWORDS, list)
        assert len(config.KEYWORDS) > 0
        assert config.TOP_N > 0


# ═══════════════════════════════════════════════════════════════
# 登录态持久化
# ═══════════════════════════════════════════════════════════════

class TestStatePersistence:
    def test_save_state(self, temp_dir):
        """保存浏览器状态"""
        state_file = os.path.join(temp_dir, "test_state.json")
        mock_context = MagicMock()
        mock_context.cookies.return_value = [
            {"name": "a1", "value": "session_123", "domain": ".xiaohongshu.com"},
        ]
        _save_state(mock_context, path=state_file)
        # storage_state 被调用了
        mock_context.storage_state.assert_called_once_with(path=state_file)

    def test_get_storage_state_exists(self, temp_dir):
        """文件存在时返回路径"""
        state_file = os.path.join(temp_dir, "exists.json")
        with open(state_file, "w") as f:
            f.write("{}")
        result = _get_storage_state(path=state_file)
        assert result == state_file

    def test_get_storage_state_not_found(self):
        """文件不存在时返回 None"""
        result = _get_storage_state(path="/nonexistent/state.json")
        assert result is None


class TestDetectLoginWall:
    def test_detects_login_wall(self, mock_page):
        mock_page.url = "https://www.xiaohongshu.com/search_result?keyword=test"
        # 模拟登录弹窗存在
        mock_modal = MagicMock()
        mock_modal.is_visible.return_value = True
        mock_page.query_selector.return_value = mock_modal
        mock_page.inner_text.return_value = "登录后查看搜索结果\n小红书\n登录"
        assert _detect_login_wall(mock_page) is True

    def test_no_login_wall(self, mock_page):
        mock_page.inner_text.return_value = "金手镯 搜索结果\n5000赞\n黄金手镯推荐"
        assert _detect_login_wall(mock_page) is False

    def test_inner_text_exception(self, mock_page):
        mock_page.inner_text.side_effect = RuntimeError("DOM error")
        assert _detect_login_wall(mock_page) is False

    def test_login_url_detected(self, mock_page):
        """URL 含 /login 时直接判定为登录墙"""
        mock_page.url = "https://www.xiaohongshu.com/login"
        assert _detect_login_wall(mock_page) is True


# ═══════════════════════════════════════════════════════════════
# scrape_keyword 登录墙检测
# ═══════════════════════════════════════════════════════════════

class TestScrapeKeywordLoginWall:
    def test_returns_empty_when_login_required(self, mock_page, temp_dir):
        """检测到登录墙时返回空列表并提示"""
        mock_page.url = "https://www.xiaohongshu.com/search_result?keyword=test"
        mock_page.inner_text.return_value = "登录后查看搜索结果"
        with patch("scraper.time.sleep"):
            with patch.object(scraper.config, "SCROLL_TIMES", 1):
                with patch.object(scraper.config, "REQUEST_DELAY", 0):
                    result = scrape_keyword(mock_page, "金手镯", temp_dir)
                    assert result == []


# ═══════════════════════════════════════════════════════════════
# main() 入口
# ═══════════════════════════════════════════════════════════════

class TestMain:
    def test_main_runs_scrape(self):
        """不带参数时运行抓取"""
        with patch.object(scraper, "run", return_value=["/path/img.jpg"]) as mock_run:
            with patch.object(scraper.sys, "argv", ["scraper.py"]):
                main()
                mock_run.assert_called_once()

    def test_main_login_flag(self):
        """--login 参数触发登录流程"""
        with patch.object(scraper, "do_login", return_value=True) as mock_login:
            with patch.object(scraper.sys, "argv", ["scraper.py", "--login"]):
                with patch.object(scraper.sys, "exit") as mock_exit:
                    main()
                    mock_login.assert_called_once()
                    mock_exit.assert_called_once_with(0)

    def test_main_login_failure_exit_code(self):
        """登录失败时退出码为 1"""
        with patch.object(scraper, "do_login", return_value=False) as mock_login:
            with patch.object(scraper.sys, "argv", ["scraper.py", "--login"]):
                with patch.object(scraper.sys, "exit") as mock_exit:
                    main()
                    mock_exit.assert_called_once_with(1)


# ═══════════════════════════════════════════════════════════════
# run() 全流程测试
# ═══════════════════════════════════════════════════════════════

class TestRun:
    def test_run_success(self, temp_dir):
        """完整 run() 流程：启动浏览器 → 搜索 → 下载"""
        mock_page = MagicMock()
        mock_page.url = "https://www.xiaohongshu.com/search_result?keyword=xxx"
        mock_page.query_selector.return_value = None
        mock_page.wait_for_selector.return_value = None

        # 构造一条笔记卡片
        img_el = MagicMock()
        img_el.get_attribute.side_effect = lambda attr: {
            "src": "https://img.xhs.com/test.jpg",
        }.get(attr, "")
        title_el = MagicMock()
        title_el.inner_text.return_value = "黄金测试"
        like_el = MagicMock()
        like_el.inner_text.return_value = "100"
        a_el = MagicMock()
        a_el.get_attribute.return_value = "/explore/test"

        card = MagicMock()
        card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
        }.get(sel)

        mock_page.query_selector_all.return_value = [card]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        with patch("scraper.sync_playwright") as mock_pw:
            mock_pw_instance = MagicMock()
            mock_pw.return_value.start.return_value = mock_pw_instance
            mock_pw_instance.chromium.launch.return_value = mock_browser

            with patch.object(scraper.config, "KEYWORDS", ["金手镯"]):
                with patch.object(scraper.config, "TOP_N", 1):
                    with patch.object(scraper.config, "IMAGE_DIR", temp_dir):
                        with patch.object(scraper.config, "ARCHIVE_BY_DATE", False):
                            with patch.object(scraper.config, "REQUEST_DELAY", 0):
                                with patch.object(scraper.config, "SCROLL_TIMES", 1):
                                    with patch("scraper.requests.get") as mock_req:
                                        mock_resp = MagicMock()
                                        mock_resp.status_code = 200
                                        mock_resp.content = b"fake_image"
                                        mock_resp.raise_for_status.return_value = None
                                        mock_req.return_value = mock_resp

                                        with patch("scraper.time.sleep"):
                                            result = scraper.run()

        assert len(result) == 1
        assert os.path.isfile(result[0])

    def test_run_browser_launch_failure(self, temp_dir):
        """浏览器启动失败时不崩溃，返回空列表"""
        with patch("scraper.sync_playwright", side_effect=RuntimeError("Chrome not found")):
            with patch.object(scraper.config, "IMAGE_DIR", temp_dir):
                with patch.object(scraper.config, "ARCHIVE_BY_DATE", False):
                    result = scraper.run()
                    assert result == []

    def test_run_keyword_scrape_exception_continues(self, temp_dir):
        """单个关键词抓取异常不影响后续关键词（但继续执行完）"""
        mock_page = MagicMock()
        mock_page.wait_for_selector.return_value = None

        # 第一页正常，切换关键词时 goto 抛异常
        img_el = MagicMock()
        img_el.get_attribute.side_effect = lambda attr: {"src": "https://img.jpg"}.get(attr, "")
        title_el = MagicMock()
        title_el.inner_text.return_value = "标题"
        like_el = MagicMock()
        like_el.inner_text.return_value = "100"
        a_el = MagicMock()
        a_el.get_attribute.return_value = "/e/1"

        card = MagicMock()
        card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
        }.get(sel)

        mock_page.url = "https://www.xiaohongshu.com/search_result?keyword=xxx"
        mock_page.query_selector.return_value = None
        mock_page.query_selector_all.return_value = [card]

        # ensure_login(1) + 关键词1 首页+搜索(2) + 关键词2 首页失败(1)
        mock_page.goto.side_effect = [
            None,                                   # ensure_login
            None, None,                             # 关键词1: 首页 + 搜索
            scraper.PlaywrightTimeout("timeout"),   # 关键词2: 首页失败
        ]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        with patch("scraper.sync_playwright") as mock_pw:
            mock_pw_instance = MagicMock()
            mock_pw.return_value.start.return_value = mock_pw_instance
            mock_pw_instance.chromium.launch.return_value = mock_browser

            with patch.object(scraper.config, "KEYWORDS", ["金手镯", "会失败的关键词"]):
                with patch.object(scraper.config, "TOP_N", 1):
                    with patch.object(scraper.config, "IMAGE_DIR", temp_dir):
                        with patch.object(scraper.config, "ARCHIVE_BY_DATE", False):
                            with patch.object(scraper.config, "REQUEST_DELAY", 0):
                                with patch.object(scraper.config, "SCROLL_TIMES", 1):
                                    with patch("scraper.requests.get") as mock_req:
                                        mock_resp = MagicMock()
                                        mock_resp.status_code = 200
                                        mock_resp.content = b"fake"
                                        mock_resp.raise_for_status.return_value = None
                                        mock_req.return_value = mock_resp

                                        with patch("scraper.time.sleep"):
                                            result = scraper.run()

        # 第一个关键词成功，第二个失败，总共 1 张图
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════
# extract_note_images（笔记详情页图片提取）
# ═══════════════════════════════════════════════════════════════

class TestExtractNoteImages:
    def test_extracts_content_images(self, mock_page):
        """从笔记详情页提取内容图片，过滤头像和图标"""
        imgs = []
        for i in range(5):
            img = MagicMock()
            img.get_attribute.side_effect = lambda attr, idx=i: {
                "src": f"https://img.xhs.com/note_photo_{idx}.jpg",
                "data-src": "",
                "class": "",
            }.get(attr, "")
            img.evaluate.return_value = "note-image-container"
            imgs.append(img)

        # 加一个头像
        avatar = MagicMock()
        avatar.get_attribute.side_effect = lambda attr: {
            "src": "https://avatar.xhs.com/head.jpg",
            "class": "author-avatar",
        }.get(attr, "")
        avatar.evaluate.return_value = "author-avatar-wrapper"
        imgs.append(avatar)

        # 加一个 data: URI（应被过滤）
        data_img = MagicMock()
        data_img.get_attribute.side_effect = lambda attr: {
            "src": "data:image/svg+xml;base64,xxx",
            "class": "",
        }.get(attr, "")
        data_img.evaluate.return_value = ""
        imgs.append(data_img)

        mock_page.query_selector_all.return_value = imgs

        with patch.object(scraper.config, "NOTE_SCROLL_TIMES", 1):
            with patch("scraper.time.sleep"):
                result = extract_note_images(mock_page)
                assert len(result) == 5  # 5 content + 1 filtered avatar + 1 filtered data URI
                assert all("note_photo" in r for r in result)

    def test_deduplicates_images(self, mock_page):
        """重复 URL 只保留一次"""
        imgs = []
        for _ in range(3):
            img = MagicMock()
            img.get_attribute.side_effect = lambda attr: {
                "src": "https://img.xhs.com/same_photo.jpg",
                "class": "",
            }.get(attr, "")
            img.evaluate.return_value = ""
            imgs.append(img)

        mock_page.query_selector_all.return_value = imgs

        with patch.object(scraper.config, "NOTE_SCROLL_TIMES", 0):
            with patch("scraper.time.sleep"):
                result = extract_note_images(mock_page)
                assert len(result) == 1

    def test_empty_page(self, mock_page):
        """没有图片时返回空列表"""
        mock_page.query_selector_all.return_value = []
        with patch.object(scraper.config, "NOTE_SCROLL_TIMES", 0):
            with patch("scraper.time.sleep"):
                result = extract_note_images(mock_page)
                assert result == []


# ═══════════════════════════════════════════════════════════════
# scrape_note_all_images（单笔记全部图片下载）
# ═══════════════════════════════════════════════════════════════

class TestScrapeNoteAllImages:
    def test_downloads_all_images(self, mock_page, temp_dir, mock_response):
        """进入笔记详情页，下载全部图片"""
        note = {
            "title": "测试笔记标题",
            "image_url": "https://img.xhs.com/cover.jpg",
            "note_url": "https://www.xiaohongshu.com/search_result/test123?xsec_token=abc",
        }

        # 模拟详情页图片
        imgs = []
        for i in range(3):
            img = MagicMock()
            img.get_attribute.side_effect = lambda attr, idx=i: {
                "src": f"https://img.xhs.com/photo_{idx}.jpg",
                "class": "",
            }.get(attr, "")
            img.evaluate.return_value = "note-image-container"
            imgs.append(img)

        mock_page.query_selector_all.return_value = imgs

        with patch("scraper.requests.get", return_value=mock_response):
            with patch.object(scraper.config, "NOTE_SCROLL_TIMES", 1):
                with patch("scraper.time.sleep"):
                    result = scrape_note_all_images(
                        mock_page, note, temp_dir, temp_dir
                    )
                    assert len(result) == 3
                    for path in result:
                        assert os.path.isfile(path)

    def test_no_note_url_falls_back_to_cover(self, mock_page, temp_dir, mock_response):
        """没有 note_url 时退回下载封面"""
        note = {
            "title": "无详情链接的笔记",
            "image_url": "https://img.xhs.com/cover_only.jpg",
            "note_url": "",
        }

        with patch("scraper.requests.get", return_value=mock_response):
            with patch("scraper.time.sleep"):
                result = scrape_note_all_images(
                    mock_page, note, temp_dir, temp_dir
                )
                assert len(result) == 1
                assert "cover_only" not in os.path.basename(result[0])
                # 文件名来自标题
                assert os.path.isfile(result[0])

    def test_page_timeout_fallback(self, mock_page, temp_dir):
        """笔记详情页超时时返回空"""
        note = {
            "title": "超时笔记",
            "image_url": "https://img.xhs.com/cover.jpg",
            "note_url": "https://www.xiaohongshu.com/search_result/timeout?xsec_token=bad",
        }
        mock_page.goto.side_effect = scraper.PlaywrightTimeout("timeout")

        with patch("scraper.time.sleep"):
            result = scrape_note_all_images(mock_page, note, temp_dir, temp_dir)
            assert result == []

    def test_no_content_images_falls_back(self, mock_page, temp_dir, mock_response):
        """详情页没有内容图时退回封面"""
        note = {
            "title": "空笔记",
            "image_url": "https://img.xhs.com/cover_backup.jpg",
            "note_url": "https://www.xiaohongshu.com/search_result/empty?xsec_token=bad",
        }
        mock_page.query_selector_all.return_value = []

        with patch("scraper.requests.get", return_value=mock_response):
            with patch.object(scraper.config, "NOTE_SCROLL_TIMES", 0):
                with patch("scraper.time.sleep"):
                    result = scrape_note_all_images(
                        mock_page, note, temp_dir, temp_dir
                    )
                    assert len(result) == 1
                    assert os.path.isfile(result[0])


# ═══════════════════════════════════════════════════════════════
# scrape_keyword "all" 模式
# ═══════════════════════════════════════════════════════════════

class TestScrapeKeywordAllMode:
    def test_all_mode_downloads_all_images_per_note(self, mock_page, temp_dir, mock_response):
        """all 模式：每篇笔记进入详情页，下载全部图片"""
        mock_page.url = "https://www.xiaohongshu.com/search_result?keyword=xxx"

        # 封面 mock
        cover_mock = MagicMock()
        cover_mock.get_attribute.side_effect = lambda attr: {
            "href": "/search_result/test123?xsec_token=abc",
        }.get(attr, "")

        # 卡片 mock
        img_el = MagicMock()
        img_el.get_attribute.side_effect = lambda attr: {
            "src": "https://img.xhs.com/cover.jpg",
        }.get(attr, "")
        title_el = MagicMock()
        title_el.inner_text.return_value = "测试笔记A"
        like_el = MagicMock()
        like_el.inner_text.return_value = "500"
        a_el = MagicMock()
        a_el.get_attribute.return_value = "/explore/test123"

        card = MagicMock()
        card.query_selector.side_effect = lambda sel: {
            "img": img_el,
            '[class*="title"]': title_el,
            '[class*="like"]': like_el,
            "a": a_el,
            "a.cover": cover_mock,
        }.get(sel)

        mock_page.query_selector_all.return_value = [card]
        mock_page.wait_for_selector.return_value = None

        # 模拟详情页的图片（与卡片 mock 共享 page.query_selector_all）
        # scrape_note_all_images 会再次调用 page.query_selector_all
        # 第一次是卡片提取，后面是详情页图片提取
        detail_imgs = []
        for i in range(2):
            img = MagicMock()
            img.get_attribute.side_effect = lambda attr, idx=i: {
                "src": f"https://img.xhs.com/detail_{idx}.jpg",
                "class": "",
            }.get(attr, "")
            img.evaluate.return_value = "note-image-container"
            detail_imgs.append(img)

        # query_selector_all 先返回卡片，再返回详情页图片
        mock_page.query_selector_all.side_effect = [
            [card],       # extract_notes: 卡片
            detail_imgs,  # extract_note_images: 详情页图片
        ]

        with patch("scraper.requests.get", return_value=mock_response):
            with patch("scraper.time.sleep"):
                with patch.object(scraper.config, "DOWNLOAD_MODE", "all"):
                    with patch.object(scraper.config, "SCROLL_TIMES", 1):
                        with patch.object(scraper.config, "REQUEST_DELAY", 0):
                            with patch.object(scraper.config, "TOP_N", 1):
                                with patch.object(scraper.config, "NOTE_SCROLL_TIMES", 0):
                                    result = scrape_keyword(
                                        mock_page, "金手镯", temp_dir
                                    )
                                    # 2 张详情图 + 封面兜底
                                    assert len(result) >= 2
                                    for path in result:
                                        assert os.path.isfile(path)
