"""化探三级权限域端到端测试（unittest，内存仓储，无需外部依赖）。

覆盖：
1. 未登录拒绝；本单位默认读写、跨单位默认不可见；
2. 授权矩阵仅本单位可调整，跨单位调用 403；跨单位授权封顶只读；
3. 采样区/项目组继承；单个样品包只读例外；
4. 越权改采样点位拒绝，且错误/数据不泄露样品类型等字段；
5. 授权结论同步台账、点位图、样品袋追溯三处，一致性校验通过；
6. 矩阵调整后历史共享引用回填、缓存失效；历史经手账号不被改写；
7. 调班/撤权按操作时间定序；成功后旧会话写操作 409，resync 后可继续。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def login(account_id: str) -> dict:
    resp = client.post("/api/security/sessions", json={"account_id": account_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def headers(session: dict) -> dict[str, str]:
    return {"X-Session-Id": session["session_id"]}


class PermissionDomainTests(unittest.TestCase):
    # ---- 1. 会话与默认归属 -------------------------------------------------

    def test_01_unauthenticated_rejected(self) -> None:
        self.assertEqual(client.get("/api/geochem").status_code, 401)
        self.assertEqual(client.get("/api/security/matrix").status_code, 401)

    def test_02_owner_unit_default_read_write(self) -> None:
        fang = login("a-fang")  # U-A，项目组 G1/G2 归属 U-A
        # P1 在 A1/G1 下，本单位账号默认读写
        resp = client.get("/api/geochem?keyword=GEOC-0001", headers=headers(fang))
        self.assertEqual(resp.status_code, 200)
        item = resp.json()["items"][0]
        self.assertEqual(item["_access_level"], "read_write")
        self.assertTrue(item["_can_write"])
        self.assertEqual(item["样品类型"], "土壤化探样")  # 本单位可见完整字段

    def test_03_cross_unit_default_invisible(self) -> None:
        zhao = login("a-zhao")  # U-B，种子仅在 A1 上有只读
        resp = client.get("/api/geochem", headers=headers(zhao))
        codes = {row["样品编号"] for row in resp.json()["items"]}
        # A1 只读 -> A1 下的 P1、P2 均可见（子级继承采样区区间）
        self.assertIn("GEOC-0001", codes)
        self.assertIn("GEOC-0002", codes)
        self.assertIn("GEOC-0003", codes)  # P2 继承 A1
        self.assertNotIn("GEOC-0004", codes)  # A2 未授权
        self.assertNotIn("GEOC-0006", codes)  # G2/A3 未授权
        # 钱检（U-B，无任何区间授权）：全部样品不可见
        qian = login("a-qian")
        resp = client.get("/api/geochem", headers=headers(qian))
        self.assertEqual(resp.json()["items"], [])

    # ---- 2. 授权矩阵：本单位才能调整，跨单位只读 -----------------------------

    def test_04_cross_unit_cannot_adjust_matrix(self) -> None:
        zhao = login("a-zhao")
        resp = client.put("/api/security/matrix", headers=headers(zhao), json={
            "account_id": "a-qian", "node_kind": "area", "node_id": "A1", "level": "read",
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "cross_unit_readonly")
        # 错误文案不得带出样品类型等敏感信息
        self.assertNotIn("化探样", resp.text)

    def test_05_cross_unit_grant_capped_at_read(self) -> None:
        fang = login("a-fang")
        # 即便本单位账号试图把外单位账号配成读写，也必须被拒绝
        resp = client.put("/api/security/matrix", headers=headers(fang), json={
            "account_id": "a-qian", "node_kind": "area", "node_id": "A2",
            "level": "read_write",
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "cross_unit_readonly")

        # 正常授予只读：赵测获得 A2 只读，P3 随采样区继承
        resp = client.put("/api/security/matrix", headers=headers(fang), json={
            "account_id": "a-zhao", "node_kind": "area", "node_id": "A2", "level": "read",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        zhao = login("a-zhao")
        access = _effective(zhao, "package", "P3")
        self.assertEqual(access["level"], "read")
        self.assertTrue(access["cross_unit"])
        self.assertFalse(access["can_write"])

    def test_06_matrix_view_readonly_for_cross_unit(self) -> None:
        zhao = login("a-zhao")
        matrix = client.get("/api/security/matrix", headers=headers(zhao)).json()
        for row in matrix["items"]:
            # 外单位在任何区间上都不具备管理权限
            self.assertFalse(row["can_manage"])
            # 配了 read_write 的区间，解析结论也只能是只读（种子无此条，用规则保证）
            self.assertNotEqual(row["effective_level"], "read_write")

    # ---- 3. 继承与只读例外 -------------------------------------------------

    def test_07_inheritance_group_to_area_to_package(self) -> None:
        fang = login("a-fang")
        # 在项目组 G2 上给赵测只读 -> A3 -> P4 逐级继承
        resp = client.put("/api/security/matrix", headers=headers(fang), json={
            "account_id": "a-zhao", "node_kind": "group", "node_id": "G2", "level": "read",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        zhao = login("a-zhao")
        area_access = _effective(zhao, "area", "A3")
        pkg_access = _effective(zhao, "package", "P4")
        self.assertEqual(area_access["level"], "read")
        self.assertEqual(pkg_access["level"], "read")
        # 继承来源应指向上级 G2
        self.assertIn("G2", pkg_access["source"])

    def test_08_package_readonly_exception(self) -> None:
        # 李勘是 U-A 本单位账号，默认读写；种子在 P3 上给了只读例外
        li = login("a-li")
        self.assertEqual(_effective(li, "package", "P3")["level"], "read")
        # 其他包仍然继承默认读写
        self.assertEqual(_effective(li, "package", "P1")["level"], "read_write")
        # 只读例外下执行样品动作必须被拒
        resp = client.post("/api/geochem/4/actions", headers=headers(li),
                           json={"action": "送样检测"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "point_write_denied")

    # ---- 4. 越权改采样点位 + 字段不泄露 -------------------------------------

    def test_09_point_update_permissions(self) -> None:
        fang = login("a-fang")  # U-A 对 P1 读写
        resp = client.put("/api/geochem/1/point", headers=headers(fang),
                          json={"采样点位": "北山-Ⅰ-018"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["entry"]["采样点位"], "北山-Ⅰ-018")

        zhao = login("a-zhao")  # 对 P1 只读
        resp = client.put("/api/geochem/1/point", headers=headers(zhao),
                          json={"采样点位": "北山-越权点"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "point_write_denied")
        detail = resp.json()["detail"]["message"]
        self.assertNotIn("化探样", detail)       # 不泄露样品类型
        self.assertNotIn("北山-越权点", detail)  # 不回显提交值

    def test_10_invisible_sample_is_404_without_fields(self) -> None:
        qian = login("a-qian")  # 对 P2 无任何授权
        resp = client.get("/api/geochem/3", headers=headers(qian))
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("水系沉积物", resp.text)

    def test_11_cross_unit_sensitive_fields_masked(self) -> None:
        zhao = login("a-zhao")
        detail = client.get("/api/geochem/1", headers=headers(zhao)).json()
        # 样品类型/分析元素/检测方法/检出限 对外单位打码/移除
        for field in ["分析元素", "检测方法", "检出限"]:
            self.assertNotIn(field, detail)
        # 点位图投影：样品类型打码但点位可见
        points = client.get("/api/security/projections/map", headers=headers(zhao)).json()["items"]
        p1 = next(p for p in points if p["package_id"] == "P1")
        self.assertEqual(p1["sample_type"], "******")
        self.assertEqual(p1["level"], "read")
        self.assertFalse(p1["can_write"])

    # ---- 5. 三处同源 -------------------------------------------------------

    def test_12_three_projections_same_source(self) -> None:
        li = login("a-li")
        report = client.get("/api/security/projections/consistency",
                            headers=headers(li)).json()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["problems"], [])

        # P3 台账只读、点位图只读、样品袋待办也只读，且指纹一致
        ledger = {r["package_id"]: r["_access_level"]
                  for r in client.get("/api/security/projections/ledger",
                                      headers=headers(li)).json()["items"]}
        pmap = {p["package_id"]: p for p in client.get(
            "/api/security/projections/map", headers=headers(li)).json()["items"]}
        bags = {b["package_id"]: b for b in client.get(
            "/api/security/projections/bag-todo", headers=headers(li)).json()["items"]}
        self.assertEqual(ledger["P3"], "read")
        self.assertEqual(pmap["P3"]["level"], "read")
        self.assertEqual(bags["P3"]["level"], "read")
        self.assertEqual(pmap["P3"]["fingerprint"], bags["P3"]["fingerprint"])

        # 无权限的包在三处都消失：钱检无任何授权，P2 在三处都不可见
        qian = login("a-qian")
        ledger_z = client.get("/api/security/projections/ledger", headers=headers(qian)).json()["items"]
        map_z = client.get("/api/security/projections/map", headers=headers(qian)).json()["items"]
        bags_z = client.get("/api/security/projections/bag-todo", headers=headers(qian)).json()["items"]
        self.assertNotIn("P2", {r["package_id"] for r in ledger_z})
        self.assertNotIn("P2", {p["package_id"] for p in map_z})
        self.assertNotIn("P2", {b["package_id"] for b in bags_z})
        check = client.get("/api/security/projections/consistency", headers=headers(qian)).json()
        self.assertTrue(check["ok"], check)

    # ---- 6. 回填 + 缓存失效 + 历史经手归属 ----------------------------------

    def test_13_grant_change_backfills_references_and_clears_cache(self) -> None:
        zhao = login("a-zhao")
        # 读一次 A2 结论，填充缓存（此时赵测已在前面用例获得 A2 只读；
        # 再由方研改成 none，缓存必须失效）
        self.assertEqual(_effective(zhao, "area", "A2")["level"], "read")

        fang = login("a-fang")
        resp = client.put("/api/security/matrix", headers=headers(fang), json={
            "account_id": "a-zhao", "node_kind": "area", "node_id": "A2", "level": "none",
            "expected_version": 1,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["cache_invalidated"])
        # 引用#2（赵测/A2，snapshot read_write）必须被回填
        self.assertGreaterEqual(data["references_backfilled"], 1)

        zhao2 = login("a-zhao")
        self.assertEqual(_effective(zhao2, "area", "A2")["level"], "none")
        self.assertEqual(_effective(zhao2, "package", "P3")["level"], "none")

        refs = client.get("/api/security/references", headers=headers(zhao2)).json()["items"]
        ref2 = next(r for r in refs if r["id"] == 2)
        self.assertEqual(ref2["current_level"], "none")
        self.assertTrue(ref2["stale"])  # 新结论比快照窄
        # 历史经手关系仍归原账号：created_by 不被回填动作覆盖
        self.assertEqual(ref2["created_by"], "a-li")
        self.assertNotEqual(ref2["backfill_by"], ref2["created_by"])

    def test_14_optimistic_version_conflict(self) -> None:
        fang = login("a-fang")
        # 以陈旧版本号再次调整 A1（种子 v1，前面未改过 A1）
        resp = client.put("/api/security/matrix", headers=headers(fang), json={
            "account_id": "a-zhao", "node_kind": "area", "node_id": "A1",
            "level": "none", "expected_version": 99,
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "version_conflict")

    # ---- 7. 调班/撤权定序与旧会话失效 ---------------------------------------

    def test_15_reassign_ordering_and_stale_session(self) -> None:
        fang = login("a-fang")
        li = login("a-li")
        # 李勘先做一次“晚”的调班
        resp = client.post("/api/security/reassign", headers=headers(fang), json={
            "account_id": "a-li", "new_unit_id": "U-B", "op_at": "2026-09-20T12:00:00",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        # 更早的迟到撤权必须 409（调班与撤权共享生命周期时间线，先做定序判定）
        resp = client.post("/api/security/revoke", headers=headers(fang), json={
            "account_id": "a-li", "op_at": "2026-09-19T08:00:00",
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "order_stale")

        # 旧会话写操作被拦（调班前建立的 li 会话）
        resp = client.post("/api/geochem/4/actions", headers=headers(li),
                           json={"action": "送样检测"})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "session_stale")
        # 旧会话读仍可用
        self.assertEqual(client.get("/api/geochem", headers=headers(li)).status_code, 200)

        # 重新同步后：李勘现在属于 U-B，对 P3 本来就是只读，调班后仍只读
        resync = client.post("/api/security/sessions/resync", headers=headers(li))
        self.assertEqual(resync.status_code, 200)
        self.assertEqual(resync.json()["unit_id"], "U-B")
        resp = client.post("/api/geochem/4/actions", headers=headers(li),
                           json={"action": "送样检测"})
        self.assertEqual(resp.status_code, 403)  # 同步后是授权拒绝，不再是会话过期

        # 复原，避免影响其他用例顺序假设
        client.post("/api/security/reassign", headers=headers(fang), json={
            "account_id": "a-li", "new_unit_id": "U-A", "op_at": "2026-09-21T08:00:00",
        })

    def test_16_revoke_kills_sessions_and_writes(self) -> None:
        qian = login("a-qian")  # 赵测同属 U-B，由钱检发起撤权
        zhao = login("a-zhao")
        # 先成功一次撤权
        resp = client.post("/api/security/revoke", headers=headers(qian), json={
            "account_id": "a-zhao", "op_at": "2026-09-22T09:00:00",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        # 迟到撤权 409（更早的时间；方研属 U-A 也必须先吃到定序失败而不是 403）
        fang = login("a-fang")
        resp = client.post("/api/security/revoke", headers=headers(fang), json={
            "account_id": "a-zhao", "op_at": "2026-09-21T09:00:00",
        })
        self.assertEqual(resp.status_code, 409)
        # 旧会话直接失效：读也不行（会话已被摘除）
        self.assertEqual(client.get("/api/geochem", headers=headers(zhao)).status_code, 401)
        # 无法再登录
        resp = client.post("/api/security/sessions", json={"account_id": "a-zhao"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "account_inactive")

    def test_17_cross_unit_cannot_reassign_or_revoke(self) -> None:
        qian = login("a-qian")  # U-B，去撤 U-A 的账号
        resp = client.post("/api/security/revoke", headers=headers(qian),
                           json={"account_id": "a-fang"})
        self.assertEqual(resp.status_code, 403)
        resp = client.post("/api/security/reassign", headers=headers(qian), json={
            "account_id": "a-fang", "new_unit_id": "U-B",
        })
        self.assertEqual(resp.status_code, 403)


def _effective(session: dict, kind: str, node_id: str) -> dict:
    """通过层级接口不方便直接取结论，直接走服务层断言。"""
    from app.services.security import security
    return security.effective_access(str(session["account_id"]), kind, node_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
