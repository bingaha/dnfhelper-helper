#!/usr/bin/env python3
"""
QQ 扫码登录：纯脚本走完 m_authorize → xlogin → ptqrshow → ptqrlogin，
拿到 openid / access_token。

⚠ 这是**思路示例**，不是开箱即用的脚本。
   appkey / sign 等参数与 App 版本、appid 绑定，换版本大概率失效。
   阅读价值大于运行价值 —— 重点是理解这条链和那几个坑。

链路
----
    GET openmobile.qq.com/oauth2.0/m_authorize   → 返回带 xlogin 地址的 HTML
    GET xlogin 地址                              → 建立 pt_login_sig 等 cookie
    GET xui.ptlogin2.qq.com/ssl/ptqrshow         → 二维码 PNG + qrsig
    GET xui.ptlogin2.qq.com/ssl/ptqrlogin        → 轮询，成功时回 openid+token

参数固定为 DNF 助手的 appid：
    appid=716027609  daid=381  pt_3rd_aid=1105742785

三个关键坑
----------
1. `pt_openlogin_data` 必须是 xlogin 页面的**原始 query 串**（含服务端回显的
   sign）。自己拼一份、或省略 sign，都会报 `sign of app is error(100044)`，
   或者退化成 check_sig 分支 —— 只回写 skey cookie，拿不到 access_token。

2. `sign` 是 App SDK 用内置密钥算的。但服务端**只把它原样回显**到 xlogin 的
   query 里，并不校验它和 time 的关系。所以钉一份已知可用的值即可。

3. `ptqrtoken = hash33(qrsig)`，不是直接拿 qrsig。

用法
----
    python qq_login.py qr     # 出二维码，存 qr_login.png，然后轮询等扫码
    python qq_login.py poll   # 单独轮询一次（用 qr_state.json 里的状态）
"""
import json
import os
import random
import re
import sys
import time
import urllib.parse

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_PNG = os.path.join(BASE_DIR, "qr_login.png")
QR_STATE = os.path.join(BASE_DIR, "qr_state.json")
LOGIN_OUT = os.path.join(BASE_DIR, "qq_oauth.json")

APPID = "716027609"
DAID = "381"
PT_3RD_AID = "1105742785"

# m_authorize 的 sign。服务端只回显、不校验（见文件头坑 2）。
# 换 appid 或 App 大版本时需要重新取一份。
M_AUTHORIZE_SIGN = "2d8d386a605ac87b47c297f3cabf4344"

UA = ("Mozilla/5.0 (Linux; Android 11; sdk_gphone_x86_64 Build/RSR1.240422.006; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 "
      "Mobile Safari/537.36")

# 不走系统代理（避免被本机代理软件拦截）
S = requests.Session()
S.trust_env = False
S.headers.update({"User-Agent": UA})


def hash33(s: str) -> int:
    """QQ 的 ptqrtoken 算法：e = e*33 + ord(c)，取低 31 位。"""
    e = 0
    for ch in s:
        e += (e << 5) + ord(ch)
    return 2147483647 & e


