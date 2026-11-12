# -*- coding: utf-8 -*-
"""v305 增量维护等价性（specs：大库 serve CPU 治理）。

正确性契约：增量路径与全量路径在相同库状态下必须产出相同结果——
- _turn_rate_sql ≡ _turn_rate（行形态全覆盖：NULL first_token / Claude / 推理段 /
  covers / 窗口越界 / output=0）；
- 速度轮表 / 今日行集 / scan：增量维护若干轮（含运行中行翻转）后清空全部
  增量状态，全量重建必须与增量结果完全一致（帧级 dict 相等）；
- _sub_agent_tasks：part 增量扫描（含回执 UPDATE）与全量扫描任务映射一致，
  旧库无 time_updated 列时退回全量路径；
- _proj_untouched：他项目新行续用缓存、本项目新行触发重算。
"""
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zusage_inc", ROOT / "zusage.py")
zusage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zusage)

SCHEMA = """
create table session (id text primary key, title text, parent_id text, project_id text,
  directory text, time_created integer, summary_additions integer, summary_deletions integer,
  summary_files integer);
create table model_usage (id text primary key, session_id text, turn_id text, query_source text,
  agent text, provider_id text, model_id text, status text, started_at integer, first_token_at integer,
  completed_at integer, duration_ms integer, time_to_first_token_ms integer, input_tokens integer,
  output_tokens integer, reasoning_tokens integer, cache_read_input_tokens integer,
  cache_creation_input_tokens integer, computed_total_tokens integer, tool_call_count integer,
  retry_count integer, context_exceeded integer);
create table turn_usage (session_id text, turn_id text, status text, model_request_count integer,
  model_retry_count integer, tool_call_count integer, tool_error_count integer, input_tokens integer,
  output_tokens integer, reasoning_tokens integer, cache_creation_input_tokens integer,
  computed_total_tokens integer, duration_ms integer, time_to_first_token_ms integer);
create table tool_usage (session_id text, tool_name text, status text, duration_ms integer);
create table part (id text primary key, session_id text, data text, time_created integer,
  time_updated integer);
"""

_ALL_INCREMENTAL_STORES = (
    "_MU_STATE", "_SCAN_DELTA_STATE", "_SCAN_ROWS_KEEP", "_SPEED_TURNS_KEEP",
    "_SPEED_DELTA_STATE", "_TODAY_STATE", "_TODAY_DELTA_STATE", "_TASK_CACHE",
    "_AGG_CACHE", "_AGG_WM", "_PROJ_CACHE", "_PROJ_WM", "_PROJ_PWD",
    "_SPEED_CACHE", "_SPEED_WM",
)


def _clear_all():
    for name in _ALL_INCREMENTAL_STORES:
        getattr(zusage, name).clear()


class _IncBase(unittest.TestCase):
    def setUp(self):
        _clear_all()
        self.addCleanup(_clear_all)
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZCODE_")}
        env.update(HOME=self.td.name, USERPROFILE=self.td.name)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = Path(self.td.name) / "db.sqlite"
        self.con = sqlite3.connect(str(self.db))
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.n = 0

    def add_row(self, sid="s1", turn=None, source="main_turn", completed=True,
                output=30, reasoning=0, model="GLM-5.3", provider="account:test",
                ft=1500, ct=3500, st=1000):
        """插入一条 model_usage 行；completed=False 时 status='running' 且无完成时间。"""
        self.n += 1
        self.con.execute(
            "insert into model_usage (id, session_id, turn_id, query_source, provider_id,"
            " model_id, status, started_at, first_token_at, completed_at, input_tokens,"
            " output_tokens, reasoning_tokens, cache_read_input_tokens,"
            " cache_creation_input_tokens, computed_total_tokens, tool_call_count, retry_count)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("r%d" % self.n, sid, turn or "t%d" % self.n, source, provider, model,
             "completed" if completed else "running", st, ft, ct if completed else None,
             60, output, reasoning, 40, 2, 100, 1, 0))
        self.con.commit()
        return "r%d" % self.n

    def complete_row(self, rid, ct=9999):
        """宿主形态：先插 running 行，完成时 UPDATE 状态（rowid 不动）。
        首字时间缺省补齐（运行行插时无首字），保证完成后速度窗口有效。"""
        self.con.execute(
            "update model_usage set status='completed', completed_at=?, duration_ms=2000,"
            " time_to_first_token_ms=500,"
            " first_token_at=coalesce(first_token_at, started_at + 500) where id=?", (ct, rid))
        self.con.commit()

    def expire_ttl_caches(self, beyond_wm=False):
        """把带时间戳的缓存条目推过 TTL；beyond_wm=True 再推过 _WM_MAX_AGE_MS——
        翻转（rowid 不动）对 max(rowid) 水位不可见，帧可见性按既有契约以
        _WM_MAX_AGE_MS 为上界，测试用超龄模拟时间流逝。"""
        extra = zusage._WM_MAX_AGE_MS + 1 if beyond_wm else 0
        for name, ttl in (("_AGG_CACHE", zusage._AGG_TTL_MS + 1 + extra),
                          ("_SPEED_CACHE", zusage._SPEED_HISTORY_TTL_MS + 1 + extra)):
            cache = getattr(zusage, name)
            for k in list(cache):
                ts, val = cache[k]
                cache[k] = (ts - ttl, val)


