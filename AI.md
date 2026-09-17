# AI 多角色聊天 / AI roleplay

## 先看规则

- 任何用户都能在机器人**私聊**中创建多个角色，分别配置名字、人设、AI 服务和自己的 API Key。不需要管理员身份或服务器操作。
- 每个用户在同一个群只能加入一个自己的角色；其他用户也能加入自己的角色。比如你的小桃加入群 A、阿雪加入群 B；小桃还可以加入群 C。
- 角色刚加入群时默认采用“**必须提到角色名 + 收集 0**”：普通消息中任何位置包含角色名字就立即回复，不要求以名字开头。角色主人可针对每个群，改为无需点名，或先收集若干条符合条件的消息再集中回答。
- 每次响应使用**该角色保存的 Key 和手动选择的模型**，计入角色主人的额度。不会借用管理员或其他人的 Key，也不会在免费额度耗尽时自动切换模型。
- 私聊仅能与自己创建的角色聊天。每位用户的全部角色**共用每天 5 次私聊请求**，不是每个角色 5 次。建新角色、删角色、换 Key、重新启动都不能重置额度。
- 角色、群、私聊的聊天记忆相互隔离；同一个角色跨群也不会串线。群里多人对同一角色聊天会共享该角色在本群的历史，成员用本群匿名标记区分。
- QQ 仍必须向机器人投递消息。免 @ 需要 QQ 开放平台的群全消息权限；插件不能绕过腾讯的投递限制。

注意：这里是**一个 QQ 官方机器人里的多个 AI 角色**，不是自动创建多个独立 QQ 机器人账号。回复仍由原 QQ 机器人发送，正文前有角色名字。

## 1. 私聊创建第一个角色

以下命令逐条发给**你自己的 QQ 机器人私聊**，不是在服务器终端，也不是发给 Codex。每次等机器人回复后再发下一条。

```text
/ai 新建 小桃 | 你是温柔活泼的虚构猫娘，喜欢用一到三句话聊天
/ai 服务 glm
/ai 模型
/ai 密钥 这里替换成你自己的APIKey
```

然后试着说：`小桃你好`。也可发送 `/ai 设置` 查看引导，`/ai 状态` 检查当前选中的角色。

若选择千问，将服务命令换成以下之一，再填写**对应地域**的 Key：

| 命令 | 默认推荐模型 | 接口地域 |
| --- | --- | --- |
| `/ai 服务 glm` | `glm-4.7-flash` | 智谱国内 |
| `/ai 服务 qwen beijing` | `qwen-flash-character-2026-02-26` | 北京 |
| `/ai 服务 qwen singapore` | `qwen-flash-character` | 新加坡 |
| `/ai 服务 qwen virginia` | `qwen-flash-character-2026-02-26` | 美国弗吉尼亚 |

选择服务后会自动采用该服务的默认推荐模型。私聊发送 `/ai 模型` 会显示当前模型、可选白名单、默认推荐以及选择理由；用 `/ai 模型 模型ID` 手动切换，`/ai 模型 推荐` 恢复默认推荐。切换模型会保留当前角色的 Key、人设和群授权，但会清空该角色在私聊及各群的旧记忆，防止不同模型混用旧上下文。不会自动切换、自动重试或暗中改用付费模型。

智谱国内可选：

| 模型 | 适用理由 |
| --- | --- |
| `glm-4.7-flash` | **默认推荐**；当前官方免费档、速度快，日常角色聊天性价比最高 |
| `glm-4.7-flashx` | 低价付费档，适合希望比免费公共池更稳定的调用 |
| `glm-4.7` | 通用指令能力更强，通常比 Flash 更贵 |
| `glm-5` / `glm-5-turbo` / `glm-5.2` | 新一代通用模型，效果优先，成本和等待时间可能更高 |

千问北京可选：

| 模型 | 适用理由 |
| --- | --- |
| `qwen-flash-character-2026-02-26` | **默认推荐**；角色扮演专用、版本固定、成本较低，角色表现更稳定 |
| `qwen-flash-character` | 角色扮演专用动态版，会随平台更新 |
| `qwen-plus-character` | 角色扮演增强版，通常效果更强、价格更高 |
| `qwen3.7-flash` | 通用低成本模型，不专门优化角色还原 |
| `qwen3.7-plus` | 通用能力更强，但角色效果和成本不一定优于 Character |

