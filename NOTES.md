# 技术细节

**DNF 助手「编年有礼」接口分析笔记。只讲结论和参数，不含排查过程。**

---

## 1. 两条接口线

| 线 | 域名 | 签名 | 用途 |
|---|---|---|---|
| H5 活动线 | `dzhu.qq.com` | HMAC-SHA1，必需 | 签到、任务列表、领经验、礼包 |
| 原生 App 线 | `api.dnfhelper.qq.com` | **无签名** | 浏览类任务、角色列表、社区 |

两条线的凭证是同一套（`userId` / `token` / `uin` 通用），但请求形态完全不同。

---

## 2. H5 活动线签名（HMAC-SHA1）

### 算法

```python
sig = base64( HMAC_SHA1( "METHOD&" + g(path) + "&" + g(paramString), SECRET ) )
```

- `paramString`：所有参数按**参数名升序**排序，拼成 `k=v`，用 `&` 连接
- `g(s)`：等价于 JS 的 `encodeURIComponent`，但 `( ) ~ ! * '` 六个字符要额外百分号编码
- `path`：请求行里的完整路径（GET 带 `/peak` 前缀，POST 带 `/yoyo`）
- `SECRET`：`nKJH89hh@8yoHJ98y&IOhIUt9hbOh98ht`（活动页 JS 里的硬编码常量）

```python
def g(s):
    t = urllib.parse.quote(s, safe="-_.!~*'()")
    for ch, rep in [("(", "%28"), (")", "%29"), ("~", "%7E"),
                    ("!", "%21"), ("*", "%2A"), ("'", "%27")]:
        t = t.replace(ch, rep)
    return t
```

### 必须参与签名的项

| 参数 | 说明 |
|---|---|
| `cRand` | 毫秒时间戳 |
| `tghappid` | 固定 `1000045` |

### 请求头

```
Clsq-Verify: base64("<token>;<uin>;<userId>")
Referer:     https://dzhu.qq.com/fe/dnf/calculation4.0/
User-Agent:  ...GH_QQConnect GameHelper_1006/2104130004
X-Requested-With: com.tencent.gamehelper.dnf
```

### 编码坑（最关键的一条）

body 必须用 `quote(v, safe='')`，**不能用 `urlencode()`**。

`urlencode` 把空格编码成 `+`，而服务端按**原始报文**校验签名，
`+` 会导致 `-90002 sig verification failed`，必须用 `%20`。

---

## 3. 原生线的加解密：不需要解

### 结论

`api.dnfhelper.qq.com` 的 App 请求体是 `application/octet-stream` 密文，
但这只是**客户端行为**。服务端**不强制**加密：用普通的
`application/x-www-form-urlencoded` 明文发过去，同样能正确解析。

唯一的硬要求是把身份字段放进 body：

```
userId=...&token=...&uin=...
```

所以完整的加解密分析不需要做。

### 请求头

```
host:            api.dnfhelper.qq.com
x-online-host:   api.dnfhelper.qq.com
content-encrypt: （空）
accept-encrypt:  （空）
gh-header:       2-1-1006-2104130004-<userId>
content-type:    application/x-www-form-urlencoded
user-agent:      Dalvik/2.1.0 (Linux; U; Android 11; sdk_gphone_x86_64 ...)
accept-encoding: gzip
```

响应是 gzip（按 `content-encoding` 判断后解压）。

### 设备指纹（必须带全）

每个原生请求都要带一整套设备字段，**缺了就报 `-20011 参数异常`**：

```
cGzip=1  cDevicePPI=420  cEnableTeenMode=0  cGameId=1006  cDeviceImei=0
cRfixPatch=(空)  cDeviceScreenHeight=2400  cDeviceCPU=armeabi-v7a$armeabi
cDeviceSP=(空)  cUin=<uin>  cSystemVersionCode=30  cDeviceNet=WIFI
cGameChannelId=(空)  cClientVersionCode=2104130004  gameId=1006
cChannelId=(空)  cDeviceMem=5939  dstType=1  userId=<userId>
cDeviceMac=00-00-00-00-00-00  token=<token>  cCurrentGameId=10014
cRand=<毫秒时间戳>  cDeviceScreenWidth=1080
cDeviceModel=google sdk_gphone_x86_64  cClientVersionName=4.13.0
cSystem=android  cDeviceId=<设备ID>  cSystemVersionName=11
cLoginType=qqConnect
```

