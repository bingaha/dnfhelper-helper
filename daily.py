#!/usr/bin/env python3
"""
每日任务一键脚本（纯接口，不依赖 App / 不做 UI 自动化）

流程
----
  1. 签到礼包：查 getmonthsigngifts，按 signStatus / giftStatus 分别处理。
  2. 任务：合并两套任务视图，按状态处理：
       1 = 已领取 → 跳过
       2 = 待领取 → claim-task 领经验
       0 = 未完成 → 按 actionId 走对应的完成方式
  3. 基础礼包：一键 claim-basic-all（需账号已绑定游戏角色）
  4. 输出 JSON 汇总；exit 0 = 无待跟进，1 = 有 pending

任务完成方式对照
----------------
  001 签到          claim-task 直接成功（服务端对 001 不校验完成状态）
  008 分享周报      actionreport（无需签名）→ 状态翻 2 → claim
  049 查看排行榜    actionreport → 状态翻 2 → claim（只在 App 视图里出现）
  043 浏览1篇内容   /moment/detail 真开一篇动态 → 状态翻 2 → claim
  045 进圈子详情页  circle/detail + momentlist → 状态翻 2 → claim
  047 浏览话题      脏数据，claim 报 -70007，跳过
  009 / 005         需游戏服侧真实事件（通关/登录），接口层面无法替代

用法
----
    python3 daily.py            # 全量跑一遍
    python3 daily.py --check    # 只查状态，不做任何写操作
"""
import json
import os
import sys
import time

import peak_client as pc
import native_client as nc

# 不可能用接口完成的任务：actionId -> 原因
SKIP_TASKS = {
    "047": "非法任务（-70007，App 也不显示）",
    "009": "需真实进游戏通关地下城",
    "005": "需登录游戏",
}


def task_snapshot() -> list:
    """默认视图（SESSION 全量参数）。"""
    return pc.call("POST", "/yoyo/dnf/getusertasklist")["data"]["taskList"]


def task_snapshot_app() -> list:
    """App 参数集视图，只有这个视图里才有 049「查看地区排行榜」。

    两套视图互斥：本函数返回 049 就不返回 047，反之亦然。
    见 peak_client.TASK_LIST_KEYS 的注释。
    """
    return pc.task_list_app()


def merged_tasks() -> list:
    """合并两套任务视图，按 mActionId 去重（同 id 取先出现的）。"""
    seen, out = set(), []
    for t in task_snapshot() + task_snapshot_app():
        aid = t.get("mActionId")
        if aid and aid not in seen:
            seen.add(aid)
            out.append(t)
    return out


def status_of(tasks: list, aid: str) -> int:
    for t in tasks:
        if t.get("mActionId") == aid:
            return int(t.get("mStatus", -1))
    return -9


def wait_status(aid: str, want: int = 2, secs: int = 15,
                step: float = 2.0) -> int:
    """轮询任务状态，等服务端把 mStatus 推到 want。

    完成任务的那一步（打开动态、进圈子、actionreport）都是异步生效的，
    固定 sleep 几秒再查一次会误判成「没完成」。这里按时间轮询，拿到就返回。
    """
    deadline = time.time() + secs
    st = status_of(merged_tasks(), aid)
    while st != want and time.time() < deadline:
        time.sleep(step)
        st = status_of(merged_tasks(), aid)
    return st


