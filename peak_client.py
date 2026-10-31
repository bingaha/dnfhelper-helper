#!/usr/bin/env python3
"""
DNF 助手「编年有礼」活动接口客户端（dzhu.qq.com）

演示两件事：
  1. H5 活动接口的 HMAC-SHA1 签名怎么算
  2. 两套任务视图（getusertasklist 按请求参数集返回不同任务）

签名算法
--------
    sig = base64( HMAC_SHA1( "METHOD&" + g(path) + "&" + g(paramString), SECRET ) )

    paramString = 所有参数按名升序，拼 "k=v"，用 & 连接
    g(s)        = encodeURIComponent(s)，再把 ( ) ~ ! * ' 六个字符额外百分号编码
    path        = 请求行里的完整路径（GET 带 /peak 前缀，POST 带 /yoyo）

必需项：
    cRand       毫秒时间戳，参与签名
    tghappid    参与签名
    Clsq-Verify 请求头 = base64("token;uin;userId")

编码陷阱：body 必须用 quote(v, safe='')，不能用 urlencode() ——
urlencode 把空格编成 "+"，而服务端按原始报文校验签名，"+" 会导致
-90002 sig verification failed，必须 %20。

凭证
----
从 session.json 读取，不要硬编码。字段来源见 README。
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HOST = "https://dzhu.qq.com"
SECRET = "nKJH89hh@8yoHJ98y&IOhIUt9hbOh98ht"
TGHAPPID = "1000045"
APPID_TASK = "1000042"
UA = ("Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Version/4.0 Chrome/83.0 Mobile Safari/537.36 "
      "GH_QQConnect GameHelper_1006/2104130004")

SESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")


def load_session(path: str = SESS_FILE) -> dict:
    """读凭证。找不到就直接报错，绝不回落到内置的示例值。

    静默回落是危险的：会拿别人的 userId/token 去跑写接口，
    日志看起来正常，实际在操作另一个账号。
    """
    if not os.path.exists(path):
        raise SystemExit(
            f"缺少 {path}\n先跑：python login_new_account.py --qr 扫码登录")
    with open(path, encoding="utf-8") as f:
        sess = json.load(f)
    missing = [k for k in ("userId", "uin", "token") if not sess.get(k)]
    if missing:
        raise SystemExit(
            f"session.json 缺少必需字段：{missing}\n"
            "先跑：python login_new_account.py --qr 重新扫码")
    return sess


SESSION = load_session()

# 路由表：名字 -> (方法, 路径)
ROUTES = {
    "sign-list": ("GET", "/peak/list/sign"),
    "basic-list": ("GET", "/peak/list/basic"),
    "lottery": ("GET", "/peak/lottery/receive"),
    "sign-record": ("POST", "/yoyo/dnf/getusersigninrecord"),
    "task-list": ("POST", "/yoyo/dnf/getusertasklist"),
    "top-info": ("POST", "/yoyo/dnf/getuseractivitytopinfo"),
    "month-gifts": ("POST", "/yoyo/dnf/getmonthsigngifts"),
    "point-records": ("POST", "/yoyo/dnf/getuserpointrecords"),
    "claim-sign": ("GET", "/peak/send/sign"),
    "claim-basic": ("GET", "/peak/send/basic"),
    "claim-basic-all": ("GET", "/peak/batchSend/basic"),
    "claim-task": ("POST", "/yoyo/dnf/doactionincrexp"),
    "report-action": ("POST", "/yoyo/dnf/actionreport"),
}


def g(s: str) -> str:
    """等价于页面里的 g()：encodeURIComponent + 补编码 ( ) ~ ! * '"""
    t = urllib.parse.quote(s, safe="-_.!~*'()")
    for ch, rep in [("(", "%28"), (")", "%29"), ("~", "%7E"),
                    ("!", "%21"), ("*", "%2A"), ("'", "%27")]:
        t = t.replace(ch, rep)
    return t