def step_qr() -> dict:
    """出二维码。三步顺序不能变。"""
    # 1) m_authorize —— 必须带 sign，服务端会把它回显进 xlogin 的 query
    p = {"cancel_display": "1", "sdkp": "a", "display": "mobile", "format": "json",
         "sdkv": "3.5.14.lite", "response_type": "token", "status_os": "11",
         "client_id": PT_3RD_AID, "switch": "1", "status_version": "30",
         "show_download_ui": "true", "pf": "openmobile_android", "scope": "all",
         "compat_v": "1", "status_machine": "sdk_gphone_x86_64", "style": "qr",
         "time": str(int(time.time())), "redirect_uri": "auth://tauth.qq.com/",
         "sign": M_AUTHORIZE_SIGN}
    r = S.get("https://openmobile.qq.com/oauth2.0/m_authorize?"
              + urllib.parse.urlencode(p), timeout=20)
    m = re.search(r'src\s*=\s*"([^"]+)"', r.text)
    if not m:
        raise SystemExit(f"m_authorize 没返回 xlogin 地址，HTTP {r.status_code}")
    xlogin_url = m.group(1).encode().decode("unicode_escape")
    xlogin_query = urllib.parse.urlparse(xlogin_url).query

    # 2) xlogin —— 建 cookie，query 留着当 pt_openlogin_data
    S.get(xlogin_url, timeout=20)

    # 3) ptqrshow
    url = ("https://xui.ptlogin2.qq.com/ssl/ptqrshow"
           f"?s=8&e=0&appid={APPID}&type=0&t={random.random()}"
           f"&u1=http%3A%2F%2Fconnect.qq.com&daid={DAID}&pt_3rd_aid={PT_3RD_AID}")
    r = S.get(url, timeout=20)

    qrsig = S.cookies.get("qrsig")
    if not qrsig:
        for c in S.cookies:
            if c.name == "qrsig":
                qrsig = c.value
                break
    if not qrsig:
        raise SystemExit(f"没拿到 qrsig，HTTP {r.status_code}")

    with open(QR_PNG, "wb") as f:
        f.write(r.content)

    state = {"qrsig": qrsig, "ptqrtoken": hash33(qrsig),
             "openlogin_data": xlogin_query.lstrip("?"),
             "cookies": {c.name: c.value for c in S.cookies}}
    with open(QR_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    return state


def step_poll_once(state: dict) -> tuple[str, str]:
    """轮询一次。返回 (code, payload)。

    ptuiCB 第一个字段是状态码：
        66 = 未扫码   67 = 已扫码待确认   65 = 已失效   0 = 成功   1 = 已取消
    成功时 payload 是带 openid/access_token 的 URL。
    """
    params = {
        "u1": "http://connect.qq.com", "from_ui": "1", "type": "1",
        "ptlang": "2052", "ptqrtoken": str(state["ptqrtoken"]), "daid": DAID,
        "aid": APPID, "pt_3rd_aid": PT_3RD_AID,
        "pt_openlogin_data": state.get("openlogin_data", ""),
        "device": "2", "ptopt": "1", "pt_uistyle": "35",
        "jsver": "3ea7b82c", "r": f"{random.random()}",
    }
    url = "https://xui.ptlogin2.qq.com/ssl/ptqrlogin?" + urllib.parse.urlencode(params)
    body = S.get(url, timeout=20).text.strip()
    if not body.startswith("ptuiCB("):
        return "-1", body[:400]
    fields = [x.strip().strip("'") for x in body[len("ptuiCB("):-1].split(",")]
    code = fields[0] if fields else "-1"
    if code == "0":
        cand = fields[2] if len(fields) > 2 else ""
        if "http" not in cand:
            m = re.search(r"https?://[^'\"\s,)]+", body)
            cand = m.group(0) if m else cand
        return code, cand
    return code, (fields[4] if len(fields) > 4 else "")


def parse_success_url(url: str) -> dict:
    """从 ptuiCB 的成功 URL 里抠出 openid / access_token / pay_token。"""
    frag = urllib.parse.urlparse(url).fragment or url
    q = {}
    for part in frag.replace("#&", "&").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            q[k.strip("#")] = v
    keep = ("openid", "access_token", "pay_token", "appid", "key", "pf", "pfkey",
            "redirect_uri_key", "uin")
    return {k: q[k] for k in keep if k in q}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "qr"

    if mode == "qr":
        st = step_qr()
        print(f"二维码已保存：{QR_PNG}")
        print("请用手机 QQ 扫码 → 确认登录。开始轮询（最多 180 秒）…", flush=True)
        last = None
        for i in range(180):
            code, msg = step_poll_once(st)
            if code != last:
                print(f"  [{i:3d}s] code={code}", flush=True)
                last = code
            if code == "0":
                info = parse_success_url(msg)
                info["ts"] = int(time.time())
                with open(LOGIN_OUT, "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=1)
                os.chmod(LOGIN_OUT, 0o600)
                print("登录成功，已写入", LOGIN_OUT)
                for k, v in info.items():
                    if k != "ts":
                        print(f"  {k} = {str(v)[:40]}")
                return 0
            if code in ("65", "1"):
                print(f"二维码失效或被取消（code={code}），重跑本脚本。")
                return 2
            time.sleep(1)
        print("轮询超时，重跑本脚本出新的二维码。")
        return 3

    if mode == "poll":
        with open(QR_STATE, encoding="utf-8") as f:
            st = json.load(f)
        for k, v in st.get("cookies", {}).items():
            S.cookies.set(k, v)
        code, msg = step_poll_once(st)
        print(f"code={code} msg={msg[:200]}")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