class TurnRateSqlEquivalenceTests(_IncBase):
    """_turn_rate_sql 与 _turn_rate 在同库上逐值相等（v305 SQL 下推口径锁定）。"""

    def _check(self, where, args):
        rows = self.con.execute(
            f"""select output_tokens, first_token_at, completed_at, started_at,
                       reasoning_tokens, model_id, provider_id
                from model_usage where status='completed' and {where}""", args).fetchall()
        covers = frozenset({"cov-provider"})
        py = zusage._turn_rate(rows, covers)
        sql = zusage._turn_rate_sql(self.con, where, args, covers)
        self.assertEqual(py, sql, where)

    def test_row_archetypes_match(self):
        self.add_row()                                                   # 常规有效
        self.add_row(output=0)                                           # 零输出
        self.add_row(ft=None, ct=3500)                                   # 缺首字（NULL → 不计速率，计 unrated）
        self.add_row(reasoning=10)                                       # 推理前置 → started 窗口
        self.add_row(model="claude-sonnet-4")                            # Claude → started 窗口
        self.add_row(provider="cov-provider", reasoning=10)              # covers provider → 首字窗口
        self.add_row(st=None, reasoning=5)                               # started 缺失回退首字窗
        self.add_row(ct=1501)                                            # 窗口 1ms < MIN → unrated
        self.add_row(ct=1500 + 3_600_001)                                # 窗口超 MAX → unrated
        self.add_row(ct=1500)                                            # 窗口 0 → unrated
        self.add_row(source="subagent")                                  # 非 main_turn 也在聚合范围
        self.add_row(turn=None)                                          # NULL turn_id
        self._check("1=1", ())
        self._check("session_id='s1' and (query_source is null or query_source='main_turn')", ())
        self._check("session_id in (?,?)", ("s1", "nope"))

    def test_empty_and_no_covers(self):
        self.assertEqual(
            zusage._turn_rate_sql(self.con, "1=1", (), frozenset()),
            {"tps": 0, "rated_output": 0, "unrated_output": 0, "gen_ms": 0})
        self.add_row()
        self._check("session_id='s1'", ())


