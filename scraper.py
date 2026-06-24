"""
小红书爆款图片抓取工具
用途：自动搜索指定关键词，下载高点赞笔记的封面图
"""

import os
import re
import time
import hashlib
import requests
from datetime import datetime
from urllib.parse import quote
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config


def setup_image_dir():
    """创建图片保存目录"""
    base = config.IMAGE_DIR
    if config.ARCHIVE_BY_DATE:
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(base, today)
    else:
        path = base
    os.makedirs(path, exist_ok=True)
    return path


def scroll_page(page, times=3):
    """滚动页面加载更多内容"""
    for i in range(times):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(config.REQUEST_DELAY)
        print(f"  滚动 {i+1}/{times}")


def extract_notes(page):
    """从搜索结果页提取笔记信息"""
    notes = []

    # 等待笔记卡片加载
    try:
        page.wait_for_selector('section.note-item', timeout=10000)
    except PlaywrightTimeout:
        print("  ⚠️ 未找到笔记卡片，尝试备用选择器...")
        try:
            page.wait_for_selector('[class*="note"]', timeout=5000)
        except PlaywrightTimeout:
            print("  ❌ 页面未加载出笔记内容")
            return notes

    # 提取笔记卡片信息
    cards = page.query_selector_all('section.note-item')
    if not cards:
        # 备用选择器
        cards = page.query_selector_all('div[class*="note-item"]')

    for card in cards:
        try:
            # 提取封面图
            img = card.query_selector('img')
            if not img:
                continue
            img_url = img.get_attribute('src') or img.get_attribute('data-src') or ""
            if not img_url:
                continue

            # 清理图片URL，获取高清版本
            img_url = re.sub(r'\?.*$', '', img_url)  # 去掉参数获取原图
            if img_url.startswith('//'):
                img_url = 'https:' + img_url

            # 提取标题
            title_el = card.query_selector('[class*="title"]') or card.query_selector('span')
            title = title_el.inner_text().strip() if title_el else "无标题"

            # 提取点赞数
            likes = 0
            like_el = card.query_selector('[class*="like"]') or card.query_selector('[class*="count"]')
            if like_el:
                like_text = like_el.inner_text().strip()
                # 处理 "1.2万" 这种格式
                if '万' in like_text:
                    try:
                        likes = int(float(like_text.replace('万', '')) * 10000)
                    except ValueError:
                        likes = 0
                else:
                    try:
                        likes = int(re.sub(r'[^\d]', '', like_text) or 0)
                    except ValueError:
                        likes = 0

            # 提取笔记链接
            link = ""
            a_el = card.query_selector('a')
            if a_el:
                href = a_el.get_attribute('href') or ""
                if href.startswith('/'):
                    link = 'https://www.xiaohongshu.com' + href
                elif href.startswith('http'):
                    link = href

            notes.append({
                'title': title[:50],
                'image_url': img_url,
                'likes': likes,
                'link': link,
            })
        except Exception as e:
            print(f"  解析卡片出错: {e}")
            continue

    return notes


def download_image(url, save_dir, filename=None):
    """下载图片"""
    if not url:
        return None

    if not filename:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        ext = '.jpg'
        if '.png' in url.lower():
            ext = '.png'
        elif '.webp' in url.lower():
            ext = '.webp'
        filename = f"{url_hash}{ext}"

    filepath = os.path.join(save_dir, filename)

    if os.path.exists(filepath):
        print(f"  跳过已存在: {filename}")
        return filepath

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.xiaohongshu.com/',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        with open(filepath, 'wb') as f:
            f.write(resp.content)

        size_kb = len(resp.content) / 1024
        print(f"  ✅ 下载: {filename} ({size_kb:.0f}KB)")
        return filepath
    except Exception as e:
        print(f"  ❌ 下载失败: {e}")
        return None


def scrape_keyword(page, keyword, save_dir):
    """抓取单个关键词"""
    print(f"\n{'='*50}")
    print(f"🔍 搜索关键词: {keyword}")
    print(f"{'='*50}")

    encoded = quote(keyword)
    url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_notes"

    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)  # 等待初始加载
    except PlaywrightTimeout:
        print(f"  ❌ 页面加载超时: {keyword}")
        return []

    # 滚动加载更多
    scroll_page(page, config.SCROLL_TIMES)

    # 提取笔记
    notes = extract_notes(page)
    print(f"  📋 找到 {len(notes)} 条笔记")

    if not notes:
        return []

    # 按点赞数排序，取 Top N
    notes.sort(key=lambda x: x['likes'], reverse=True)
    top_notes = notes[:config.TOP_N]

    print(f"  🏆 取点赞 Top {len(top_notes)}:")
    for i, note in enumerate(top_notes, 1):
        print(f"    {i}. [{note['likes']}赞] {note['title']}")

    # 下载图片
    downloaded = []
    for i, note in enumerate(top_notes, 1):
        prefix = f"{keyword}_{i:02d}"
        # 用标题前几个字做文件名
        safe_title = re.sub(r'[\\/:*?"<>|]', '', note['title'])[:20]
        filename = f"{prefix}_{safe_title}.jpg"

        result = download_image(note['image_url'], save_dir, filename)
        if result:
            downloaded.append(result)

        time.sleep(config.REQUEST_DELAY)

    return downloaded


def run():
    """主流程"""
    print("🚀 小红书爆款图片抓取工具")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔑 关键词: {', '.join(config.KEYWORDS)}")
    print(f"📊 每个关键词取 Top {config.TOP_N}")

    save_dir = setup_image_dir()
    print(f"📁 保存目录: {save_dir}")

    all_downloaded = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=config.HEADLESS,
            slow_mo=config.SLOW_MO,
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )
        page = context.new_page()

        for keyword in config.KEYWORDS:
            downloaded = scrape_keyword(page, keyword, save_dir)
            all_downloaded.extend(downloaded)
            time.sleep(3)  # 关键词之间间隔

        browser.close()

    # 汇总
    print(f"\n{'='*50}")
    print(f"✅ 完成！共下载 {len(all_downloaded)} 张图片")
    print(f"📁 保存位置: {save_dir}")
    print(f"{'='*50}")

    return all_downloaded


if __name__ == '__main__':
    run()
