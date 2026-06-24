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
HEADLESS = True          # True=无头模式（后台运行），False=显示浏览器窗口（调试用）
SLOW_MO = 500            # 操作间隔毫秒数，模拟人类速度
SCROLL_TIMES = 3         # 搜索页滚动次数（越多加载越多结果）

# 排序方式：按点赞数取 Top N
TOP_N = 15

# 请求间隔（秒），避免太快
REQUEST_DELAY = 2