class IncrementalStateEquivalenceTests(_IncBase):
    """增量维护若干轮（含运行中行翻转）后，全量重建必须与增量结果完全一致。"""

    def _seed_sessions(self):
        self.con.execute(
            "insert into session (id, title, parent_id, project_id, directory, time_created)"
            " values ('s1','会话一',null,'proj-a','/w/pa',100),"
            " ('s2','会话二',null,'proj-b','/w/pb',100)")
        self.con.commit()

    def _speed_frame(self):
        return zusage._speed_history_safe(self.con, "s1")

    def _reset_incremental(self):
        for name in ("_MU_STATE", "_SPEED_TURNS_KEEP", "_SPEED_DELTA_STATE",
                     "_TODAY_STATE", "_TODAY_DELTA_STATE", "_SPEED_CACHE", "_SPEED_WM"):
            getattr(zusage, name).clear()

    def test_speed_and_today_after_inserts_and_flip(self):
        self._seed_sessions()
        for i in range(3):
            self.add_row(sid="s1" if i % 2 == 0 else "s2")
        f1 = self._speed_frame()
        self.assertEqual(len(f1["turns"]), 3)
        # 运行中行 → 完成翻转（rowid 不动，只走 watch）
        rid = self.add_row(sid="s1", completed=False, ft=None, ct=None)
        self.expire_ttl_caches(beyond_wm=True)
        f2 = self._speed_frame()   # watch 收录 running 行，不进轮表
        self.assertEqual(len(f2["turns"]), 3)
        self.complete_row(rid)
        self.expire_ttl_caches(beyond_wm=True)
        f3 = self._speed_frame()   # 翻转行经 watch 并入轮表（可见性上界 = _WM_MAX_AGE_MS，与既有契约一致）
        self.assertEqual(len(f3["turns"]), 4)
        # 新完成行（常规 INSERT）
        self.add_row(sid="s2")
        self.expire_ttl_caches()
        f4 = self._speed_frame()
        self.assertEqual(len(f4["turns"]), 5)
        # 全量重建对照：清空增量状态后结果必须一致
        self._reset_incremental()
        f5 = self._speed_frame()
        self.assertEqual(f4["turns"], f5["turns"])
        self.assertEqual(f4["sids"], f5["sids"])
        self.assertEqual(f4["truncated"], f5["truncated"])

    def test_speed_frame_contract_has_no_maintenance_keys(self):
        self._seed_sessions()
        self.add_row()
        f = self._speed_frame()
        for t in f["turns"]:
            self.assertNotIn("k", t, "维护键不得出帧")
        raw = json.dumps(f)
        self.assertNotIn('"k"', raw)

    def test_today_rows_incremental_equals_full(self):
        day = zusage._day_start()
        s, e = zusage._epoch_ms(day), zusage._epoch_ms(day + __import__("datetime").timedelta(days=1))
        self._seed_sessions()
        self.add_row(ct=int(time.time() * 1000))
        r1 = zusage._today_rows(self.con, s, e)
        rid = self.add_row(completed=False, ft=None, ct=None)
        self.complete_row(rid, ct=int(time.time() * 1000))
        self.expire_ttl_caches(beyond_wm=True)
        r2 = zusage._today_rows(self.con, s, e)
        self.assertEqual(len(r2), len(r1) + 1, "翻转行并入今日行集")
        # 昨日行不进今日窗口
        self.add_row(ct=s - 86400000)
        r3 = zusage._today_rows(self.con, s, e)
        self.assertEqual(len(r3), len(r2))
        # 全量重建对照
        zusage._TODAY_STATE.clear(); zusage._TODAY_DELTA_STATE.clear()
        zusage._MU_STATE.clear()
        r4 = zusage._today_rows(self.con, s, e)
        self.assertEqual(
            sorted((r["rowid"], r["completed_at"]) for r in r3),
            sorted((r["rowid"], r["completed_at"]) for r in r4))

    def test_today_tuple16_matches_sql_today_usage(self):
        import datetime
        day = zusage._day_start()
        s, e = zusage._epoch_ms(day), zusage._epoch_ms(day + datetime.timedelta(days=1))
        now_ms = int(time.time() * 1000)
        self._seed_sessions()
        self.add_row(ct=now_ms, output=100, reasoning=7)
        self.add_row(sid="s2", ct=now_ms, turn="t-shared", source="subagent", output=50)
        self.add_row(ct=s - 1, output=999)   # 昨日尾（不在窗口）
        rows = self.con.execute(
            f"""select {zusage._MU_DELTA_COLS} from model_usage
                where status='completed' and completed_at between ? and ?""", (s, e)).fetchall()
        got = zusage._today_tuple16(rows)
        want = zusage._today_usage(self.con, s, e)
        for i, (g, w) in enumerate(zip(got, want)):
            if i in (12, 13):   # 均值列：浮点比较
                self.assertAlmostEqual(g or 0, w or 0, places=6)
            else:
                self.assertEqual(g, w, f"col {i}")

    def test_scan_incremental_merge_equals_full(self):
        self._seed_sessions()
        self.add_row(sid="s1", ct=1000)
        self.add_row(sid="s2", ct=2000)
        zusage._scan_session_ids(self.con)
        self.add_row(sid="s1", ct=3000)
        rid = self.add_row(sid="s2", completed=False, ft=None, ct=None)
        self.complete_row(rid, ct=4000)
        self.expire_ttl_caches()
        d = dict(zusage._scan_session_ids(self.con))
        self.assertEqual(d.get("s2"), 4000, "翻转行更新 max(completed_at)")
        self.assertEqual(d.get("s1"), 3000)
        # 全量对照
        zusage._AGG_CACHE.clear(); zusage._AGG_WM.clear()
        zusage._MU_STATE.clear(); zusage._SCAN_ROWS_KEEP.clear(); zusage._SCAN_DELTA_STATE.clear()
        c = dict(zusage._scan_session_ids(self.con))
        self.assertEqual({k: v for k, v in d.items()}, c)


