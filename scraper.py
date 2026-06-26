# encoding: utf-8
"""
小红书爆款图片抓取工具
用途：自动搜索指定关键词，下载高点赞笔记的封面图

首次使用需要登录：
  python scraper.py --login      # 打开浏览器，手动扫码登录，登录后自动保存 cookie
正常抓取：
  python scraper.py               # 使用已保存的 cookie，后台静默抓取
"""

import json
import logging
import os
import re
import sys
import time
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote, urlparse

from PIL import Image
import requests
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

import config

# ── Windows 终端 Unicode 修复 ────────────────────────────
if sys.platform == "win32" and sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("goldbug")

# ── 常量 ─────────────────────────────────────────────────
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search_result_notes"
LOGIN_URL = "https://www.xiaohongshu.com/explore"
_downloaded_url_hashes: set[str] = set()  # 全局去重：已下载图片的 URL 哈希
SELECTORS = {
    "note_card": "section.note-item",
    "note_card_fallback": 'div[class*="note-item"]',
    "note_card_alt": '[class*="note"]',
    "image": "img",
    "title": '[class*="title"]',
    "title_fallback": "span",
    "likes": '[class*="like"]',
    "likes_fallback": '[class*="count"]',
    "link": "a",
}

# ── 登录态持久化 ──────────────────────────────────────────

def _save_state(context, path: str = None) -> None:
    """保存浏览器状态（cookie + localStorage）到文件"""
    filepath = path or config.STATE_FILE
    context.storage_state(path=filepath)
    cookies = context.cookies()
    logger.info("浏览器状态已保存到 %s (%d 条 cookie)", filepath, len(cookies))


def _get_storage_state(path: str = None) -> Optional[str]:
    """获取已保存的浏览器状态文件路径"""
    filepath = path or config.STATE_FILE
    if os.path.exists(filepath):
        logger.info("已加载登录态: %s", filepath)
        return filepath
    logger.warning("未找到登录态文件: %s (请先运行 --login)", filepath)
    return None


def _detect_login_wall(page: Page) -> bool:
    """检测页面是否需要登录（只检测登录墙弹窗，不检测页面底部的「登录」链接）"""
    try:
        # 检查是否被重定向到登录页
        if "/login" in page.url:
            return True
        # 检查是否存在登录墙的模态弹窗（而非页面底部的静态链接）
        login_modal = page.query_selector(
            '[class*="login-modal"], '
            '[class*="LoginModal"], '
            '[class*="login-container"]:has([class*="qrcode"])'
        )
        if login_modal and login_modal.is_visible():
            return True
        # 检查页面标题是否被替换为登录
        title = page.title()
        if "登录" in title and "搜索" not in title:
            body_text = page.inner_text("body")[:300]
            if "登录后查看" in body_text:
                return True
    except Exception:
        pass
    return False


# ── 登录流程 ─────────────────────────────────────────────

def do_login() -> bool:
    """打开浏览器让用户手动登录，关闭浏览器窗口后自动保存状态"""
    logger.info("=" * 50)
    logger.info("浏览器窗口已打开，请扫码登录小红书")
    logger.info("登录成功后（能看到搜索结果了），直接关闭浏览器窗口即可")
    logger.info("=" * 50)

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=100,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = context.new_page()

        search_url = SEARCH_URL.format(keyword=quote("金手镯"))
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # 每 5 秒自动保存一次，关闭窗口时已有备份
        logger.info("请扫码登录，脚本每 5 秒自动保存状态...")
        try:
            while browser.is_connected():
                time.sleep(5)
                _save_state(context)
        except Exception:
            pass

        _save_state(context)
        logger.info("登录状态已保存！运行 python scraper.py 开始抓取")
        return True

    except Exception as e:
        logger.error("登录异常: %s", e)
        return False
    finally:
        try:
            pw.stop()
        except Exception:
            pass


# ── 图片下载 ──────────────────────────────────────────────

def _validate_image_url(url: str) -> bool:
    """校验图片 URL 是否可下载（排除 data:、javascript: 等协议）"""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", ""):
        return False
    return True