def sign(method: str, path: str, params: dict) -> str:
    items = [f"{k}={params[k]}" for k in sorted(params) if params[k] is not None]
    payload = f"{method.upper()}&{g(path)}&{g('&'.join(items))}"
    digest = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def call(method: str, path: str, extra: dict | None = None) -> dict:
    params = dict(SESSION)
    params.update(extra or {})
    params["cRand"] = str(int(time.time() * 1000))
    params["tghappid"] = TGHAPPID
    params["sig"] = sign(method, path, params)

    verify = base64.b64encode(
        f"{params['token']};{params['uin']};{params['userId']}".encode()
    ).decode()
    headers = {
        "Clsq-Verify": verify,
        "Referer": "https://dzhu.qq.com/fe/dnf/calculation4.0/",
        "User-Agent": UA,
        "X-Requested-With": "com.tencent.gamehelper.dnf",
    }

    encoded = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                       for k, v in params.items())

    if method == "GET":
        req = urllib.request.Request(HOST + path + "?" + encoded,
                                     method="GET", headers=headers)
    else:
        body = encoded.encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(HOST + path, data=body, method=method,
                                     headers=headers)

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


TASK_EXTRA = {
    "accType": "qc",
    "cGameId": "1006",
    "cClientVersionCode": "2104130004",
    "getNavUaStr": "_QQConnect GameHelper_1006/2104130004",
}

# --- getusertasklist 的两套互斥视图 -----------------------------------------
#
# 服务端按**请求参数集**决定返回哪一套任务，两套不会同时出现 049 和 047：
#
#   默认视图（SESSION 全量字段）→ 含 047「浏览话题详细页」，不含 049
#   App 视图（下面这套精简字段）→ 含 049「查看地区排行榜」，不含 047
#
# 用默认视图查就永远看不到 049，这是最容易踩的坑。
TASK_LIST_KEYS = (
    "accessToken", "appOpenid", "gameId", "game_code", "openid", "sPartition",
    "sRoleId", "token", "uin", "uniqueRoleId", "userId",
)


def task_list_app() -> list:
    """用 App 的精确参数集查任务列表，能拿到 049「查看地区排行榜」。

    与默认视图的差别只有参数集（去掉 serverName/roleName/areaName/
    roleJob/roleLevel/appidTask 六个），服务端据此返回不同的任务视图。
    """
    core = {k: SESSION[k] for k in TASK_LIST_KEYS if k in SESSION}
    core.update({k: TASK_EXTRA[k] for k in
                 ("cGameId", "cClientVersionCode", "getNavUaStr")})
    saved = dict(SESSION)
    try:
        SESSION.clear()
        SESSION.update(core)
        return call("POST", "/yoyo/dnf/getusertasklist")["data"]["taskList"]
    finally:
        SESSION.clear()
        SESSION.update(saved)


def claim_task(action_id: str) -> dict:
    """领取任务奖励（给经验的，如 049「查看地区排行榜」）。

    这条路径与 /peak/* 不同：
      - POST /yoyo/dnf/doactionincrexp
      - 参数集是 core + accType/cGameId/cClientVersionCode/getNavUaStr
      - **不带** appidTask/serverName/roleName（多带会 sig 校验失败）
    """
    core = {k: v for k, v in SESSION.items()
            if k not in ("serverName", "roleName", "appidTask")}
    core.update(TASK_EXTRA)
    backup = dict(SESSION)
    try:
        SESSION.clear()
        SESSION.update(core)
        return call("POST", "/yoyo/dnf/doactionincrexp", {"actionId": action_id})
    finally:
        SESSION.clear()
        SESSION.update(backup)


def claim_basic_all(num: int = 1) -> dict:
    """「一键领取」——服务端单接口一次性领完已解锁的等级奖励。

    GET /peak/batchSend/basic，只需 num，不需要 amsid/mold。
    返回 sendNum 与 basicGifts 明细。
    """
    return call("GET", "/peak/batchSend/basic", {"num": str(num)})


