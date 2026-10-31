#!/usr/bin/env python3
"""
从活动页 URL 恢复 session.json。

背景
----
活动页 https://dzhu.qq.com/fe/dnf/calculation4.0?... 的查询串里带齐了
调 dzhu.qq.com 接口所需的全套凭证。本脚本把那个查询串解析成 session.json。

这是**备用登录方式**：正常情况下用 `login_new_account.py --qr` 扫码即可，
它会自动调 `/game/chatroles` 把角色字段填全。本脚本适用于已经有活动页
URL 的场景（例如从别处拿到了完整链接），省去一次接口往返。

用法
----
    python harvest_session.py '<https://dzhu.qq.com/fe/dnf/calculation4.0?uin=...>'
    python harvest_session.py login.txt       # 文件内容里含 URL 即可
    cat login.txt | python harvest_session.py -
"""
import json
import os
import re
import sys
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESS = os.path.join(BASE_DIR, "session.json")

KEEP = (
    "userId", "uin", "token", "accessToken", "appOpenid",
    "gameId", "game_code",
    "uniqueRoleId", "sRoleId", "sPartition", "serverName", "roleName",
    "roleJob", "roleLevel", "areaName", "appidTask", "nickname",
)


def extract_url(text: str) -> str:
    m = re.search(r"https://dzhu\.qq\.com/fe/dnf/calculation4\.0[^\s'\"\\]*", text)
    if not m:
        raise SystemExit("没在输入里找到 calculation4.0 的 URL")
    return m.group(0)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    arg = sys.argv[1]
    if arg == "-":
        text = sys.stdin.read()
    elif os.path.exists(arg):
        with open(arg, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = arg

    url = extract_url(text)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))

    sess = {}
    for k in KEEP:
        if k in q and q[k]:
            sess[k] = q[k]

    # URL 里叫 serverId，接口里叫 sPartition —— 注意不是 areaId
    if "serverId" in q and "sPartition" not in sess:
        sess["sPartition"] = q["serverId"]
    if "roleId" in q and "sRoleId" not in sess:
        sess["sRoleId"] = q["roleId"]

    sess.setdefault("gameId", "1006")
    sess.setdefault("game_code", "dnf")
    sess.setdefault("appidTask", "1000042")

    for k in ("uniqueRoleId", "sRoleId", "sPartition", "serverName", "roleName",
              "accessToken", "appOpenid", "userId", "token", "uin"):
        sess.setdefault(k, "")

    missing = [k for k in ("userId", "uin", "token") if not sess.get(k)]
    if missing:
        raise SystemExit(f"URL 里缺关键字段：{missing}")

    with open(SESS, "w", encoding="utf-8") as f:
        json.dump(sess, f, ensure_ascii=False, indent=1)
    os.chmod(SESS, 0o600)

    print(f"已写入 {SESS}（0600）")
    for k in ("userId", "uin", "nickname", "roleName", "serverName",
              "sRoleId", "sPartition", "uniqueRoleId"):
        v = str(sess.get(k, ""))
        print(f"  {k:14} = {v[:12]}{'…' if len(v) > 12 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