def _sanitize_filename(text: str, max_len: int = 20) -> str:
    """清理文件名中的非法字符，确保结果非空"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "", text)[:max_len]
    return cleaned.strip() or "note"


def _is_screenshot(filepath: str, white_ratio: float = 0.7) -> bool:
    """快速检测图片是否为截图（尺寸比例 + 缩略图颜色分析）"""
    try:
        img = Image.open(filepath)
        w, h = img.size
        if w < 50 or h < 50:
            return False

        # 尺寸过滤：极端竖长图（手机截图比例 > 1:2）
        aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 1
        if aspect > 2.5:
            logger.info("skip 疑似截图: 竖长比例 %.1f:1 — %s", aspect,
                        os.path.basename(filepath))
            return True

        # 颜色分析：缩到 50px 宽，极快
        img = img.resize((50, int(50 * h / w)), Image.NEAREST).convert("RGB")
        pixels = list(img.getdata())
        white_count = sum(1 for r, g, b in pixels if r > 200 and g > 200 and b > 200)
        ratio = white_count / len(pixels)
        if ratio > white_ratio:
            logger.info("skip 疑似截图: 白色占比 %.0f%% — %s", ratio * 100,
                        os.path.basename(filepath))
            return True
    except Exception as e:
        logger.debug("截图检测失败: %s — %s", filepath, e)
    return False


def download_image(url: str, save_dir: str, filename: Optional[str] = None) -> Optional[str]:
    """下载图片到指定目录

    Args:
        url: 图片 URL
        save_dir: 保存目录
        filename: 自定义文件名（含扩展名），为 None 则自动生成

    Returns:
        下载成功返回文件路径，失败返回 None
    """
    if not _validate_image_url(url):
        logger.warning("跳过无效 URL: %s", url[:80] if url else "(空)")
        return None

    if not filename:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        ext = ".jpg"
        for candidate in (".png", ".webp", ".jpeg", ".gif"):
            if candidate in url.lower():
                ext = candidate
                break
        filename = f"{url_hash}{ext}"

    filepath = os.path.join(save_dir, filename)

    if os.path.exists(filepath):
        logger.info("跳过已存在: %s", filename)
        return filepath

    # 全局去重：相同图片 URL 只下载一次（目标文件尚不存在时）
    url_key = hashlib.md5(url.encode()).hexdigest()
    if url_key in _downloaded_url_hashes:
        logger.debug("跳过重复图片: %s", url[:80])
        return None
    _downloaded_url_hashes.add(url_key)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.xiaohongshu.com/",
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()

            with open(filepath, "wb") as f:
                f.write(resp.content)

            size_kb = len(resp.content) / 1024
            logger.info("ok 下载: %s (%.0fKB)%s",
                        filename, size_kb,
                        f" (retry {attempt})" if attempt > 1 else "")
            return filepath

        except requests.ConnectionError as e:
            last_error = e
            logger.warning("连接失败 (第 %d 次): %s", attempt, e)
        except requests.HTTPError as e:
            last_error = e
            status = resp.status_code if 'resp' in dir() else '?'
            logger.warning("HTTP 错误 %s (第 %d 次)", status, attempt)
            if 'resp' in dir() and resp.status_code in (403, 404, 410):
                break
        except requests.Timeout as e:
            last_error = e
            logger.warning("请求超时 (第 %d 次)", attempt)
        except OSError as e:
            last_error = e
            logger.error("文件写入失败: %s", e)
            break

        if attempt < 3:
            wait = 2 ** attempt
            logger.info("等待 %d 秒后重试...", wait)
            time.sleep(wait)

    logger.error("下载失败 (已重试): %s — %s", filename, last_error)
    return None


# ── 笔记提取 ──────────────────────────────────────────────

def _parse_likes(text: str) -> int:
    """解析点赞数文本，支持「1.2万」格式"""
    if not text:
        return 0
    text = text.strip()
    if "万" in text:
        try:
            return int(float(text.replace("万", "")) * 10000)
        except ValueError:
            return 0
    try:
        return int(re.sub(r"[^\d]", "", text) or "0")
    except ValueError:
        return 0


def _resolve_url(href: str) -> str:
    """将相对路径转为完整 URL"""
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.xiaohongshu.com" + href
    if href.startswith("http"):
        return href
    return ""


def _parse_note_date(text: str):
    """解析小红书笔记的相对时间文本，返回绝对日期 date 对象（解析失败返回 None）"""
    if not text:
        return None
    text = text.strip()
    today = datetime.now().date()
    if "今天" in text or "刚刚" in text or "分钟前" in text or "小时前" in text:
        return today
    if "昨天" in text:
        return today - timedelta(days=1)
    if "前天" in text:
        return today - timedelta(days=2)
    m = re.search(r"(\d+)\s*天前", text)
    if m:
        return today - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*周前", text)
    if m:
        return today - timedelta(days=int(m.group(1)) * 7)
    m = re.search(r"(\d+)\s*个?月前", text)
    if m:
        months = int(m.group(1))
        y, mth = today.year, today.month - months
        while mth <= 0:
            y -= 1
            mth += 12
        return today.replace(year=y, month=mth)
    m = re.search(r"(\d{1,2})\s*[-/]\s*(\d{1,2})", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            target = today.replace(month=month, day=day)
            if target > today:
                target = target.replace(year=today.year - 1)
            return target
        except ValueError:
            return None
    return None


def scroll_page(page: Page, times: int = 3) -> None:
    """滚动页面加载更多内容"""
    for i in range(times):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(config.REQUEST_DELAY)
        logger.info("  滚动 %d/%d", i + 1, times)


def extract_notes(page: Page) -> list[dict]:
    """从搜索结果页提取笔记信息"""
    notes: list[dict] = []

    note_loaded = False
    for selector_name in ("note_card", "note_card_alt"):
        selector = SELECTORS[selector_name]
        try:
            page.wait_for_selector(selector, timeout=10000)
            note_loaded = True
            break
        except PlaywrightTimeout:
            continue

    if not note_loaded:
        logger.warning("未找到笔记卡片，页面可能未加载完整")
        return notes

    cards = page.query_selector_all(SELECTORS["note_card"])
    if not cards:
        cards = page.query_selector_all(SELECTORS["note_card_fallback"])

    for card in cards:
        try:
            img = card.query_selector(SELECTORS["image"])
            if not img:
                continue
            img_url = img.get_attribute("src") or img.get_attribute("data-src") or ""
            if not img_url:
                continue

            img_url = re.sub(r"\?.*$", "", img_url)
            img_url = _resolve_url(img_url)

            title_el = card.query_selector(SELECTORS["title"]) or card.query_selector(SELECTORS["title_fallback"])
            title = title_el.inner_text().strip() if title_el else "无标题"

            like_el = card.query_selector(SELECTORS["likes"]) or card.query_selector(SELECTORS["likes_fallback"])
            like_text = like_el.inner_text().strip() if like_el else ""
            likes = _parse_likes(like_text)

            # 提取发布时间（多策略）
            note_date = None
            for date_sel in [
                '[class*="date"]', '[class*="time"]', '[class*="footer"] span',
                '[class*="bottom"] span', '[class*="info"] span',
                'span[class*="note"]', 'footer span',
            ]:
                el = card.query_selector(date_sel)
                if el:
                    note_date = _parse_note_date(el.inner_text().strip())
                    if note_date:
                        break
            if not note_date:
                all_text = card.inner_text()
                for line in all_text.split("\n"):
                    note_date = _parse_note_date(line.strip())
                    if note_date:
                        break

            a_el = card.query_selector(SELECTORS["link"])
            href = a_el.get_attribute("href") or "" if a_el else ""
            link = _resolve_url(href)

            # 提取封面上的 xsec_token 链接（进入笔记详情页用）
            note_url = ""
            cover_a = card.query_selector("a.cover")
            if cover_a:
                cover_href = cover_a.get_attribute("href") or ""
                note_url = _resolve_url(cover_href)

            notes.append({
                "title": title[:50],
                "image_url": img_url,
                "likes": likes,
                "link": link,
                "note_url": note_url,
                "note_date": note_date,
            })
        except Exception as e:
            logger.debug("解析卡片出错: %s — %s", type(e).__name__, e)
            continue

    return notes


# ── 笔记详情页图片提取 ────────────────────────────────────

def extract_note_images(page: Page) -> list[str]:
    """从笔记详情页提取内容图片（仅笔记正文，过滤评论区/推荐/头像等）"""
    # 滚动以触发懒加载
    for i in range(config.NOTE_SCROLL_TIMES):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(1)

    time.sleep(2)  # 等图片渲染

    # 尝试锁定笔记正文容器，只取里面的图片
    content_selectors = [
        '[class*="note-content"]',
        '[class*="note-detail"]',
        'div.note-scroller',
        '#detail-desc',
        '[class*="article"]',
    ]
    container = None
    for sel in content_selectors:
        el = page.query_selector(sel)
        if el:
            container = el
            break

    imgs = container.query_selector_all("img") if container else page.query_selector_all("img")
    logger.debug("图片容器: %s, 找到 %d 个 img", "content" if container else "全页", len(imgs))

    def _has_ancestor_class(img_element, keywords, depth=5):
        """检查祖先元素 class 是否包含指定关键词"""
        try:
            text = img_element.evaluate(f"""
                el => {{
                    let text = el.className || '';
                    let parent = el.parentElement;
                    for (let i = 0; i < {depth} && parent; i++) {{
                        text += ' ' + (parent.className || '');
                        parent = parent.parentElement;
                    }}
                    return text.toLowerCase();
                }}
            """)
        except Exception:
            return False
        return any(kw in text for kw in keywords)

    def _is_avatar(img_element) -> bool:
        """多维度判断图片是否为头像"""
        if _has_ancestor_class(img_element, ["avatar", "author-avatar", "user-avatar",
                                              "profile-pic", "head-image", "head-img",
                                              "portrait", "author-image"]):
            return True
        alt = (img_element.get_attribute("alt") or "").lower()
        if "头像" in alt or "avatar" in alt:
            return True
        src = img_element.get_attribute("src") or ""
        src_lower = src.lower()
        if any(p in src_lower for p in ["/avatar/", "/avatars/", "avatar.", "avatar-",
                                         "xhs-avatar", "xhscdn.com/avatar"]):
            return True
        try:
            w = img_element.evaluate("el => el.naturalWidth || el.width || 0")
            h = img_element.evaluate("el => el.naturalHeight || el.height || 0")
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                if w > 0 and h > 0:
                    ratio = max(w, h) / min(w, h) if min(w, h) > 0 else 99
                    if max(w, h) < 200 and ratio < 1.5:
                        return True
        except Exception:
            pass
        return False

    images = []
    for img in imgs:
        # ── 1. 获取 src ──
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        if not src or src.startswith("data:"):
            continue

        # ── 2. 头像检测 ──
        if _is_avatar(img):
            logger.debug("跳过头像: %s", src[:80])
            continue

        # ── 3. 过滤评论区/推荐区（祖先 class 含关键词） ──
        if _has_ancestor_class(img, ["comment", "reply", "comment-item", "comment-list",
                                      "recommend", "related-note", "note-item",
                                      "sidebar", "footer"]):
            logger.debug("跳过评论/推荐: %s", src[:80])
            continue

        # ── 4. 过滤 class（图标、二维码等） ──
        cls = img.get_attribute("class") or ""
        parent_cls = ""
        try:
            parent_cls = img.evaluate("el => el.parentElement?.className || ''")
        except Exception:
            pass
        skip = ["logo", "icon", "qrcode", "worldcup", "header"]
        combined = (cls + " " + parent_cls).lower()
        if any(p in combined for p in skip):
            continue

        # ── 5. 过滤非内容 CDN ──
        if "fe-platform.xhscdn.com" in src:
            continue

        # ── 6. 过滤小图（表情包尺寸通常 < 100px） ──
        try:
            w = img.evaluate("el => el.naturalWidth || el.width || 0")
            h = img.evaluate("el => el.naturalHeight || el.height || 0")
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                if w > 0 and h > 0 and (w < 100 or h < 100):
                    logger.debug("跳过小图 %dx%d: %s", w, h, src[:80])
                    continue
        except Exception:
            pass

        images.append(src)

    # 去重
    seen = set()
    unique = []
    for url in images:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    logger.info("  找到 %d 张图片（过滤前 %d 张）", len(unique), len(imgs))
    return unique


def scrape_note_all_images(page: Page, note: dict, save_dir: str, note_dir: str) -> list[str]:
    """进入笔记详情页，下载所有图片

    Args:
        page: Playwright 页面
        note: 笔记信息（含 note_url）
        save_dir: 笔记专属子目录
        note_dir: 总保存目录（未使用，保留兼容）

    Returns:
        下载的图片路径列表
    """
    note_url = note.get("note_url", "")
    if not note_url:
        logger.warning("笔记缺少详情链接，仅下载封面: %s", note["title"][:30])
        filename = _sanitize_filename(note["title"]) + ".jpg"
        result = download_image(note["image_url"], save_dir, filename)
        return [result] if result else []

    # 进入笔记详情页
    try:
        page.goto(note_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
    except PlaywrightTimeout:
        logger.warning("笔记详情页加载超时: %s", note["title"][:30])
        return []

    # 提取所有图片
    image_urls = extract_note_images(page)
    logger.info("  [%s] 找到 %d 张图片", note["title"][:20], len(image_urls))

    if not image_urls:
        # 没找到内容图，退回封面
        filename = _sanitize_filename(note["title"]) + ".jpg"
        result = download_image(note["image_url"], save_dir, filename)
        return [result] if result else []

    # 下载所有图片
    downloaded = []
    for i, img_url in enumerate(image_urls, 1):
        ext = ".jpg"
        for candidate in (".png", ".webp", ".jpeg", ".gif"):
            if candidate in img_url.lower():
                ext = candidate
                break
        filename = f"img_{i:03d}{ext}"
        result = download_image(img_url, save_dir, filename)
        if result:
            # 过滤小于 10KB 的图片（表情包、装饰图）
            size_kb = os.path.getsize(result) / 1024
            if size_kb < 10:
                os.remove(result)
                logger.debug("丢弃小图 (%.0fKB): %s", size_kb, filename)
                continue
            # 过滤截图（聊天记录、手机截图等白色背景占大比例的图）
            if config.SKIP_SCREENSHOTS and _is_screenshot(result, config.SCREENSHOT_WHITE_RATIO):
                os.remove(result)
                continue
            downloaded.append(result)
        time.sleep(0.5)  # 图片间短间隔

    return downloaded


# ── 关键词抓取 ────────────────────────────────────────────

def scrape_keyword(page: Page, keyword: str, save_dir: str) -> list[str]:
    """抓取单个关键词的笔记图片"""
    logger.info("%s", "=" * 50)
    logger.info("搜索关键词: %s", keyword)
    logger.info("%s", "=" * 50)

    url = SEARCH_URL.format(keyword=quote(keyword))

    try:
        # 先访问首页让 cookie 生效，再跳转搜索
        page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
    except PlaywrightTimeout:
        logger.error("页面加载超时: %s", keyword)
        return []

    # 检测登录墙
    if _detect_login_wall(page):
        logger.error("需要登录! 请先运行: python scraper.py --login")
        return []

    scroll_page(page, config.SCROLL_TIMES)

    notes = extract_notes(page)
    logger.info("找到 %d 条笔记", len(notes))

    # 日志：日期解析统计
    parsed = sum(1 for n in notes if n["note_date"] is not None)
    if parsed > 0:
        logger.info("日期解析: %d/%d 条成功", parsed, len(notes))

    if not notes:
        return []

    # 日期过滤
    start = config.DATE_FILTER_START
    end = config.DATE_FILTER_END
    if start or end:
        start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else None
        end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else None
        filtered = []
        for n in notes:
            d = n["note_date"]
            if d is None:
                continue  # 无法解析日期的笔记，跳过
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            filtered.append(n)
        skipped = len(notes) - len(filtered)
        if skipped:
            range_str = f"{start or '...'} ~ {end or '...'}"
            logger.info("日期过滤 (%s): 保留 %d 条, 排除 %d 条", range_str, len(filtered), skipped)
        notes = filtered

    if not notes:
        logger.warning("日期过滤后无结果")
        return []

    notes.sort(key=lambda x: x["likes"], reverse=True)
    top_notes = notes[: config.TOP_N]

    logger.info("取点赞 Top %d:", len(top_notes))
    for i, note in enumerate(top_notes, 1):
        logger.info("  %d. [%d赞] %s", i, note["likes"], note["title"])

    downloaded: list[str] = []
    for i, note in enumerate(top_notes, 1):
        if config.DOWNLOAD_MODE == "all":
            # 为每篇笔记创建独立子目录
            prefix = f"{keyword}_{i:02d}"
            safe_title = _sanitize_filename(note["title"])
            note_subdir = os.path.join(save_dir, f"{prefix}_{safe_title}")
            os.makedirs(note_subdir, exist_ok=True)

            results = scrape_note_all_images(page, note, note_subdir, save_dir)
            downloaded.extend(results)
            logger.info("  [%d/%d] %s -> %d 张图", i, len(top_notes), safe_title, len(results))
        else:
            # 封面模式：只下载封面图
            prefix = f"{keyword}_{i:02d}"
            safe_title = _sanitize_filename(note["title"])
            filename = f"{prefix}_{safe_title}.jpg"
            result = download_image(note["image_url"], save_dir, filename)
            if result:
                if config.SKIP_SCREENSHOTS and _is_screenshot(result, config.SCREENSHOT_WHITE_RATIO):
                    os.remove(result)
                else:
                    downloaded.append(result)

        time.sleep(config.REQUEST_DELAY)

    return downloaded


# ── 浏览器启动 ────────────────────────────────────────────

def _launch_browser():
    """启动 Playwright 浏览器，优先加载已保存的登录态"""
    playwright = sync_playwright().start()

    # 尝试加载已保存的浏览器状态
    state_file = _get_storage_state()

    browser = playwright.chromium.launch(
        headless=config.HEADLESS,
        slow_mo=config.SLOW_MO,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )

    context_kwargs: dict = {
        "viewport": {"width": 1280, "height": 800},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "locale": "zh-CN",
    }
    if state_file:
        context_kwargs["storage_state"] = state_file

    context = browser.new_context(**context_kwargs)

    page = context.new_page()
    page.add_init_script("""
        // 隐藏自动化痕迹
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        // 覆盖 chrome 对象
        window.chrome = { runtime: {} };
        // 覆盖权限查询
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
        );
    """)

    return playwright, browser, context, page, bool(state_file)


def setup_image_dir() -> str:
    """创建图片保存目录"""
    base = config.IMAGE_DIR
    if config.ARCHIVE_BY_DATE:
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(base, today)
    else:
        path = base
    os.makedirs(path, exist_ok=True)
    return path


# ── 登录预检 ──────────────────────────────────────────────

def ensure_login(page: Page, context, cookie_loaded: bool) -> bool:
    """抓取前确认登录状态，未登录则等待用户扫码"""
    logger.info("检查登录状态...")
    try:
        page.goto(SEARCH_URL.format(keyword=quote("黄金")), wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
    except PlaywrightTimeout:
        logger.warning("页面加载超时，继续检查...")

    if not _detect_login_wall(page):
        logger.info("已登录，开始抓取")
        return True

    if config.HEADLESS:
        logger.error("未登录！无头模式下无法扫码，请先关闭无头模式登录")
        return False

    if not cookie_loaded:
        logger.warning("=" * 50)
        logger.warning("未登录！请在浏览器窗口中扫码登录")
        logger.warning("登录成功后脚本会自动继续...")
        logger.warning("=" * 50)

    # 等待用户扫码（只检测当前页面，不刷新/跳转）
    count = 0
    while True:
        time.sleep(3)
        count += 1
        if not _detect_login_wall(page):
            logger.info("登录成功！保存状态并继续抓取...")
            _save_state(context)
            time.sleep(1)
            return True
        if count % 20 == 0:
            logger.warning("仍在等待登录... 请在浏览器中扫码")


# ── 主流程 ────────────────────────────────────────────────

def run() -> list[str]:
    """主抓取流程"""
    _downloaded_url_hashes.clear()
    logger.info("小红书爆款图片抓取工具")
    logger.info("时间: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("关键词: %s", ", ".join(config.KEYWORDS))
    logger.info("每个关键词取 Top %d", config.TOP_N)

    save_dir = setup_image_dir()
    logger.info("保存目录: %s", save_dir)

    all_downloaded: list[str] = []
    pw = None

    try:
        pw, browser, context, page, cookie_loaded = _launch_browser()

        if not ensure_login(page, context, cookie_loaded):
            logger.error("登录检查失败，抓取终止")
            return []

        for keyword in config.KEYWORDS:
            try:
                downloaded = scrape_keyword(page, keyword, save_dir)
                all_downloaded.extend(downloaded)
            except Exception as e:
                logger.error("关键词 '%s' 抓取异常: %s — %s", keyword, type(e).__name__, e)
            finally:
                time.sleep(3)

    except Exception as e:
        logger.error("浏览器启动失败: %s — %s", type(e).__name__, e)
    finally:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    logger.info("%s", "=" * 50)
    logger.info("完成! 共下载 %d 张图片", len(all_downloaded))
    logger.info("保存位置: %s", save_dir)
    logger.info("%s", "=" * 50)

    return all_downloaded


# ── 入口 ──────────────────────────────────────────────────

def main():
    """命令行入口：支持 --login 参数"""
    if len(sys.argv) > 1 and sys.argv[1] == "--login":
        success = do_login()
        sys.exit(0 if success else 1)
    else:
        run()


if __name__ == "__main__":
    main()
