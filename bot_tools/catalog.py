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