class SubAgentTaskIncrementalTests(_IncBase):
    def _agent_part(self, pid, sid, desc, prompt, output, t):
        data = json.dumps({"type": "tool", "tool": "Agent",
                           "state": {"input": {"description": desc, "prompt": prompt},
                                     "output": output}})
        self.con.execute(
            "insert into part (id, session_id, data, time_created, time_updated)"
            " values (?,?,?,?,?)", (pid, sid, data, t, t))
        self.con.commit()

    def test_incremental_matches_full_and_receipt_update(self):
        self.con.execute(
            "insert into session (id, title, parent_id, project_id, directory, time_created)"
            " values ('s1','p',null,'proj','/w',1)")
        self.con.commit()
        self._agent_part("p1", "s1", "调研", "去调研X" + "长" * 20, "", 100)
        tasks1 = zusage._sub_agent_tasks(self.con, "s1")
        self.assertEqual(tasks1, {})   # 无回执、无子会话：兜底也不绑定
        # 追加第二个 part（回执带 agentId）
        self._agent_part("p2", "s1", "调研", "去调研X" + "长" * 20,
                         "done\nagentId: agent_abc", 200)
        tasks2 = zusage._sub_agent_tasks(self.con, "s1")
        self.assertEqual(tasks2, {"sess_subagent_agent_abc": "调研"})
        # 回执落盘 = UPDATE 同一 part（time_updated 推进）
        data = json.dumps({"type": "tool", "tool": "Agent",
                           "state": {"input": {"description": "重跑", "prompt": "再跑一遍" + "长" * 20},
                                     "output": "done\nagentId: agent_def"}})
        self.con.execute("update part set data=?, time_updated=300 where id='p1'", (data,))
        self.con.commit()
        tasks3 = zusage._sub_agent_tasks(self.con, "s1")
        self.assertEqual(tasks3, {"sess_subagent_agent_abc": "调研",
                                  "sess_subagent_agent_def": "重跑"})
        # 全量对照：清缓存重扫必须一致
        zusage._TASK_CACHE.clear()
        self.assertEqual(zusage._sub_agent_tasks(self.con, "s1"), tasks3)

    def test_legacy_schema_without_time_updated_falls_back_to_full_scan(self):
        self.con.execute("drop table part")
        self.con.execute(
            "create table part (id text primary key, session_id text, data text, time_created integer)")
        self.con.execute(
            "insert into session (id, title, parent_id, project_id, directory, time_created)"
            " values ('s1','p',null,'proj','/w',1)")
        self.con.commit()
        data = json.dumps({"type": "tool", "tool": "Agent",
                           "state": {"input": {"description": "旧库任务", "prompt": "x" * 30},
                                     "output": "ok\nagentId: agent_old"}},
                          separators=(",", ":"))   # 宿主落盘为紧凑 JSON（LIKE 模式无空格）
        self.con.execute(
            "insert into part (id, session_id, data, time_created) values ('p1','s1',?,50)", (data,))
        self.con.commit()
        tasks = zusage._sub_agent_tasks(self.con, "s1")
        self.assertEqual(tasks, {"sess_subagent_agent_old": "旧库任务"})

    def test_non_agent_parts_and_bad_json_skipped(self):
        self.con.execute(
            "insert into session (id, title, parent_id, project_id, directory, time_created)"
            " values ('s1','p',null,'proj','/w',1)")
        self.con.commit()
        self.con.executemany(
            "insert into part (id, session_id, data, time_created, time_updated) values (?,?,?,?,?)",
            [("p1", "s1", "{bad json", 10, 10),
             ("p2", "s1", json.dumps({"type": "tool", "tool": "Bash", "state": {}}), 20, 20),
             ("p3", "s1", json.dumps({"type": "text", "tool": "Agent", "state": {}}), 30, 30)])
        self.con.commit()
        self.assertEqual(zusage._sub_agent_tasks(self.con, "s1"), {})


