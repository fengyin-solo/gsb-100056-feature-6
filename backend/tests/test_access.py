"""三级权限域 + 化探样品接入的端到端规则测试。

覆盖：
- 项目组继承采样区、样品包只读例外
- 本单位才能调矩阵，跨单位只读
- 越权改采样点位拒绝；跨单位读取敏感字段脱敏、无授权 404
- 台账/点位图/样品袋待办三处同源
- 授权调整回填历史共享引用、清缓存，历史经手人保留
- 调班/撤权按 op_time 定序，冲突失败旧会话立即失效
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.access import access
from app.main import app
from app.store import store


class AccessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        access.reset()
        self.client = TestClient(app)

    def login(self, username: str) -> str:
        response = self.client.post("/api/access/sessions", json={"username": username})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def auth(self, token: str) -> dict[str, str]:
        return {"X-Session-Token": token}

    # ------------------------------------------------------------ 继承/例外

    def test_group_inherits_area_and_package_readonly_exception(self) -> None:
        li = self.login("li.gong")  # AREA-A1 读写
        wang = self.login("wang.gong")  # AREA-A1 读写 + PKG-A111 只读例外

        # 李工：北坡2号包（无包级条目）继承采样区 → 读写
        response = self.client.patch(
            "/api/geochem/2", headers=self.auth(li),
            json={"values": {"样品类型": "岩石地球化学样"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        # 王工在 PKG-A111 是只读例外：改普通字段也拒绝
        response = self.client.patch(
            "/api/geochem/1", headers=self.auth(wang),
            json={"values": {"样品类型": "岩石地球化学样"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        self.assertIn("只读", response.json()["message"])

        # 王工对 PKG-A112（继承采样区）仍可写
        response = self.client.patch(
            "/api/geochem/2", headers=self.auth(wang),
            json={"values": {"检出限": "0.02ppm"}},
        )
        self.assertTrue(response.json()["ok"])

    # ------------------------------------------------------------ 矩阵管理

    def test_only_owner_unit_can_adjust_matrix(self) -> None:
        admin = self.login("admin.a")
        partner = self.login("inspector.c")

        # 本单位管理员可调整：给李工把采样区降为只读
        response = self.client.put(
            "/api/access/grants", headers=self.auth(admin),
            json={"account": "li.gong", "kind": "area",
                  "scope": "AREA-A1", "level": "readonly"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        # 降权后李工再写样品被拒
        li = self.login("li.gong")
        response = self.client.patch(
            "/api/geochem/2", headers=self.auth(li),
            json={"values": {"样品类型": "岩石地球化学样"}},
        )
        self.assertFalse(response.json()["ok"])
        self.assertIn("只读", response.json()["message"])

        # 跨单位账号调 UNIT-A 的区间：一律拒绝
        response = self.client.put(
            "/api/access/grants", headers=self.auth(partner),
            json={"account": "li.gong", "kind": "area",
                  "scope": "AREA-A1", "level": "readwrite"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("只读", response.json()["detail"])

        # 矩阵对跨单位账号标注 can_manage=False
        matrix = self.client.get("/api/access/matrix", headers=self.auth(partner)).json()
        area_row = next(row for row in matrix["rows"] if row["scope"] == "AREA-A1")
        self.assertFalse(area_row["can_manage"])

        # 项目组不允许单独授权
        response = self.client.put(
            "/api/access/grants", headers=self.auth(admin),
            json={"account": "li.gong", "kind": "group",
                  "scope": "GRP-A11", "level": "readwrite"},
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_grant_cross_unit_account(self) -> None:
        admin = self.login("admin.a")
        response = self.client.put(
            "/api/access/grants", headers=self.auth(admin),
            json={"account": "partner.b", "kind": "area",
                  "scope": "AREA-A1", "level": "readwrite"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("跨单位", response.json()["detail"])

    # ------------------------------------------------------------ 点位写保护

    def test_point_change_forbidden_without_rw(self) -> None:
        partner = self.login("inspector.c")  # 第三方单位，跨单位只读
        wang = self.login("wang.gong")  # PKG-A111 只读例外

        # 跨单位改点位：拒绝
        response = self.client.patch(
            "/api/geochem/1", headers=self.auth(partner),
            json={"values": {"采样点位": "PKG-A111-越权点"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        self.assertIn("越权修改采样点位", response.json()["message"])
        row = store.find("geochem", 1)
        self.assertNotEqual(row["采样点位"], "PKG-A111-越权点")

        # 只读例外账号改点位：拒绝
        response = self.client.patch(
            "/api/geochem/1", headers=self.auth(wang),
            json={"values": {"采样点位": "PKG-A111-越权点"}},
        )
        self.assertFalse(response.json()["ok"])
        self.assertIn("越权修改采样点位", response.json()["message"])

        # 本单位读写账号改点位：放行
        li = self.login("li.gong")
        response = self.client.patch(
            "/api/geochem/2", headers=self.auth(li),
            json={"values": {"采样点位": "PKG-A112-点02X"}},
        )
        self.assertTrue(response.json()["ok"], response.json())
        self.assertEqual(store.find("geochem", 2)["采样点位"], "PKG-A112-点02X")

    # ------------------------------------------------------------ 脱敏/不可见

    def test_cross_unit_readonly_redaction_and_404(self) -> None:
        partner = self.login("inspector.c")  # 第三方单位：对 A/B 都是跨单位只读

        # 跨单位只读可见全部样品，但敏感字段全部脱敏
        listing = self.client.get("/api/geochem", headers=self.auth(partner)).json()
        self.assertGreaterEqual(listing["total"], 5)
        sample_1 = next(item for item in listing["items"] if item["id"] == 1)
        self.assertEqual(sample_1["样品类型"], "******")
        self.assertEqual(sample_1["分析元素"], "******")
        self.assertEqual(sample_1["采样点位"], "PKG-A111-点01")  # 点位不脱敏

        detail = self.client.get("/api/geochem/1", headers=self.auth(partner)).json()
        self.assertEqual(detail["检出限"], "******")

        # 不存在样品统一 404，错误文案不泄露任何样品字段
        response = self.client.get("/api/geochem/999", headers=self.auth(partner))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("样品类型", response.json()["detail"])

    def test_revoked_same_unit_account_loses_own_samples_but_keeps_cross_unit_readonly(self) -> None:
        # 本单位账号撤光授权后：本单位样品整条不可见（404 口径），
        # 但跨单位只读口径不变（可见 UNIT-B 样品且脱敏）。
        admin = self.login("admin.a")
        wang = self.login("wang.gong")
        listing = self.client.get("/api/geochem", headers=self.auth(wang)).json()
        self.assertGreater(listing["total"], 1)

        grant_ids: list[int] = []
        for row in self.client.get("/api/access/matrix",
                                   headers=self.auth(admin)).json()["rows"]:
            grant_ids.extend(g["grant_id"] for g in row["accounts"]
                             if g["account"] == "wang.gong")
        base = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
        for index, grant_id in enumerate(grant_ids):
            response = self.client.post(
                f"/api/access/grants/{grant_id}/revoke",
                headers=self.auth(admin),
                json={"op_time": (base + timedelta(minutes=index)).isoformat()})
            self.assertEqual(response.status_code, 200, response.text)

        # 撤光后旧会话已被撤权关闭；重新登录后本单位样品全部不可见
        self.assertEqual(self.client.get("/api/geochem", headers=self.auth(wang)).status_code,
                         401)
        wang_fresh = self.login("wang.gong")
        listing = self.client.get("/api/geochem", headers=self.auth(wang_fresh)).json()
        self.assertEqual({item["样品包编号"] for item in listing["items"]}, {"PKG-B111"})
        # 跨单位只读依然脱敏
        self.assertEqual(listing["items"][0]["样品类型"], "******")
        # 本单位样品详情 404
        response = self.client.get("/api/geochem/1", headers=self.auth(wang_fresh))
        self.assertEqual(response.status_code, 404)
        # 写跨单位样品仍被拒
        response = self.client.patch(
            "/api/geochem/5", headers=self.auth(wang_fresh),
            json={"values": {"采样点位": "PKG-B111-越权点"}},
        )
        self.assertFalse(response.json()["ok"])

    # ------------------------------------------------------------ 三处同源

    def test_ledger_map_bag_share_one_snapshot(self) -> None:
        li = self.login("li.gong")
        full = self.client.get("/api/geochem/access-view",
                               headers=self.auth(li)).json()
        ledger = self.client.get("/api/geochem/access-view?view=ledger",
                                 headers=self.auth(li)).json()
        point_map = self.client.get("/api/geochem/access-view?view=point_map",
                                    headers=self.auth(li)).json()
        bags = self.client.get("/api/geochem/access-view?view=bag_todos",
                               headers=self.auth(li)).json()

        version = full["decision_version"]
        self.assertEqual(ledger["decision_version"], version)
        self.assertEqual(point_map["decision_version"], version)
        self.assertEqual(bags["decision_version"], version)

        # 同源一致性：同一样品编号在台账与点位图里授权结论一致
        ledger_by_id = {row["样品编号"]: row for row in ledger["ledger"]}
        map_by_id = {row["样品编号"]: row for row in point_map["point_map"]}
        self.assertEqual(set(ledger_by_id), set(map_by_id))
        for code in ledger_by_id:
            self.assertEqual(ledger_by_id[code]["access"], map_by_id[code]["access"])

        # 样品袋待办数量 = 台账内 pending 样品数
        todo_total = sum(bag["pending"] for bag in bags["bag_todos"])
        pending_total = sum(1 for row in ledger["ledger"] if row["pending"])
        self.assertEqual(todo_total, pending_total)

        # 点位图不携带任何敏感字段
        for row in point_map["point_map"]:
            self.assertNotIn("样品类型", row)
            self.assertNotIn("分析元素", row)

    # ------------------------------------------------------------ 回填+缓存

    def test_grant_adjust_backfills_refs_and_clears_cache(self) -> None:
        admin = self.login("admin.a")
        li = self.login("li.gong")

        # 先取两次快照：第二次命中缓存
        self.client.get("/api/geochem/access-view", headers=self.auth(li))
        self.client.get("/api/geochem/access-view", headers=self.auth(li))
        status_before = self.client.get("/api/access/cache-status",
                                        headers=self.auth(admin)).json()
        self.assertGreaterEqual(status_before["hits"], 1)
        version_before = status_before["decision_version"]

        # 历史共享引用初始未回填，经手人是 wang.gong
        refs = self.client.get("/api/access/share-refs",
                               headers=self.auth(admin)).json()["items"]
        ref1 = next(ref for ref in refs if ref["ref_no"] == "REF-2026-0301")
        self.assertIsNone(ref1["resolved_grant_id"])
        self.assertEqual(ref1["shared_by"], "wang.gong")

        # 李工当前对 PKG-A111 没有包级授权；管理员补一条包级读写，触发回填
        response = self.client.put(
            "/api/access/grants", headers=self.auth(admin),
            json={"account": "li.gong", "kind": "package",
                  "scope": "PKG-A111", "level": "readwrite"},
        )
        body = response.json()
        self.assertIn(1, body["backfilled_refs"])
        self.assertEqual(body["decision_version"], version_before + 1)

        refs = self.client.get("/api/access/share-refs?resolved=true",
                               headers=self.auth(admin)).json()["items"]
        ref1 = next(ref for ref in refs if ref["ref_no"] == "REF-2026-0301")
        self.assertEqual(ref1["resolved_grant_id"], body["grant_id"])
        # 历史经手关系仍归原账号
        self.assertEqual(ref1["shared_by"], "wang.gong")

        # 失效缓存已清理：revision 增加、条目归零
        status_after = self.client.get("/api/access/cache-status",
                                       headers=self.auth(admin)).json()
        self.assertGreater(status_after["cache_revision"],
                           status_before["cache_revision"])
        self.assertEqual(status_after["entries"], 0)

    # ------------------------------------------------------------ 调班定序

    def test_handover_ordering_and_stale_session_closed(self) -> None:
        li = self.login("li.gong")

        # 第一笔调班成功，返回接班人新会话
        r1 = self.client.post(
            "/api/access/handover", headers=self.auth(li),
            json={"new_account": "wang.gong",
                  "op_time": "2026-09-30T09:00:00+00:00"},
        )
        self.assertEqual(r1.status_code, 200, r1.text)
        wang_token = r1.json()["token"]

        # 旧会话不得继续提交
        response = self.client.get("/api/geochem", headers=self.auth(li))
        self.assertEqual(response.status_code, 401)
        self.assertIn("handover", response.json()["detail"])

        # 接班人新会话可正常工作
        self.assertEqual(
            self.client.get("/api/geochem", headers=self.auth(wang_token)).status_code,
            200,
        )

        # 迟到的调班（op_time 更早）：冲突失败，且接班人旧会话立即关闭
        r_late = self.client.post(
            "/api/access/handover", headers=self.auth(wang_token),
            json={"new_account": "li.gong",
                  "op_time": "2026-09-30T08:59:00+00:00"},
        )
        self.assertEqual(r_late.status_code, 409)
        response = self.client.get("/api/geochem", headers=self.auth(wang_token))
        self.assertEqual(response.status_code, 401)
        self.assertIn("stale_op_time", response.json()["detail"])

        # 被拒绝的操作也留痕，actor 保留原账号
        audit = self.client.get("/api/access/audit",
                                headers=self.auth(self.login("admin.a"))).json()["items"]
        rejected = [a for a in audit if a["action"] == "handover_rejected"]
        self.assertEqual(rejected[-1]["actor"], "wang.gong")
        self.assertFalse(rejected[-1]["applied"])

    def test_cross_unit_handover_rejected_without_closing_session(self) -> None:
        li = self.login("li.gong")
        response = self.client.post(
            "/api/access/handover", headers=self.auth(li),
            json={"new_account": "partner.b",
                  "op_time": "2026-09-30T09:00:00+00:00"},
        )
        self.assertEqual(response.status_code, 400)
        # 调班未成行，原会话仍可用
        self.assertEqual(
            self.client.get("/api/geochem", headers=self.auth(li)).status_code,
            200,
        )

    def test_revoke_closes_sessions_only_when_no_grant_left(self) -> None:
        admin = self.login("admin.a")

        # 王工有 2 条授权，撤 1 条会话不关；撤光才关
        matrix = self.client.get("/api/access/matrix",
                                 headers=self.auth(admin)).json()["rows"]
        wang_grants = [(row["kind"], row["scope"], g["grant_id"])
                       for row in matrix for g in row["accounts"]
                       if g["account"] == "wang.gong"]
        self.assertEqual(len(wang_grants), 2)

        t = "2026-09-30T10:00:00+00:00"
        package_grant = next(grant for grant in wang_grants if grant[0] == "package")
        r = self.client.post(
            f"/api/access/grants/{package_grant[2]}/revoke",
            headers=self.auth(admin), json={"op_time": t},
        )
        self.assertEqual(r.json()["sessions_closed"], 0)

        wang = self.login("wang.gong")
        # 撤掉包级只读后，王工对 PKG-A111 恢复继承采样区读写
        response = self.client.patch(
            "/api/geochem/1", headers=self.auth(wang),
            json={"values": {"采样点位": "PKG-A111-点01Y"}},
        )
        self.assertTrue(response.json()["ok"], response.json())

        area_grant = next(grant for grant in wang_grants if grant[0] == "area")
        r = self.client.post(
            f"/api/access/grants/{area_grant[2]}/revoke",
            headers=self.auth(admin),
            json={"op_time": "2026-09-30T10:05:00+00:00"},
        )
        self.assertEqual(r.json()["sessions_closed"], 1)
        response = self.client.get("/api/geochem", headers=self.auth(wang))
        self.assertEqual(response.status_code, 401)
        self.assertIn("revoked", response.json()["detail"])

        # 撤权迟到同样 409 并关闭旧会话（用李工：撤包级授权，他在 AREA-A1 还有区级）
        self.client.post(
            "/api/access/grants/1/revoke", headers=self.auth(admin),
            json={"op_time": "2026-09-30T11:00:00+00:00"},
        )
        stale = self.client.post(
            "/api/access/grants/2/revoke", headers=self.auth(admin),
            json={"op_time": "2026-09-30T10:59:00+00:00"},
        )
        self.assertEqual(stale.status_code, 409)

    # ------------------------------------------------------------ 会话门禁

    def test_geochem_requires_session(self) -> None:
        self.assertEqual(self.client.get("/api/geochem").status_code, 401)
        self.assertEqual(
            self.client.post("/api/geochem", json={"values": {}}).status_code,
            401,
        )

    def test_create_requires_package_and_write(self) -> None:
        partner = self.login("partner.b")
        response = self.client.post(
            "/api/geochem", headers=self.auth(partner),
            json={"values": {"样品编号": "GEOC-X", "样品类型": "土壤样",
                             "采样点位": "X-1", "样品包编号": "PKG-A111"}},
        )
        # 跨单位只读：包存在但无写权 → 404 口径
        self.assertEqual(response.status_code, 404)

        admin = self.login("admin.a")
        response = self.client.post(
            "/api/geochem", headers=self.auth(admin),
            json={"values": {"样品编号": "GEOC-006", "样品类型": "土壤地球化学样",
                             "采样点位": "PKG-A112-点06", "样品包编号": "PKG-A112"}},
        )
        self.assertTrue(response.json()["ok"], response.json())
        entry = response.json()["entry"]
        self.assertEqual(entry["项目组编号"], "GRP-A11")
        self.assertEqual(entry["采样区编号"], "AREA-A1")


if __name__ == "__main__":
    unittest.main()
