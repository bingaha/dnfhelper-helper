#!/usr/bin/env python3
"""
api.dnfhelper.qq.com 原生接口客户端

核心发现：**服务端不强制加密**。
App 发的是 application/octet-stream 密文，但只要用普通
application/x-www-form-urlencoded 明文发过去，服务端同样解析。
唯一的硬要求是把身份字段（userId/token/uin）放进 body。

请求头：
    gh-header:      2-1-1006-2104130004-<userId>
    content-type:   application/x-www-form-urlencoded
    user-agent:     Dalvik/2.1.0 (...)
    content-encrypt / accept-encrypt: 空

设备指纹
--------
每个原生请求都要带一整套设备字段（见 _device_params）。缺了就报
-20011 参数异常。这些值本身不敏感，但不能省。

用途示例
--------
    043 浏览1篇内容       → browse_one_moment()
    045 进入圈子详细页    → circle_detail_full() + circle_momentlist_full()
    角色列表              → chat_roles()
"""
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://api.dnfhelper.qq.com"
UA = ("Dalvik/2.1.0 (Linux; U; Android 11; sdk_gphone_x86_64 "
      "Build/RSR1.240422.006)")
GAME_ID = "10014"          # 子游戏 id（DNF 端游）
C_GAME_ID = "1006"
DEVICE_ID = "c21b20b3b6c22fdf"   # cDeviceId，跨会话稳定

# 本机若配了系统代理会干扰直连，显式清空
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")

# 登录流程（login_new_account.py --qr）需要 import 本模块来拿 _device_params，
# 那时 session.json 还不存在 —— 所以这里不能硬性失败，否则装不进扫码这步。
# 没有凭证时 S 为空字典，实际发请求前由 require_auth() 兜底报错。
if os.path.exists(SESS_FILE):
    with open(SESS_FILE, encoding="utf-8") as _f:
        S = json.load(_f)
    _AUTH = {"userId": S["userId"], "token": S["token"], "uin": S["uin"]}
else:
    S = {}
    _AUTH = {}


def require_auth() -> None:
    """发带身份的请求前调用；未登录时给出明确指引。"""
    missing = [k for k in ("userId", "token", "uin") if not S.get(k)]
    if missing:
        raise SystemExit(
            f"缺少 {SESS_FILE} 或字段不全（{missing}）。"
            "先跑 python login_new_account.py --qr 扫码登录。")


def use_auth(user_id: str, token: str, uin: str) -> None:
    """用内存里的凭证调接口，不落盘。

    登录脚本刚换到 userId/token 时 session.json 还不存在，
    这一步调 /game/chatroles 要用它。
    """
    S.clear()
    S.update({"userId": str(user_id), "token": str(token), "uin": str(uin)})
    _AUTH.clear()
    _AUTH.update(S)


def gh_header() -> str:
    return f"2-1-{C_GAME_ID}-2104130004-{S.get('userId', '')}"


def post(path: str, params: dict | None = None, auth: bool = True,
         timeout: int = 20) -> dict:
    """POST 一个明文表单请求，返回解析后的 JSON。"""
    body = dict(params or {})
    if auth:
        require_auth()
        body.update(_AUTH)
    data = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in body.items()
    ).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={
            "host": "api.dnfhelper.qq.com",
            "x-online-host": "api.dnfhelper.qq.com",
            "accept-encrypt": "",
            "content-encrypt": "",
            "gh-header": gh_header(),
            "user-agent": UA,
            "content-type": "application/x-www-form-urlencoded",
            "accept-encoding": "gzip",
        })
    with _OPENER.open(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"_raw": raw[:200].decode("utf-8", "replace"), "_len": len(raw)}


def _device_params(game_id: str = C_GAME_ID) -> dict:
    """App 每个原生请求都会带的设备指纹字段。

    服务端**会校验**这些字段，缺了就是 -20011 参数异常。
    值来自真机实际请求；cRand 每次现取毫秒时间戳。
    """
    return {
        "cGzip": "1", "cDevicePPI": "420", "cEnableTeenMode": "0",
        "cGameId": C_GAME_ID, "cDeviceImei": "0", "cRfixPatch": "",
        "cDeviceScreenHeight": "2400",
        "cDeviceCPU": "armeabi-v7a$armeabi", "cDeviceSP": "",
        "cUin": S.get("uin", ""), "cSystemVersionCode": "30",
        "cDeviceNet": "WIFI", "cGameChannelId": "",
        "cClientVersionCode": "2104130004", "gameId": game_id,
        "cChannelId": "", "cDeviceMem": "5939", "dstType": "1",
        "userId": S.get("userId", ""), "cDeviceMac": "00-00-00-00-00-00",
        "token": S.get("token", ""), "cCurrentGameId": GAME_ID,
        "cRand": str(int(time.time() * 1000)),
        "cDeviceScreenWidth": "1080",
        "cDeviceModel": "google sdk_gphone_x86_64",
        "cClientVersionName": "4.13.0", "cSystem": "android",
        "cDeviceId": DEVICE_ID, "cSystemVersionName": "11",
        "cLoginType": "qqConnect",
    }


def _full_post(path: str, extra: dict | None = None,
               game_id: str = C_GAME_ID) -> dict:
    p = _device_params(game_id)
    p.update(extra or {})
    return post(path, p, auth=False)


# ---- 角色 ----

