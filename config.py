# 小红书爆款抓取配置

# 搜索关键词列表
KEYWORDS = ["金手镯", "黄金手镯", "金镯子"]

# 每个关键词抓取数量
MAX_NOTES_PER_KEYWORD = 15

# 图片保存目录
IMAGE_DIR = "images"

# 是否按日期归档（images/2026-06-23/）
ARCHIVE_BY_DATE = True

# 浏览器设置
HEADLESS = False         # False=显示浏览器（推荐，小红书会拦截无头模式），True=后台运行
SLOW_MO = 500            # 操作间隔毫秒数，模拟人类速度
SCROLL_TIMES = 3         # 搜索页滚动次数（越多加载越多结果）

# 排序方式：按点赞数取 Top N
TOP_N = 15

# 请求间隔（秒），避免太快
REQUEST_DELAY = 2

# 下载模式: "cover"=仅封面, "all"=笔记内全部图片
DOWNLOAD_MODE = "all"

# 笔记详情页滚动次数（图片懒加载）
NOTE_SCROLL_TIMES = 2

# Cookie 持久化（保存登录态，避免每次手动登录）
STATE_FILE = "browser_state.json"  # 登录态持久化文件

# 日期过滤：只抓取指定日期范围内发布的笔记（None = 不限）
DATE_FILTER_START = None  # 起始日期 "YYYY-MM-DD"（None = 不限）
DATE_FILTER_END = None    # 结束日期 "YYYY-MM-DD"（None = 不限）

# 过滤截图：跳过白色背景占比过高的图片（聊天截图、手机截图等）
SKIP_SCREENSHOTS = True       # 是否开启截图过滤
SCREENSHOT_WHITE_RATIO = 0.7  # 白色/浅色像素占比阈值（0.7 = 70%）

# ── 安全模式开关 ──────────────────────────────────────────
LOW_FREQ_MODE = True          # 低频模式：随机延迟、限量、强制封面
SEMI_AUTO_MODE = False        # 半自动模式：每篇笔记需人工确认后再下载

# 低频模式参数（仅 LOW_FREQ_MODE=True 时生效）
LOW_FREQ_TOP_N_CAP = 5              # 每个关键词最多抓取篇数
LOW_FREQ_MAX_KEYWORDS = 1           # 单次运行最多处理几个关键词
LOW_FREQ_SESSION_MAX_NOTES = 10     # 单次运行最多处理笔记总数
LOW_FREQ_DELAY_MIN = 8              # 操作间隔下限（秒）
LOW_FREQ_DELAY_MAX = 15             # 操作间隔上限（秒）
LOW_FREQ_KEYWORD_DELAY_MIN = 30     # 关键词之间间隔下限（秒）
LOW_FREQ_KEYWORD_DELAY_MAX = 60     # 关键词之间间隔上限（秒）
LOW_FREQ_SCROLL_TIMES_CAP = 2       # 搜索页最多滚动次数

# 运行时停止标志（由 GUI/WebUI 停止按钮设置，勿手动改）
SCRAPE_ABORT_REQUESTED = False