def report_action(action_id: str,
                  referer: str = "https://dzhu.qq.com/fe/dnf/weekly/main") -> dict:
    """上报"完成了某任务动作"（分享周报 008、查看地区排行榜 049）。

    这个接口**不需要签名** —— body 很小，不带 cRand/tghappid/sig。
    返回 data:0 = 服务端认可并把任务推成「待领取」；data:1 = 不认可。
    之后调 claim_task() 领经验。

    注意：并非所有任务都吃这条通道。009/005 恒返回 data:1，
    因为它们需要游戏服侧的真实事件。
    """
    body = {
        "actionId": action_id,
        "gameId": "1006",
        "userId": SESSION["userId"],
        "token": SESSION["token"],
        "cGameId": "1006",
        "cClientVersionCode": "2104130004",
        "getNavUaStr": TASK_EXTRA["getNavUaStr"],
    }
    data = "&".join(f"{k}={v}" for k, v in body.items()).encode()
    verify = base64.b64encode(
        f"{SESSION['token']};{SESSION['uin']};{SESSION['userId']}".encode()
    ).decode()
    req = urllib.request.Request(
        HOST + "/yoyo/dnf/actionreport", data=data, method="POST",
        headers={
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "accept": "application/json",
            "origin": HOST,
            "x-requested-with": "com.tencent.gamehelper.dnf",
            "Clsq-Verify": verify,
            "referer": referer,
            "User-Agent": UA,
        })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def analysis(eid: str, mid: str = "10") -> dict:
    """H5 行为上报：POST /login/analysis（multipart，无需签名）。

    活动页的埋点接口，但后端会拿 eid（= 任务的 mEventId）去推进任务状态。
    durl 必须带整套凭证，后端从中解析身份。
    """
    boundary = "----WebKitFormBoundary" + "".join(
        __import__("random").choice(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        for _ in range(16))
    fields = [
        ("mid", mid),
        ("eid", eid),
        ("durl", SESSION.get("h5url", "")),
        ("qq", SESSION["uin"]),
        ("game", SESSION.get("url_subGameId", "10014")),
        ("bd", "0"),
        ("qid", "dzhu"),
        ("aid", SESSION["userId"]),
        ("from", "mobile"),
        ("encodeOpenId", "0"),
        ("encodeQQ", "0"),
    ]
    parts = []
    for k, v in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
    parts.append(f"--{boundary}--\r\n")
    data = "".join(parts).encode("utf-8")
    verify = base64.b64encode(
        f"{SESSION['token']};{SESSION['uin']};{SESSION['userId']}".encode()).decode()
    req = urllib.request.Request(
        HOST + "/login/analysis", data=data, method="POST",
        headers={
            "content-type": f"multipart/form-data; boundary={boundary}",
            "accept": "application/json",
            "origin": HOST,
            "referer": SESSION.get("h5url", HOST + "/fe/dnf/calculation4.0/"),
            "x-requested-with": "com.tencent.gamehelper.dnf",
            "clsq-verify": verify,
            "User-Agent": UA,
        })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def claim_sign(day: str | None = None) -> dict:
    """领取某天的签到奖励。day 形如 2026-10-31，默认今天。

    先查本月签到礼包拿当天 amsid（即 sLbcode），再调 /peak/send/sign。
    未绑定游戏角色的号会返回 2009「角色错误」。
    """
    data = call("POST", "/yoyo/dnf/getmonthsigngifts")["data"]
    if day is None:
        entry = next((v for v in data.values() if v.get("isToday")), None)
    else:
        entry = data.get(day)
    if not entry:
        raise SystemExit(f"找不到 {day or '今天'} 的签到礼包")
    sdays = entry["sDays"]
    return call("GET", "/peak/send/sign", {
        "amsid": entry["sLbcode"],      # 这里 amsid 用的是 sLbcode（IEGAMS-...）
        "date": f"{sdays[:4]}-{sdays[4:6]}-{sdays[6:]}",
        "num": "1",
        "serverName": SESSION.get("serverName", ""),
        "roleName": SESSION.get("roleName", ""),
        "exNum": "1",
    })


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "top-info"
    if name not in ROUTES:
        print(f"可用命令：{', '.join(ROUTES)}", file=sys.stderr)
        return 2
    if name == "report-action":
        print(json.dumps(report_action(sys.argv[2] if len(sys.argv) > 2 else "008"),
                         ensure_ascii=False, indent=2))
        return 0
    if name == "claim-task":
        print(json.dumps(claim_task(sys.argv[2] if len(sys.argv) > 2 else "049"),
                         ensure_ascii=False, indent=2))
        return 0
    if name == "claim-basic-all":
        print(json.dumps(claim_basic_all(), ensure_ascii=False, indent=2))
        return 0
    if name == "claim-sign":
        print(json.dumps(claim_sign(sys.argv[2] if len(sys.argv) > 2 else None),
                         ensure_ascii=False, indent=2))
        return 0
    method, path = ROUTES[name]
    print(json.dumps(call(method, path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