def run(check_only: bool = False) -> dict:
    summary = {"actions": [], "pending": [], "exp_total": None}

    def log(action: str, result):
        summary["actions"].append({"action": action, "result": result})
        print(f"[{action}] {json.dumps(result, ensure_ascii=False)[:300]}",
              flush=True)

    # ---- 1. 签到礼包 ----
    #
    # signStatus: 0=未签到, 1=已签到；giftStatus: 0=未领, 1=已领。
    # 这是独立的两件事，必须分开报 —— 别把「领奖失败」说成「没签到」。
    if not check_only:
        try:
            from datetime import date
            day = str(date.today())

            def sign_state() -> dict:
                mg = pc.call("POST", "/yoyo/dnf/getmonthsigngifts")["data"]
                return mg.get(day) or {}

            def wait_sign(secs: int = 20, step: float = 2.0) -> dict:
                """等 signStatus 翻 1。

                埋点（/login/analysis）是异步落库的，发完立刻查大概率还是 0。
                固定 sleep 一两秒就重查会误报「签到未成功」，必须轮询。
                """
                deadline = time.time() + secs
                cur = sign_state()
                while cur.get("signStatus") != 1 and time.time() < deadline:
                    time.sleep(step)
                    cur = sign_state()
                return cur

            st = sign_state()
            if st:
                if st.get("signStatus") == 0:
                    # 签到埋点序列：eid 取自任务 001 的 mEventId
                    er = {}
                    for eid in ("21437", "21374"):
                        try:
                            er[eid] = pc.analysis(eid)
                        except Exception as ex:  # noqa: BLE001
                            er[eid] = f"ERR {ex}"
                        time.sleep(1)
                    log("sign-experiment-analysis", er)
                    st = wait_sign()

                if st.get("signStatus") == 1:
                    if st.get("giftStatus") == 1:
                        log("claim-sign", "skip（今日已领）")
                    else:
                        r = pc.claim_sign()
                        log("claim-sign", r)
                        if (r.get("code") or r.get("result")) != 200:
                            summary["pending"].append(
                                f"签到已成功，但礼包领取失败："
                                f"{r.get('code') or r.get('result')} {r.get('msg', '')}"
                                "（基础礼包需绑定游戏角色）")
                else:
                    summary["pending"].append(
                        "签到未成功（埋点后等待 20 秒 signStatus 仍为 0）")
        except Exception as ex:  # noqa: BLE001
            summary["pending"].append(f"签到流程异常：{ex}")

    # ---- 2. 任务 ----
    for t in merged_tasks():
        aid, name = t["mActionId"], t.get("name", "")
        st = int(t.get("mStatus", -1))
        if st == 1:
            continue
        if aid in SKIP_TASKS:
            summary["pending"].append(f"{aid} {name}：跳过（{SKIP_TASKS[aid]}）")
            continue
        if check_only:
            summary["pending"].append(f"{aid} {name}：mStatus={st}（check 模式未动）")
            continue
        try:
            if st == 2:
                log(f"claim-task-{aid}", pc.claim_task(aid))
            elif st == 0:
                if aid == "001":
                    # 服务端对 001 不校验完成状态，0 也能直接领
                    log("claim-task-001", pc.claim_task("001"))
                elif aid == "008":
                    rep = pc.report_action("008")
                    log("report-action-008", rep)
                    if wait_status("008") == 2:
                        log("claim-task-008", pc.claim_task("008"))
                elif aid == "049":
                    # App 打开地区排行榜页时发 actionreport actionId=049
                    # （与 008 同一条无签名明文通道），服务端随即推到 2。
                    rep = pc.report_action("049")
                    log("report-action-049", rep)
                    if wait_status("049") == 2:
                        log("claim-task-049", pc.claim_task("049"))
                    else:
                        summary["pending"].append(
                            f"049 {name}：actionreport 后仍非 2"
                            f"（data={rep.get('data')}）")
                elif aid == "043":
                    # 「浏览1篇内容」必须真的打开一篇动态。
                    # 只发发现页三连服务端不认，状态会一直是 0。
                    calls = {}
                    for label, fn in (("getrecocirclelist", nc.circle_reco),
                                      ("chatgroups", nc.chatgroups),
                                      ("hottopiclist", nc.hottopiclist),
                                      ("moment/detail", nc.browse_one_moment)):
                        try:
                            calls[label] = fn().get("returnCode")
                        except Exception as ex:  # noqa: BLE001
                            calls[label] = f"ERR {ex}"
                        time.sleep(1)
                    log(f"native-browse-{aid}", calls)
                    if wait_status(aid, secs=20) == 2:
                        log(f"claim-task-{aid}", pc.claim_task(aid))
                    else:
                        summary["pending"].append(
                            f"{aid} {name}：打开动态后仍 mStatus=0"
                            f"（calls={calls}）")
                elif aid == "045":
                    # 「进入圈子详细页」= 圈子详情 + 动态列表 + 达人榜
                    calls = {}
                    for label, fn in (("circle/detail", nc.circle_detail_full),
                                      ("circle/momentlist",
                                       nc.circle_momentlist_full),
                                      ("getcircletalent", nc.circle_talent)):
                        try:
                            calls[label] = fn().get("returnCode")
                        except Exception as ex:  # noqa: BLE001
                            calls[label] = f"ERR {ex}"
                        time.sleep(1)
                    log(f"native-browse-{aid}", calls)
                    if wait_status(aid, secs=20) == 2:
                        log(f"claim-task-{aid}", pc.claim_task(aid))
                    else:
                        summary["pending"].append(
                            f"{aid} {name}：原生接口浏览后仍 mStatus=0")
                else:
                    # 未知新任务：先 report 再看是否翻 2（report 无副作用）
                    rep = pc.report_action(aid)
                    log(f"report-action-{aid}", rep)
                    if wait_status(aid) == 2:
                        log(f"claim-task-{aid}", pc.claim_task(aid))
                    else:
                        summary["pending"].append(
                            f"{aid} {name}：report 不认（data:1），待分析")
        except Exception as ex:  # noqa: BLE001
            summary["pending"].append(f"{aid} {name}：异常 {ex}")

    # ---- 3. 基础礼包 ----
    if not check_only:
        try:
            log("claim-basic-all", pc.claim_basic_all())
        except Exception as ex:  # noqa: BLE001
            summary["pending"].append(f"claim-basic-all 异常：{ex}")

    # ---- 4. 汇总 ----
    try:
        top = pc.call("POST", "/yoyo/dnf/getuseractivitytopinfo")["data"]
        summary["exp_total"] = top.get("totalExp")
        summary["level"] = top.get("level")
    except Exception:  # noqa: BLE001
        pass
    return summary


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    summary = run(check_only=check_only)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["pending"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