def chat_roles() -> list:
    """当前 QQ 下所有已绑定的游戏角色。

    注意用的是 /game/chatroles，**不是** /game/chatroleslist ——
    后者在未绑定角色时返回 roles 为空，容易误判成"没有角色"。

    返回字段里没有 uniqueRoleId（那是 H5 活动侧自己的 ID）。
    """
    r = _full_post("/game/chatroles", {"gameId": GAME_ID}, game_id=GAME_ID)
    return (r.get("data") or {}).get("roles") or []


# ---- 发现页 / 动态（043）----

def circle_reco() -> dict:
    """发现页推荐圈子。"""
    return _full_post("/circle/getrecocirclelist")


def chatgroups() -> dict:
    """发现页群聊。"""
    return _full_post("/game/chatgroups", {"apiVersion": "2", "gameId": GAME_ID})


def hottopiclist() -> dict:
    """热门话题列表。data 结构是 {"list": [...]}，不是裸数组。"""
    return _full_post("/moment/hottopiclist", {"gameId": GAME_ID})


def moment_detail(moment_id: str) -> dict:
    """读一篇动态详情 —— 043「浏览1篇内容」的真正判定请求。

    参数名必须叫 `momentId`；叫 id / oMomentId 一律 -20011。

    只发列表三连（getrecocirclelist/chatgroups/hottopiclist）**不够**，
    服务端要看到确实打开了一篇内容才把 043 置为可领取。
    这是该任务最容易踩的坑：把"首屏加载"当成了"浏览"。
    """
    return _full_post("/moment/detail", {"momentId": str(moment_id),
                                         "gameId": GAME_ID})


def browse_one_moment() -> dict:
    """043 一条龙：取热门话题流，再打开第一篇动态。

    顺序与 App 实际请求一致：先取列表再读详情。
    """
    top = hottopiclist()
    try:
        moment = top["data"]["list"][0]["momentList"][0]
        mid = moment["momentId"]
    except (KeyError, IndexError, TypeError):
        return {"hottopiclist": top.get("returnCode"), "detail": None}
    det = moment_detail(mid)
    return {"hottopiclist": top.get("returnCode"), "momentId": mid,
            "detail": det.get("returnCode")}


# ---- 圈子（045）----

def circle_detail(cle_id: str = "4716") -> dict:
    """圈子详情。"""
    return post("/circle/detail", {"gameId": GAME_ID, "cleId": cle_id})


def circle_talent(cle_id: str = "4716") -> dict:
    """圈子达人榜（进详情页时并发发出）。"""
    return post("/circle/getcircletalent", {"gameId": GAME_ID, "cleId": cle_id})


def circle_momentlist(cle_id: str = "4716", **kw) -> dict:
    """圈子动态列表。"""
    p = {"gameId": GAME_ID, "tagId": "0", "fromTime": "0", "fromId": "0",
         "cleId": cle_id, "fromIds": "", "labels": "", "sortType": "2",
         "page": "", "apiVersion": "1", "cornerVersion": "v2"}
    p.update(kw)
    return post("/circle/momentlist", p)


def circle_detail_full(cle_id: str = "4716") -> dict:
    """带设备参数的圈子详情（045 判定请求）。"""
    return _full_post("/circle/detail", {"cleId": cle_id, "gameId": GAME_ID})


def circle_momentlist_full(cle_id: str = "4716", **kw) -> dict:
    """带设备参数的圈子动态列表（045 判定主请求）。"""
    p = {"cleId": cle_id, "tagId": "0", "fromTime": "0", "fromId": "0",
         "fromIds": "", "labels": "", "sortType": "2", "page": "",
         "apiVersion": "1", "cornerVersion": "v2"}
    p.update(kw)
    return _full_post("/circle/momentlist", p, game_id=GAME_ID)


# ---- 话题（047）----

def topic_momentlist(topic_id: str, role_id: str = "", **kw) -> dict:
    """话题动态列表。

    App 进话题页会连发两条：先 sortType=100 + roleId=<uniqueRoleId>，
    再 sortType=2 + roleId=0 取列表。
    """
    base = {"fromTime": "0", "fromId": "0", "topicId": str(topic_id),
            "apiVersion": "1", "cornerVersion": "v2"}
    first = dict(base, roleId=str(role_id or S.get("uniqueRoleId", "")),
                 sortType="100")
    second = dict(base, fromIds="", sortType="2", roleId="0", page="")
    first.update(kw)
    second.update(kw)
    return {"head": _full_post("/moment/topicmomentlist", first),
            "list": _full_post("/moment/topicmomentlist", second)}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "probe":
        for name, fn in [
            ("circle/getrecocirclelist", circle_reco),
            ("game/chatgroups", chatgroups),
            ("moment/hottopiclist", hottopiclist),
            ("circle/detail", circle_detail_full),
            ("circle/momentlist", circle_momentlist_full),
        ]:
            r = fn()
            flag = "OK " if r.get("returnCode") == 0 else "ERR"
            print(f"{flag} {name:26} code={r.get('returnCode')} "
                  f"{str(r.get('returnMsg'))[:40]}")
    elif cmd == "roles":
        for x in chat_roles():
            print(f"{x.get('roleId')}  Lv.{x.get('level')}  "
                  f"{x.get('serverName')}  {x.get('roleName')}  "
                  f"{x.get('roleJob')}")
    elif cmd == "browse":
        print(json.dumps(browse_one_moment(), ensure_ascii=False))
    else:
        print(json.dumps(post("/" + cmd.lstrip("/"), {}), ensure_ascii=False)[:800])