`cDeviceId` 跨会话稳定，可复用同一个值。

### 关于密文格式（如果要解）

虽然不需要，但记录一下特征，避免走弯路：

- 密文 `postData` 是 base64，解码后长度通常是 4 字节对齐
- **不是**裸 AES/ECB/CBC 分组模式；也不是标准 XXTEA
- 前 4 字节随机，没有固定魔数
- 用 token/userId/uin/deviceId/gh-header 的 md5/sha/拼接做 key 穷举，零命中

说明 key 要么是二进制常量、要么由服务端下发或更复杂的派生参与。
**但这条路不必走** —— 见上文结论。

---

## 4. 任务系统

### 状态语义

| mStatus | 含义 |
|---|---|
| 0 | 未完成 |
| 2 | 待领取（做完了没领） |
| 1 | 已领取 |

### 两套互斥的任务视图（重要）

`POST /yoyo/dnf/getusertasklist` 返回哪一套任务，**由请求参数集决定**：

| 视图 | 参数集 | 返回的任务 |
|---|---|---|
| 默认 | SESSION 全量（含 `serverName`/`roleName`/`areaName`/`roleJob`/`roleLevel`/`appidTask`） | 001 043 045 **047** 008 009 005 |
| App | 只带 11 个身份字段 + `cGameId`/`cClientVersionCode`/`getNavUaStr`，共 14 个 | 001 **049** 043 045 008 009 005 |

**049 和 047 互斥**，不会同时出现。用默认视图查就永远看不到 049，
这是最容易踩的坑 —— 会误以为任务不存在。

App 视图的字段集：

```python
TASK_LIST_KEYS = (
    "accessToken", "appOpenid", "gameId", "game_code", "openid",
    "sPartition", "sRoleId", "token", "uin", "uniqueRoleId", "userId",
)
# 再加 cGameId / cClientVersionCode / getNavUaStr
```

多带那 6 个字段就会掉回默认视图，049 直接消失。

### 各任务的完成方式

| actionId | 判定请求 | 说明 |
|---|---|---|
| 001 | 无（直接 `claim-task`） | 服务端对 001 不校验完成状态，`mStatus=0` 也能领到 |
| 043 | `POST /moment/detail` `momentId=<id>` | **必须真开一篇动态**，只发发现页列表不够 |
| 045 | `POST /circle/detail` + `/circle/momentlist` | 圈子详情 + 动态列表 |
| 049 | `POST /yoyo/dnf/actionreport` `actionId=049` | 无签名明文通道 |
| 008 | `POST /yoyo/dnf/actionreport` `actionId=008` | 同上 |
| 047 | —— | 脏数据，`claim` 报 `-70007 非法任务`，App 里也不显示 |
| 009/005 | 游戏服侧事件 | 接口层无法替代 |

### 043 的细节（最容易做错的一个）

App 打开「发现」页的真实请求序列：

```
/circle/getrecocirclelist      ┐
/game/chatgroups               ├ 首屏三连（并发）
/moment/hottopiclist           ┘
/moment/hottopiclist             滚动再拉一次
/moment/detail                   点开一篇  ← 这一步才是 043 的判定
```

只发前三步（把"首屏加载"当成"浏览"），`mStatus` 会一直是 0。

`moment/detail` 的参数名**必须**是 `momentId`：

| 参数名 | 结果 |
|---|---|
| `momentId` | `returnCode 0` |
| `id` | `-20011 参数异常` |
| `oMomentId` | `-20011 参数异常` |

`momentId` 从 `/moment/hottopiclist` 的 `data.list[0].momentList[0].momentId` 取。

### `actionreport` 语义

```
POST /yoyo/dnf/actionreport
actionId=<id>&gameId=1006&userId=..&token=..&cGameId=1006
&cClientVersionCode=2104130004&getNavUaStr=..
```

**不需要签名**（不带 `cRand`/`tghappid`/`sig`），只需 `Clsq-Verify` 头。

返回值：

- `data:0` = 服务端认可，任务推到 `mStatus=2`
- `data:1` = 不认可

这里能看出服务端按 actionId 分了不同通道：008/049 吃这条，009/005 恒返回 `data:1`。

---

## 5. 角色与绑定的两套 ID

### 角色列表：用 `/game/chatroles`

```
POST api.dnfhelper.qq.com/game/chatroles
gameId=10014  + 完整设备参数
```

返回 `data.roles[]`，每个角色有：