class ProjectScopeProbeTests(_IncBase):
    def _seed(self):
        self.con.execute(
            "insert into session (id, title, parent_id, project_id, directory, time_created)"
            " values ('s1','一',null,'pa','/w/pa',1), ('s2','二',null,'pb','/w/pb',1)")
        self.con.commit()
        self.add_row(sid="s1", ct=1000)

    def test_other_project_new_row_reuses_cache(self):
        self._seed()
        p1 = zusage._project_usage(self.con, "s1")
        self.assertEqual(p1["requests"], 1)
        full = next(k for k in zusage._PROJ_CACHE if k[1][0] == "pa")
        zusage._PROJ_CACHE[full] = (zusage._PROJ_CACHE[full][0] - zusage._AGG_TTL_MS - 1,
                                    zusage._PROJ_CACHE[full][1])   # TTL 过期
        self.add_row(sid="s2", ct=2000)   # 他项目新行：全局水位已变
        p2 = zusage._project_usage(self.con, "s1")
        self.assertIs(p1, p2, "探测放行：本项目无新行，续用缓存对象")

    def test_own_project_new_row_recomputes(self):
        self._seed()
        p1 = zusage._project_usage(self.con, "s1")
        full = next(k for k in zusage._PROJ_CACHE if k[1][0] == "pa")
        zusage._PROJ_CACHE[full] = (zusage._PROJ_CACHE[full][0] - zusage._AGG_TTL_MS - 1,
                                    zusage._PROJ_CACHE[full][1])
        self.add_row(sid="s1", ct=2000)
        p2 = zusage._project_usage(self.con, "s1")
        self.assertEqual(p2["requests"], 2)
        self.assertIsNot(p1, p2)

    def test_probe_never_returns_stale_within_max_age_only(self):
        """探测只在 _WM_MAX_AGE_MS 内有效：超龄必须重算（既有陈旧契约不放宽）。"""
        self._seed()
        zusage._project_usage(self.con, "s1")
        full = next(k for k in zusage._PROJ_CACHE if k[1][0] == "pa")
        zusage._PROJ_CACHE[full] = (zusage._PROJ_CACHE[full][0] - zusage._WM_MAX_AGE_MS - 1,
                                    zusage._PROJ_CACHE[full][1])
        self.add_row(sid="s2", ct=2000)
        zusage._PROJ_WM.clear()   # 超龄场景水位延长本就失效
        p2 = zusage._project_usage(self.con, "s1")
        self.assertEqual(p2["requests"], 1)
        self.assertIsNotNone(p2)


class LedgerFoundationTests(_IncBase):
    """代次账本基座：消费方只补差额、断代自愈、dbkey 隔离。"""

    def test_consumer_takes_each_generation_once(self):
        consumer = {}
        zusage._mu_take(self.con, consumer)   # 首见：full
        self.add_row()
        need, new, flip = zusage._mu_take(self.con, consumer)
        self.assertFalse(need); self.assertEqual(len(new), 1)
        need, new2, flip2 = zusage._mu_take(self.con, consumer)   # 无新行
        self.assertFalse(need); self.assertEqual(new2, [])
        # 多代次合并领取
        consumer2 = {}
        zusage._mu_take(self.con, consumer2)   # full（gen 重置后首见）
        self.add_row(); self.add_row()
        need, new3, _ = zusage._mu_take(self.con, consumer2)
        self.assertFalse(need)
        self.assertEqual(len(new3), 2, "两代合并一次领取")

    def test_flip_via_watch_seen_by_second_consumer(self):
        consumer = {}
        zusage._mu_take(self.con, consumer)
        rid = self.add_row(completed=False, ft=None, ct=None)
        zusage._mu_take(self.con, consumer)   # running 行进 watch
        self.complete_row(rid)
        need, new, flip = zusage._mu_take(self.con, consumer)
        self.assertFalse(need)
        self.assertEqual(len(flip), 1, "完成翻转经 watch 当帧捕获")
        self.assertEqual(new, [])

    def test_rowid_shrink_forces_full(self):
        consumer = {}
        zusage._mu_take(self.con, consumer)
        self.add_row(ct=1000)
        self.add_row(ct=2000)
        zusage._mu_take(self.con, consumer)   # 水位推进到 rowid=2
        self.con.execute("delete from model_usage where id='r2'")   # 删最高 rowid
        self.con.commit()
        need, _, _ = zusage._mu_take(self.con, consumer)
        self.assertTrue(need, "max(rowid) 收缩：整体重建")

    def test_memory_db_is_always_full(self):
        mem = sqlite3.connect(":memory:")
        self.addCleanup(mem.close)
        mem.row_factory = sqlite3.Row
        mem.executescript(SCHEMA)
        consumer = {}
        self.assertTrue(zusage._mu_take(mem, consumer)[0])
        self.assertTrue(zusage._mu_take(mem, consumer)[0], "内存库恒全量")


if __name__ == "__main__":
    unittest.main()