新加坡和弗吉尼亚只展示该地域已核对的 Character 模型。数学、翻译、向量、视觉、语音等专用模型不会出现在角色聊天白名单中；控制台显示免费额度不代表它适合本机器人当前的纯文字角色接口。

地域由 API 账户/Key 决定，不由你的住址或机器人服务器所在地决定。不支持自定义 Base URL、任意模型或 Coding Plan 专用接口。请使用对应平台的标准按量 API Key。选择服务或模型只保存配置，不会自动测试或扣费；第一次叫名字才会发起请求。

本次核对的官方资料：[智谱 GLM-4.7-Flash](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash)、[智谱定价](https://bigmodel.cn/pricing)、[千问角色扮演](https://help.aliyun.com/zh/model-studio/role-play)、[千问文本模型](https://help.aliyun.com/zh/model-studio/text-generation-model)、[百炼地域与 Base URL](https://help.aliyun.com/zh/model-studio/base-url)。定价、免费政策、地区可用性和限流均以平台当时规则为准。智谱及支持思考的千问通用模型会关闭思考模式，只发送文字聊天，不开工具或联网检索。

百炼控制台的“免费额度用完即停”不是模型切换开关。开启后，当前模型免费额度耗尽会返回 `403 AllocationQuota.FreeTierOnly`；机器人会提示角色主人私聊 `/ai 模型`，但仍不会自动换模型。关闭该保护后可能直接进入按量付费，具体以百炼账户状态为准。

智谱采用流式接收，在服务器收齐后只向 QQ 发送一条回复，不逐字刷屏。连接超时 10 秒，连续 90 秒没有新数据或整次请求超过 120 秒会停止等待；流式接收不会消除平台排队时间。网络中断、返回格式错误或未收到完整结束标记时，不发送半截回答，也不自动重试。报错会区分连接失败、等待回复超时和传输中断；不会展示密钥、思考内容或上游原始错误。千问仍使用原来的非流式接收方式。

智谱报错 `1305` 表示模型服务繁忙，不等于密钥错误或欠费；`1302` 表示账户请求频率/并发受限，`1113` 才是余额不足。机器人会用固定安全文案区分这些情况，其他额度限制请查看智谱控制台。参见[智谱错误码](https://docs.bigmodel.cn/cn/api/api-code)。

## 2. 创建、选择和管理多个角色

```text
/ai 新建 阿雪 | 你是冷静但关心朋友的虚构角色，回复简短
/ai 角色列表
/ai 选择 角色编号
/ai 角色 新名字 | 新人设
```

新建后自动选中该角色，但**不会自动复制任何密钥**。给新角色配置服务与 Key，或者明确复用自己另一个角色的配置：

```text
/ai 复用密钥 原角色编号
```

只允许复用自己的 Key。复用后两个角色各自加密保存，修改原角色的 Key **不会自动同步**到其他角色；去 AI 平台撤销同一把 Key 则会影响所有使用它的角色。

`/ai 选择` 只切换之后的配置对象，**不会替换群内运行中的角色**。服务、密钥、人设、发布、清空、删除密钥默认操作当前选中的角色；先 `/ai 状态` 核对。

```text
/ai 删除角色 角色编号 确认
/ai 删除密钥 确认
```

删除角色会一并撤销这个角色的群授权、删除其已保存密钥和记忆，其他角色不变。删除密钥保留角色的人设，但让该角色在所有群停用。备份和 QQ 聊天记录可能仍有旧副本；需要彻底让 Key 失效，请到 AI 平台撤销它。

## 3. 指定角色加入群：普通用户即可完成

1. **私聊**选择你要加入群的角色：`/ai 选择 角色编号`。
2. **私聊**发送 `/ai 发布 100`，允许这个角色在目标群每天最多请求 100 次。额度由你承担；私聊和所有群还受你个人每日总上限约束。
3. 将机器人返回的 `/ai 加入 接入码` 发到**目标群**。任何群成员都可提交，不要求群主批准。群内会显示角色名字、群标识和确认码。
4. 回到你的**私聊**发送 `/ai 确认 确认码`，核对角色、每日额度和群标识确实与刚才目标群一致。
5. 确定后**私聊**发送 `/ai 启用 确认码`。此时才会真正授权你的 Key 给这个群里的角色使用。

为什么要回私聊确认：不能假设 QQ 私聊 OpenID 和群成员 OpenID 永远一样。一次性码把“你的角色”和“准确的目标群”关联起来，不把群成员身份强行认定为你的私聊账号；拿到码的其他人也无法替你完成最后的私聊授权。

接入码和确认码最多 10 分钟有效，一次成功后失效。重新发布会作废**当前角色**的旧接入码。别的群不能覆盖已经生成的确认申请。切换当前选择不影响申请对应的角色；更改该角色的服务、Key、人设则会废弃旧申请。

重复上述过程，可将同一个角色加入其他群。将另一个角色加入已有自己角色的群前，先撤销你原角色在该群的授权：

```text
/ai 授权列表
/ai 撤销 授权编号
```

`/ai 授权列表 2` 查看下一页；列表会同时显示你的不同角色和对应群标识。`/ai 撤销 all` 撤销**你的全部角色**的群授权和待处理接入码，不影响别人。

### 设置角色在某个群里的触发方式

只有角色主人能设置，并且必须在**机器人私聊**中操作。先取得对应群授权的 12 位编号：

```text
/ai 授权列表
/ai 群设置 授权编号
```

点名条件与收集条数是两项独立设置：

```text
/ai 群设置 授权编号 点名 on
/ai 群设置 授权编号 点名 off
/ai 群设置 授权编号 收集 3 5
/ai 群设置 授权编号 收集 0
```

- `点名 on`：只有包含角色名的普通群消息才计入；`收集 3 5` 表示每轮随机选定 3～5 条，攒够后一次回答。
- `点名 off`：所有普通群消息都可计入；仍然排除斜杠命令、机器人消息和空文本。
- `收集 0`：不等待累计，每一条符合点名条件的消息都会尝试调用 AI。它不是“关闭收集后不回复”。
- 收集范围允许 `1～20`，最小值不能大于最大值。每轮达到阈值并回答后，下一轮会重新随机阈值。
- 例如“`点名 on` + `收集 3 5`”会收集 3～5 条叫到角色名的消息；“`点名 off` + `收集 3 5`”会收集 3～5 条普通群消息；“`点名 off` + `收集 0`”则会尝试回答群里的每条普通消息。

每次修改都会立即生效并清掉该角色在该群尚未回答的临时消息。**无需点名 + 收集 0 是最高消耗模式**：活跃群的每条普通消息都可能产生一次模型请求，可能很快耗尽 Token、账户余额、群额度和个人总额度。收集范围越小，调用越频繁；范围越大，每次请求携带的输入消息越多，单次输入 Token 通常越高。建议使用专用限额 Key，并通过 `/ai 额度 次数` 限制每天最多请求数。

群内 `/ai` 或 `/ai 状态 2` 查看本群角色和授权编号。授权编号不是密钥，也不会赋予管理权限。机器人无法从这套 QQ 事件直接保证获得真实群名/群号，因此显示服务器生成的稳定群标识供两边核对。

## 4. 额度、聊天长度和记忆

| 限制 | 当前默认值/规则 |
| --- | --- |
| 用户每日总请求上限 | 100 次，私聊 `/ai 额度 次数` 可设置 1～1000，所有角色和群共用 |
| 用户私聊 | 每日最多 5 次，全部自己的角色共用，不能调高 |
| 群角色 | 发布时设置 1～1000 次/日，实际仍受用户总额度限制 |
| 私聊角色数 | 每用户最多 20 个；每个角色最多授权 20 个群 |
| 群中角色数量 | 不限定为一个，但每个私聊用户在该群仅能有一个自己的角色 |
| 频率 | 同一角色在同一会话间隔 5 秒；同一角色同时最多一个模型请求 |
| 私聊单条输入 | 500 字、1500 UTF-8 字节以内 |
| 群消息收集 | 每轮 0 或 1～20 条；临时消息有效期 30 分钟，过期后不再进入回答并在下次清理时删除 |
| 群聊单次批量输入 | 收集时每条先限制为 500 字、1500 UTF-8 字节；整批提示最多 2000 字、6000 UTF-8 字节 |
| 人设 | 800 字、2400 UTF-8 字节以内 |
| 单次输出 | 最多 256 tokens，显示最多 400 字 |
| 记忆 | 最近最多 3 轮，历史上下文最多 1800 字节；24 小时不使用的历史在下次清理时删除，不会继续发送 |

每天北京时间 00:00 重置计数。**按发起的模型请求计数，失败/超时不返还次数，不自动重试。**一批消息同时触发两个角色算两次请求。这样避免网络中断时重复计费；腾讯拒绝发送回复时，上游也可能已经计费。快速连续触发时仍受 5 秒冷却和单角色并发限制，因此“收集 0”表示每条符合条件的消息都会尝试回复，不保证越过安全限制强制发起请求。

`/ai 用量` 显示今天的请求次数和服务端已报告的输入/输出 tokens，不显示伪精确的人民币账单；未报告用量的失败调用单独标记。统计保留最近 31 天。设置里没有固定人民币扣费上限，请同时在 AI 平台限制 Key 的权限、额度及余额。

默认点名模式只发送叫到该角色的文本、该角色人设和对应历史。角色主人开启无需点名或消息收集后，符合该群设置的普通文本会先在服务器 SQLite 中短暂保存，达到本轮阈值后作为一批发送给该角色选择的 AI 服务；消息正文有效期 30 分钟，过期后不再进入回答并在下一次 AI 清理时删除。用于去重的 QQ 消息 ID 最长保留 24 小时且不包含正文。不会收集或上传图片、附件、斜杠命令、机器人消息，也不会把其他角色的回复自动转发给模型；模型不能执行机器人命令、读取服务器文件或调用其他插件。

自己在私聊 `/ai 清空` 清空当前选中角色的私聊记忆。修改当前角色的名字、人设或 Key 会清空它的全部会话记忆，不清空其他角色。复制密钥或切换服务会撤销**当前角色**旧的群授权，需重新确认。

## 5. 群管理与总管理员

群管理员和群主可以管理**本群**角色，不可查看 Key、人设全文或更改角色主人的总额度：

```text
/ai 暂停 授权编号
/ai 恢复 授权编号
/ai 清空 授权编号
```

群管理员、群主和机器人总管理员都可 `/ai 移除 授权编号`。群管理员必须指定一个编号；群主或总管理员可用 `all` 移除本群全部角色。暂停、恢复会清空对应本群记忆；已撤销或暂停的角色，其正在进行的请求返回后不会写回旧记忆或发送正常回复（已提交的请求无法保证撤回费用）。`/ginfo [页码]` 会为群设置列出对应命令；AI 列表只显示角色名和编号，不显示主人、所用 AI、状态、额度、API Key 或人设。

`/command disable ai` 可以关闭当前群的叫名聊天；`/command enable ai` 恢复。仍保留 `/ai` 配置、删除和撤销入口，避免锁住用户的 Key。

服务器授权的机器人总管理员可在绑定的私聊 `/ai 全局 off` 关闭全部 AI 聊天，`/ai 全局 on` 恢复。普通群主不是总管理员，无权使用全局开关。

## 6. 本地持久化、安全及更新

- 无需新增 `.env` 字段；容器通过原有 `bot-data:/app/data` 数据卷持久化，用户修改 AI 配置立即生效。
- SQLite：`/app/data/bot.sqlite3` 的 `ai_*` 表。API Key 用 Fernet 认证加密保存。服务器主密钥首次使用时自动生成于 `/app/data/secrets/ai-master.key`，Linux 下文件 600、目录 700。
- 私聊密钥输入经过 QQ 传输，**不是端到端保密输入**。应用会在 NoneBot 记录原始事件前屏蔽 AI 指令日志；第三方插件自行记录原始事件不在这项保证内。不要安装不信任的插件，不要开启外部 HTTP 抓包来记录凭据。
- 服务器管理员能读取主密钥并解密 API Key，不能承诺“服务器主人也看不到”。人设和短期聊天历史本地明文保存，调用时发送给用户选择的 AI 服务。
- 数据卷、主密钥、`.env` 不进入镜像或安装包。打包会包括 `AI.md`；发包给别人不包含你的 AI 凭据和聊天数据。
- 备份要使用 SQLite 在线备份或在停止容器后复制数据库，**加密主密钥另行安全备份**。只恢复数据库但不恢复匹配的主密钥，会导致旧 Key 无法解密；程序不会静默生成新主密钥覆盖旧数据。
- 迁移/回滚时保留原数据卷，不要 `docker compose down -v`。删除 Key 后备份仍可能包含旧密文，必须在 AI 平台撤销 Key 才能让其真正失效。

升级源码部署：Windows `bot.cmd build` / Linux `./bot.sh build`（重建镜像并启动）；离线包用户需先取得包含此插件的新镜像包，再运行 `bot.cmd start` / `./bot.sh start`。日常用户改 AI 配置不用重启。菜单更新：Windows `menu.cmd sync` / Linux `./menu.sh sync`。C2C 和群面板用一个 `/ai` 入口替换原 `/random` 项，随机数功能仍能手动输入，FF14 仍只有一个面板入口。

## English quick reference

Any user may create multiple personas in a private QQ chat. Each persona has its own provider/key, manually selected model, name and prompt. Different users' personas can coexist in a group; one user may deploy only one of their personas to each group. A persona can be deployed to multiple groups. These are personas of the existing official QQ bot, not separate QQ bot accounts.

1. Private: `/ai new Name | Persona`, `/ai service glm` (or `qwen beijing`, `qwen singapore`, `qwen virginia`), `/ai model`, optionally `/ai model MODEL_ID`, then `/ai key YOUR_API_KEY`.
2. Private: `/ai roles`, `/ai select ROLE_ID`, `/ai publish 100`.
3. Target group: `/ai join INVITE_CODE`. Any member may submit it.
4. Private: `/ai confirm CONFIRMATION_CODE`; check the group tag, then `/ai accept CONFIRMATION_CODE`.
5. Default per-group behavior is `mention on + collect 0`: a matching name replies immediately. The persona owner may privately configure the exact grant with `/ai group-settings GRANT_ID mention on|off`, `/ai group-settings GRANT_ID collect 0`, or `/ai group-settings GRANT_ID collect MIN MAX` (1–20). Mention matching and collection are independent. Commands are excluded, and non-mention delivery still requires QQ full-message permission.

Private: `/ai model` lists the current allowlist and marks both the current and default recommended model, with a reason for each choice. `/ai model MODEL_ID` switches manually; `/ai model recommended` restores the default. A model switch keeps the key, persona and group grants but clears that persona's old conversation memory. It never silently falls back when free quota is exhausted. Other commands: `/ai usage`, `/ai limit 100`, `/ai grants`, `/ai group-settings GRANT_ID`, `/ai revoke GRANT_ID`, `/ai revoke all`, `/ai clear`, `/ai delete-key confirm`, `/ai delete-role ROLE_ID confirm`. `/ai copy-key OWN_ROLE_ID` explicitly copies your own other persona's provider/model/key; later rotations do not auto-sync. Collection buffers expire after 30 minutes. `mention off + collect 0` may call the provider for every ordinary group message and can consume tokens and balance very quickly; use a dedicated limited key and a conservative daily limit.

In a group, QQ admins, QQ owners and recognized bot superadmins can inspect settings with command hints using `/ginfo [page]`. AI rows contain only role names and IDs. They may remove one group role with `/ai remove ROLE_ID`; only the QQ owner or bot superadmin may remove `all`. Owners, providers, status, quota, API keys and persona text are not included.

All your personas share five private-chat requests/day and a total quota across groups. Requests are reserved atomically before calling the provider, including failures; no automatic paid retry or key/model fallback. Credentials are encrypted at rest, but QQ and the server operator can access them. Keep the database and its matching encryption key backed up securely, separately from distributed packages. Chat history is isolated per persona and per conversation.

GLM uses SSE internally and sends one QQ message only after the complete response arrives. Connection timeout: 10 seconds; read inactivity timeout: 90 seconds; total deadline: 120 seconds. Streaming does not eliminate provider queueing. Interrupted or malformed streams are discarded, without automatic retries. Errors distinguish connection, read-timeout and transfer failures without exposing credentials or raw provider errors. Qwen remains non-streaming.

GLM error 1305 means the model is busy, 1302 means account rate/concurrency limits, and 1113 means insufficient balance. Known codes are shown with fixed safe explanations, not raw upstream messages.
