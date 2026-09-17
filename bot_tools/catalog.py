"""One public command directory shared by /toolbox and /otter."""
from message_ui import help_panel


def command_directory() -> str:
    return help_panel("机器人 · 全部功能", [
        "💬 AI 角色",
        "/ai 多角色聊天 · 私聊配置自己的密钥和人设",
        "/learn 群聊学习 · 群管理员开启与管理",
        "",
        "🧩 游戏插件",
        "/ff14 FF14 插件 · 查询目录与用法",
        "/tr 嘟嘟脸恶作剧 · 角色、图鉴与攻略",
        "/akhr 明日方舟 · 公开招募计算",
        "/bili B站推送 · 直播与动态订阅",
        "",
        "🖼 图片与文字",
        "/cat 猫图 · /waifu 本地动漫图 · /image 分类图库",
        "/gif 文字动图 · /tex 公式 · /duilian 对联草稿",
        "",
        "🎲 互动与小工具",
        "/vote 投票 · /lottery 抽奖 · /custom_reply 关键词",
        "/ginfo 群设置总览（群管理员）",
        "/dice 骰子 · /random 随机数",
        "",
        "🔧 管理与反馈",
        "/bot 管理面板 · /group 会话设置 · /command 命令开关",
        "/left_reply 剩余次数 · /comment 反馈",
        "/ping 在线检查 · /about 关于",
    ], footer="用法：/命令 help。有群全消息权限可直接发命令，否则请先 @机器人。管理操作仍需权限；图库需添加素材，部分查询需配置 API。")


def ff14_directory() -> str:
    return help_panel("FF14 插件 · 查询目录", [
        "🛒 市场与制作",
        "/market 物品名 服务器/国服全大区 · 市场价格与全区比价",
        "/sales 物品名 服务器 · 最近成交",
        "/cheapest 物品名 大区 · 跨服比价",
        "/recip 物品名 · 配方",
        "/craftcost 物品名 大区 · 制作成本",
        "/gather 物品名 · 采集地点",
        "",
        "⚔ 战绩与角色工具",
        "/dps 角色名 服务器 [副本简称] · 排名",
        "/raid 角色名 服务器 [副本简称] · 通关记录",
        "/fflogs · 战绩详细帮助",
        "/fsx 暴击 3000 · 副属性计算",
        "",
        "🎮 游戏资料与活动",
        "/quest 任务名 · /search 物品名",
        "/weather 区域 [天气] [数量]",
        "/house 服务器 [区域] [大小] [部队|个人]",
        "/ofish · 海钓班次",
        "/hunt · 手动狩猎时钟（非实时数据）",
        "/luck · 今日运势 /gate [2|3] · 挖宝选门",
    ], footer="直接输入具体命令查询，命令后加 help 查看参数。部分接口需要管理员配置凭据。机器人全部功能：/toolbox。")
