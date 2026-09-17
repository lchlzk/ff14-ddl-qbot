# 群聊学习 / Learning Chat

这是 [`nonebot-plugin-learning-chat 0.4.0`](https://github.com/CMHopeSunshine/nonebot-plugin-learning-chat) 面向腾讯官方 `nonebot-adapter-qq` 的原生移植。上游作者为 CMHopeSunshine（惜月），上游及本移植部分按照 **AGPL-3.0-only** 提供。

## 使用规则

- 默认关闭。群主或群管理员必须在目标群发送 `/learn on` 才开始学习。
- 学习库默认公开：公开群各自在本群学习，但学到的候选问答可以在其他公开群参与回复。群主可切换成本群私有，私有群只使用自己的内容。学习内容不会进入图片图库或 AI 角色上下文。
- 学习相邻发言：例如多次出现“早上好”之后紧跟“早”，机器人会逐渐把“早”学成候选回答。
- 默认同一回答至少学习 4 次才可能回复。次数越高，被选中的概率越高，但仍可能不回复。
- 相同消息达到复读阈值且至少来自两名成员时，机器人会复读或发送“打断复读！”。
- 斜杠命令、过短文字、URL、疑似 Token/API Key 和不合规图片不会学习。机器人消息不会再次触发学习。
- 需要 QQ 开放平台向机器人投递群普通消息；只有 @ 消息权限时，插件看不到未 @ 的群聊内容。

## 群内命令

查看状态和帮助：

```text
/learn
/learn status
```

群主、群管理员或机器人总管理员：

```text
/learn on
/learn off
/learn threshold 4
/learn repeat 3
/learn repeat 0
/learn image on
/learn image off
/learn mode public
/learn mode local
/learn word add 屏蔽词
/learn word del 屏蔽词
/learn word list
/learn list 1
/learn ban 回复编号
/learn bans 1
/learn unban 禁用编号
```

`/learn mode public` 与 `/learn mode local` 仅限本群群主或机器人总管理员。公开模式会让本群学到的问答进入机器人的公开学习库，也允许本群使用其他公开群的问答；私有模式不跨群。切换不会搬迁或删除已有内容，改回公开后原有内容会重新进入公开候选池。

回复某条普通文字消息并发送 `/learn ban-reply`，可在当前群直接禁用该回答，包括来自公开学习库的同内容回答。回复某位群友并发送 `/learn user block`，之后不再学习该成员的新消息；`/learn user unblock` 恢复。机器人只保存这个成员在当前群的匿名身份摘要，不显示或保存数字 QQ 号。

群主或机器人总管理员可永久清空本群学习数据：

```text
/learn clear confirm
```

`/learn off` 只停止继续学习和回复，已有内容保留；`clear confirm` 删除学习内容、图片、回复关系和禁用回复，但保留开关参数与屏蔽词。

## 容量与持久化

| 项目 | 限制 |
| --- | --- |
| 单条文字 | 最多 300 字、1200 UTF-8 字节 |
| 回复关系 | 每群最多 5000 组，超出后优先清理禁用、低频和最旧记录 |
| 唯一文字内容 | 每群最多 6000 个 |
| 图片 | 每群最多 100 个、合计 100 MiB；单图最多 4 MiB |
| 图片格式 | PNG、JPEG、WebP、GIF；沿用图库的像素、GIF 帧数和下载来源检查 |
| 消息去重 ID | 最长保留 24 小时，不含正文 |

数据保存在现有 `/app/data/bot.sqlite3` 数据卷的 `learning_*` 表，修改设置立即生效，不需要修改 `.env` 或重启。备份及删除注意事项与工具箱其他 SQLite 数据相同。

## 隐私与上游差异

开启后，普通群文字和图片会保存在机器人服务器，管理员可在网页后台搜索、查看、禁用或恢复学习关系。请在开启前告知群成员，并通过私有学习库、屏蔽词、成员屏蔽和定期清理降低风险。服务器管理员和数据库备份持有者能够读取这些内容。

没有直接安装上游 Web UI：其 Pydantic 1.x、OneBot、Tortoise ORM 等依赖会破坏当前 QQ 官方适配器环境。本项目自己的本地网页后台提供按群配置、搜索学习词库以及禁用/恢复回答，并使用服务器本地设置、只保存加盐哈希的独立账号密码。

公开学习库由群主明确控制；需要严格隔离时请切到 `local`。OneBot 的撤回、戳一戳和定时主动群发无法在腾讯官方 QQ 机器人上保持同等语义，因此本版不提供这三项；被禁用内容只停止后续回复，不尝试撤回已经发送的 QQ 消息。

## License notice

Portions are adapted from `nonebot-plugin-learning-chat` 0.4.0, Copyright (C) CMHopeSunshine and contributors, licensed under GNU Affero General Public License v3.0 only. Source: <https://github.com/CMHopeSunshine/nonebot-plugin-learning-chat>. The complete license text is included as `LICENSES/AGPL-3.0-only.txt` in source and as `AGPL-3.0-only.txt` in release packages.