```json
{
  "roleId": "3612345678",
  "roleName": "角色名",
  "level": 112,
  "areaId": "35",
  "serverId": 43,
  "serverName": "河南二区",
  "areaName": "河南",
  "roleJob": "极诣·阿修罗",
  "isMainRole": true,
  "uin": "...", "userId": "...",
  "originalRoleId": "81234567"
}
```

注意 `roleId` 和 `originalRoleId` 是**一对交叉映射**：`roleId` 就是活动页里的
`uniqueRoleId`，`originalRoleId` 就是活动页里的 `roleId`（= `sRoleId`）。
详见 [§5.3](#uniqueroleid-的来源就是-roleid)。

**注意**：`/game/chatroleslist` 是另一个接口，未绑定角色时返回 `roles: []`
且 `isAuthed: false`，容易误判成"这个账号没有角色"。查角色用 `chatroles`。

### `sPartition` 等于 `serverId`，不是 `areaId`

H5 活动 URL 里传的是 `serverId=43`，接口里叫 `sPartition`。
两个字段名不同，别填错成 `areaId=35`。

### `uniqueRoleId` 的来源：就是 `roleId`

这是活动侧自己的 ID，和 `roleId` **不是同一个字段**，但**可以从接口拿到**：

```
uniqueRoleId = /game/chatroles 返回的 roleId
```

两个 ID 是**交叉对应**的，很容易填反：

| 活动页 URL 字段 | `/game/chatroles` 字段 | 示例 |
|---|---|---|
| `roleId`（= `sRoleId`） | `originalRoleId` | 81234567 |
| `uniqueRoleId` | `roleId` | 3612345678 |

活动页 URL 的实际形态：

```
https://dzhu.qq.com/fe/dnf/calculation4.0?
  serverName=..&appid=..&areaName=..&roleName=..&gameName=..&nickname=..
  &isMainRole=1&roleJob=..&areaId=35&roleId=81234567&uin=..&toUin=..
  &serverId=27&token=..&uniqueRoleId=3612345678&acctype=qc&roleLevel=115&userId=..
```

对照同一次会话的 `/game/chatroles` 响应即可确认：`roleId=3612345678`、
`originalRoleId=81234567`。两者位数相近但含义不同，填反就是 `2009 角色错误`。

`login_new_account.py` 登录成功后会自动调 `/game/chatroles` 把这几个字段
填进 `session.json`，不需要手工补。

---

## 6. 礼包接口与错误码

### 接口

| 路径 | 方法 | 参数 | 说明 |
|---|---|---|---|
| `/peak/list/basic` | GET | 无 | 礼包列表（`basic1List` 单人档 / `basic2List` 搭档档） |
| `/peak/batchSend/basic` | GET | `num=1` | 一键领取所有已解锁档位 |
| `/peak/send/basic` | GET | `amsid`(=sLbCode), `num`, `isLock`, `iLbSel1`, `mold` | 领单个 |
| `/peak/list/sign` | GET | 无 | 签到日历 |
| `/peak/send/sign` | GET | `amsid`(=sLbcode), `date`, `num`, `serverName`, `roleName`, `exNum` | 领签到礼包 |
| `/yoyo/dnf/getmonthsigngifts` | POST | 无 | 本月签到礼包 + 当天 `signStatus`/`giftStatus` |

礼包字段 `sLbCode` 和 `sLbCodeReal` 的区别：

- `sLbCode`：形如 `basic_0001`，接口用的是它（叫 `amsid`）
- `sLbCodeReal`：形如 `IEGAMS-1056528-...`，**为空时表示这是活动内积分**（如"年史碎片"），
  非空时是真实 CDK，要发到游戏角色

### 常见错误码

| code | 含义 | 处理 |
|---|---|---|
| `200` | 成功 | |
| `2009` | 角色错误，请切换为当前QQ下的角色 | 角色字段为空，或 `sRoleId`/`uniqueRoleId` 填反 |
| `200031` | 当前没有可领取的礼包呢 | 已领完，属正常幂等结果 |
| `200054` | 角色绑定异常 | 有角色但活动侧没绑定记录 |
| `200005` | 领取礼包失败：系统错误 | 角色字段被接受但下游出错 |
| `12` | 系统错误 | 路径存在但参数不对（常用于探测接口是否存在） |
| `-90002` | sig verification failed | 签名算错，检查 `quote(safe='')` |
| `-30003` | 登录态失效 | token 过期，重新登录 |
| `-70007` | 非法任务 | 该 actionId 是脏数据 |

### 领礼包必须带全角色字段

`/peak/send/sign` 和 `/peak/batchSend/basic` 都依赖 `session.json` 里的角色字段，
缺任一个就是 `2009 角色错误`：

| 字段 | 来源 |
|---|---|
| `sRoleId` | `chatroles.originalRoleId` |
| `uniqueRoleId` | `chatroles.roleId` |
| `sPartition` | `chatroles.serverId`（**不是** `areaId`） |
| `serverName` / `roleName` | `chatroles.serverName` / `roleName` |

`login_new_account.py` 登录后自动填，见 §5。

### 签到状态是两个独立维度

```
signStatus: 0=未签到  1=已签到
giftStatus: 0=未领    1=已领
```

**不要混在一起判断。** 常见情况是 `signStatus=1, giftStatus=0` ——
签到成功了但礼包因为角色问题领不了，这时报「签到未完成」是错的。

### 签到埋点

如果 `signStatus=0`，可以试发签到埋点（任务 001 的 `mEventId`）：

```
POST /login/analysis   (multipart，无签名)
mid=10&eid=21437&durl=<H5完整URL>&qq=<uin>&game=10014&bd=0
&qid=dzhu&aid=<userId>&from=mobile&encodeOpenId=0&encodeQQ=0
```

`eid` 取任务的 `mEventId`。`durl` 必须带全套凭证，后端从中解析身份。

**埋点是异步落库的**：发完立刻查 `getmonthsigngifts`，`signStatus` 大概率
还是 0。固定 `sleep` 一两秒再查一次会误报「签到未成功」，必须轮询到翻转
（实测 5~15 秒不等）。同理，`actionreport` 和浏览类任务把 `mStatus`
推到 2 也是异步的，`daily.py` 里统一用 `wait_sign()` / `wait_status()` 轮询。

---

## 7. 登录链

### 助手登录（`/user/login`）

```
POST api.dnfhelper.qq.com/user/login
+ 完整设备参数
+ appOpenid / openid / accessToken / loginType=qqConnect / uin / cUin
```

坑：

| 现象 | 原因 |
|---|---|
| `-20002 服务器繁忙` | **不是繁忙**，是缺 `loginType=qqConnect`（小写 `l`，不是 `cLoginType`） |
| `-20001 服务器繁忙` | `uin`/`cUin` 是上个账号的残留；或误带了 `sdkp`/`sdkv`/`pf` |

`userId` / `token` 是登录成功后才有的，请求前要清掉旧值。

### QQ 扫码链

```
GET  openmobile.qq.com/oauth2.0/m_authorize    → 含 xlogin 地址的 HTML
GET  xlogin 地址                               → 建 cookie
GET  xui.ptlogin2.qq.com/ssl/ptqrshow          → 二维码 PNG + qrsig
GET  xui.ptlogin2.qq.com/ssl/ptqrlogin         → 轮询
```

固定参数：`appid=716027609`、`daid=381`、`pt_3rd_aid=1105742785`。

三个坑：

1. **`pt_openlogin_data` 必须是 xlogin 页面的原始 query 串**（含服务端回显的 `sign`）。
   自己拼一份、或省略 `sign`，会报 `sign of app is error(100044)`，
   或退化成 `check_sig` 分支 —— 只回写 skey cookie，拿不到 `access_token`。

2. `sign` 由 App SDK 用内置密钥算，但服务端**只把它原样回显**到 xlogin 的 query 里，
   不校验它和 `time` 的关系。钉一份已知可用的值即可。

3. `ptqrtoken = hash33(qrsig)`，不是直接拿 `qrsig`：

```python
def hash33(s):
    e = 0
    for ch in s:
        e += (e << 5) + ord(ch)
    return 2147483647 & e
```

轮询状态码：`66` 未扫码 / `67` 已扫码待确认 / `65` 已失效 / `0` 成功 / `1` 取消。

---

## 8. 其他接口备忘

`/near/*`（地区排行榜相关）的明文可用性：

| 路径 | 状态 |
|---|---|
| `/near/getalllocations` | ✅ 明文可用 |
| `/near/getlbsprofessionlist` | ✅ 可用，需职业过滤参数 |
| `/near/getlbsranklist` | ❌ `-20011`，参数未逆出 |

**但这不影响 049 任务** —— 049 的判定是 `actionreport`，
`/near/*` 只负责渲染榜单数据。
