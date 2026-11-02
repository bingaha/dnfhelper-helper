# dnfhelper-helper

DNF 助手 App「编年有礼」活动接口自动化。

> ## ⚠️ 说明
>
> 本仓库是**参考实现**，不是投入生产即用的成品。
>
> 代码逻辑是完整的 —— 扫码登录、任务完成、奖励领取全都实现了。
> 但接口参数、任务 ID、错误码都可能随活动版本变化，届时需要按
> [NOTES.md](NOTES.md) 里的技术细节自行调整。
>
> 用途是：给你或你的 AI 助手一份可参考的实现思路，帮你快速整理出自用的版本。

---

## 已实现的任务

| actionId | 名称 | exp | 频率 | 完成方式 |
|---|---|---|---|---|
| 001 | DNF助手签到 | 10 | 每日 | 直接领取 |
| 043 | 浏览1篇内容 | 5 | 每日 | 打开一篇动态 |
| 045 | 进入圈子详细页 | 4 | 每日 | 进圈子详情页 |
| 049 | 【周】查看地区排行榜 | 12 | 每周 | 上报动作 |
| 008 | 【周】分享助手周报 | 10 | 每周 | 上报动作 |
| 009 | 通关推荐地下城3次 | 24 | 每日 | ❌ 需游戏服侧真实事件 |
| 005 | 登录游戏 | 15 | 每日 | ❌ 需游戏服侧真实事件 |

**能做的 5 个任务可以 100% 纯接口完成。**

009 / 005 不是"还没做"，是**做不到**：活动服务端要等游戏服的真实回执，
上报接口对它们恒返回拒绝。这是接口设计的边界，不是实现遗漏。

## 使用方法

### 环境

```bash
pip install requests
```

Python 3.8+。除 `qq_login.py` 外，其余脚本只用标准库。

### 第一步：登录

一条命令完成——生成二维码、等你扫码、换取登录态、写出 `session.json`：

```bash
python login_new_account.py --qr
```

终端会打印二维码保存路径（`qr_login.png`），用手机 QQ 扫码并确认即可。
成功后自动写出 `session.json`（权限 0600）。

> 如果之前扫码过了、`qq_oauth.json` 还在，直接 `python login_new_account.py` 跳过出码。

### 第二步：检查

```bash
python daily.py --check
```

只读，不做任何写操作。输出当前任务状态、经验等级。确认凭证有效后再往下。

### 第三步：跑任务

```bash
python daily.py
```

签到 → 任务 → 礼包 → 汇总，全程幂等，可以重复跑。

退出码：`0` = 无待跟进，`1` = 有 pending（会逐条说明原因）。

### 单独调用某个接口

```bash
python peak_client.py top-info          # 活动等级/经验
python peak_client.py task-list         # 任务列表（默认视图）
python peak_client.py basic-list        # 礼包列表
python peak_client.py claim-basic-all   # 一键领礼包
python peak_client.py claim-task 043    # 单独领某个任务

python native_client.py probe           # 探测原生接口连通性
python native_client.py roles           # 列出当前 QQ 下所有游戏角色
python native_client.py browse          # 模拟"浏览一篇动态"（043）
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `daily.py` | 每日一键脚本，串起签到 → 任务 → 礼包 → 汇总 |
| `peak_client.py` | `dzhu.qq.com` H5 活动接口客户端 |
| `native_client.py` | `api.dnfhelper.qq.com` 原生接口客户端 |
| `login_new_account.py` | **登录入口**。`--qr` 一条龙，登录后自动填充角色字段 |
| `qq_login.py` | QQ 扫码链实现（被上面调用，也可单独跑） |
| `harvest_session.py` | 备用：从已有活动页 URL 恢复 `session.json` |
| `NOTES.md` | **技术细节**：签名算法、接口表、错误码对照 |
| `session.example.json` | 凭证模板 |

## 关于基础礼包

签到礼包和等级礼包需要 `session.json` 里带全角色字段，否则返回：

```
2009 角色错误，请点击页面的[切换角色]按钮，切换为当前QQ下的角色参与活动
```

**这些字段是自动填的** —— `login_new_account.py` 登录成功后会调
`/game/chatroles` 取当前账号的主角色，写进 `session.json`：

| 字段 | 取自 |
|---|---|
| `sRoleId` | `originalRoleId` |
| `uniqueRoleId` | `roleId` |
| `sPartition` | `serverId` |

两个 ID 是交叉对应的，填反就会报 2009。登录时终端会打印一行：

```
  角色   = 角色名 河南一区 Lv.115（共 2 个角色）
```

看到这行说明角色已就绪，礼包能领。

领完之后再跑会返回 `200031 当前没有可领取的礼包呢`，这是正常的幂等结果，
不是错误。

## 常见问题

**Q: `-30003 登录态失效`**

token 过期。重跑 `python login_new_account.py --qr` 重新扫码。

**Q: 能不能挂定时任务？**

可以。`daily.py` 是幂等的，重复跑不会重复领。建议每天一次。

**Q: 009 / 005 为什么不实现？**

需要游戏服侧的真实事件（通关地下城、登录游戏），活动服务端要等游戏服回执。
上报接口对这两个任务恒返回拒绝，埋点接口也推不动。接口层面无解。

**Q: 049 任务列表里看不到？**

049 只在特定的请求参数集下才返回。代码里 `merged_tasks()` 已经合并了两套视图，
直接用 `daily.py` 就不会漏。原理见 [NOTES.md §4](NOTES.md)。

## 如何让 AI 帮你整理

`NOTES.md` 记录了签名算法、错误码语义、每个任务对应的真实请求，
足以让 AI 在不需要重新分析协议的情况下接手。示例提问：

```
这是一个 DNF 助手活动自动化项目，包含 peak_client.py / native_client.py /
daily.py 和 NOTES.md。请阅读后：

1. 说明 HMAC-SHA1 签名怎么算，以及为什么 body 必须用 quote(safe='') 而不是 urlencode
2. 解释 getusertasklist 的两套任务视图为什么互斥
3. 检查 daily.py 的任务分支，指出哪些依赖了"服务端不校验完成状态"这类可能失效的前提
4. 帮我把新任务 NNN 接入进去
```

## 许可证

[GPL-3.0](LICENSE)。

## 免责

仅供学习与个人研究。请遵守腾讯的服务条款；不要用于批量操作他人账号、
商业牟利或其他损害服务提供方的行为。使用本项目所产生的一切后果由使用者自负。
