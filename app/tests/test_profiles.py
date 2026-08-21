"""每種長寬比一組校準。

這個檔案的重點不是「功能有沒有動」，而是**校準資料會不會被弄丟**。
這個專案已經因為「自動同步」覆蓋掉逐格校準的手牌位置與幾十個自抓樣板一次了，
所以下面每一條規則都要有測試守著：

1. 遷移舊設定檔時，座標值一個都不准變。
2. 在 A 比例下存檔，不能動到 B 比例那一組。
3. 借用別組座標的狀態下存檔，要生出**新的**一組，不能寫回被借的那一組。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import profiles as pf  # noqa: E402

R21 = {"table_marker": {"x": 0.1289, "y": 0.0365, "w": 0.1445, "h": 0.1826}}
P21 = {"start_round": {"x": 0.5, "y": 0.8128}}
R16 = {"table_marker": {"x": 0.1000, "y": 0.0400, "w": 0.1800, "h": 0.2000}}
P16 = {"start_round": {"x": 0.5, "y": 0.7900}}


def legacy_cfg() -> dict:
    """升級前的設定檔：只有頂層 regions/points + calibration。"""
    return {
        "aspect_ratio_tolerance": 0.02,
        "calibration": {"client_width": 1843, "client_height": 778},
        "regions": copy.deepcopy(R21),
        "points": copy.deepcopy(P21),
    }


class TestLabels(unittest.TestCase):
    def test_real_measurements_get_readable_labels(self):
        # 使用者實際校準過的兩個尺寸，都該落在 21:9
        self.assertEqual(pf.label_for(1843, 778), "21:9")
        self.assertEqual(pf.label_for(1937, 817), "21:9")
        self.assertEqual(pf.label_for(1639, 691), "21:9")
        self.assertEqual(pf.label_for(1920, 1080), "16:9")
        self.assertEqual(pf.label_for(1024, 768), "4:3")

    def test_odd_ratios_fall_back_to_a_number(self):
        # 2.0 距離最近的 16:9 有 12.5%，不該被硬塞進 16:9
        self.assertEqual(pf.label_for(2000, 1000), "2.00:1")

    def test_invalid_sizes_do_not_crash(self):
        self.assertEqual(pf.label_for(0, 0), "?")
        self.assertEqual(pf.label_for(100, 0), "?")
        self.assertEqual(pf.aspect_of(0, 5), 0.0)


class TestMigration(unittest.TestCase):
    def test_coordinates_are_not_touched(self):
        cfg = legacy_cfg()
        before = copy.deepcopy(cfg["regions"])
        self.assertTrue(pf.ensure_profiles(cfg))
        self.assertEqual(cfg["regions"], before)
        self.assertEqual(cfg["calibration_profiles"][0]["regions"], before)

    def test_label_and_active_come_from_the_calibration_size(self):
        cfg = legacy_cfg()
        pf.ensure_profiles(cfg)
        self.assertEqual(cfg["active_profile"], "21:9")
        self.assertEqual(cfg["calibration_profiles"][0]["client_width"], 1843)

    def test_running_twice_changes_nothing(self):
        cfg = legacy_cfg()
        pf.ensure_profiles(cfg)
        snapshot = copy.deepcopy(cfg)
        self.assertFalse(pf.ensure_profiles(cfg))
        self.assertEqual(cfg, snapshot)

    def test_config_without_calibration_size_still_keeps_coordinates(self):
        cfg = {"regions": copy.deepcopy(R21), "points": copy.deepcopy(P21)}
        self.assertTrue(pf.ensure_profiles(cfg))
        self.assertEqual(cfg["calibration_profiles"][0]["regions"], R21)
        self.assertEqual(cfg["calibration_profiles"][0]["label"], "未知比例")

    def test_empty_config_is_left_alone(self):
        cfg: dict = {}
        self.assertFalse(pf.ensure_profiles(cfg))
        self.assertEqual(pf.get_profiles(cfg), [])


class TestSelection(unittest.TestCase):
    def _two_profiles(self) -> dict:
        cfg = legacy_cfg()
        pf.ensure_profiles(cfg)
        cfg["calibration_profiles"].append(
            pf._make_profile("16:9", 1920, 1080, R16, P16))
        return cfg

    def test_same_ratio_different_size_reuses_the_profile(self):
        cfg = self._two_profiles()
        # 1937x817 與校準時的 1843x778 是同一個比例，只是視窗大小不同
        selection = pf.select_for_window(cfg, 1937, 817)
        self.assertTrue(selection["matched"])
        self.assertEqual(selection["label"], "21:9")
        self.assertEqual(cfg["regions"], R21)

    def test_switching_ratio_switches_coordinates(self):
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1920, 1080)
        self.assertEqual(cfg["regions"], R16)
        self.assertEqual(cfg["active_profile"], "16:9")
        pf.select_for_window(cfg, 1843, 778)
        self.assertEqual(cfg["regions"], R21)

    def test_unknown_ratio_is_derived_not_borrowed(self):
        """沒有這個比例的校準時**算**出一組，不是拿別人的頂著。

        遊戲的 UI 排在置中的 16:9 內容框裡，所以換算是精確的
        （見 test_content_box.py 的實測驗證）。
        """
        cfg = self._two_profiles()
        selection = pf.select_for_window(cfg, 1024, 768)   # 4:3，本來沒有
        self.assertTrue(selection["matched"])
        self.assertEqual(selection["derived_from"], "16:9")  # 4:3 離 16:9 比較近
        self.assertIn("換算", pf.summarize_selection(selection))
        self.assertEqual(cfg["active_profile"], "4:3")

    def test_deriving_does_not_touch_the_source_profile(self):
        """換算出新的一組，來源那一組必須毫髮無傷。"""
        cfg = self._two_profiles()
        before = copy.deepcopy(pf.find_by_label(cfg, "16:9"))
        pf.select_for_window(cfg, 1024, 768)
        self.assertEqual(pf.find_by_label(cfg, "16:9"), before)
        self.assertEqual(pf.find_by_label(cfg, "21:9")["regions"], R21)

    def test_derived_coordinates_actually_move(self):
        """換算過的座標必須跟來源不一樣 —— 一樣就代表換算根本沒作用。"""
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1024, 768)
        derived = pf.find_by_label(cfg, "4:3")
        self.assertNotEqual(derived["regions"]["table_marker"],
                            R16["table_marker"])
        # 但仍然是合法的比例值
        for key, value in derived["regions"]["table_marker"].items():
            self.assertGreaterEqual(value, -0.5, key)
            self.assertLessEqual(value, 1.5, key)

    def test_first_ever_selection_adopts_the_default_layout(self):
        cfg = {"regions": copy.deepcopy(R21), "points": copy.deepcopy(P21),
               "calibration_profiles": []}
        selection = pf.select_for_window(cfg, 1920, 1080)
        self.assertTrue(selection["matched"])
        self.assertEqual(cfg["active_profile"], "16:9")
        self.assertEqual(len(cfg["calibration_profiles"]), 1)

    def test_activate_deep_copies(self):
        """profile 與工作副本共用 dict，等於使用者一微調就寫進 profile，
        規則 2 的保護會整個失效。"""
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1843, 778)
        cfg["regions"]["table_marker"]["x"] = 0.999
        stored = pf.find_by_label(cfg, "21:9")
        self.assertEqual(stored["regions"]["table_marker"]["x"], 0.1289)


class TestSyncActive(unittest.TestCase):
    def _two_profiles(self) -> dict:
        cfg = legacy_cfg()
        pf.ensure_profiles(cfg)
        cfg["calibration_profiles"].append(
            pf._make_profile("16:9", 1920, 1080, R16, P16))
        return cfg

    def test_saving_writes_into_the_active_profile_only(self):
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1920, 1080)
        cfg["regions"]["table_marker"]["x"] = 0.42
        pf.sync_active(cfg)
        self.assertEqual(pf.find_by_label(cfg, "16:9")["regions"]["table_marker"]["x"], 0.42)
        # 另一組完全沒被動到
        self.assertEqual(pf.find_by_label(cfg, "21:9")["regions"], R21)

    def test_editing_a_derived_profile_writes_into_that_profile(self):
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1024, 768)      # 換算出 4:3 並生效
        cfg["regions"]["table_marker"]["x"] = 0.31
        saved = pf.sync_active(cfg)
        self.assertEqual(saved["label"], "4:3")
        self.assertEqual(saved["regions"]["table_marker"]["x"], 0.31)
        # 其他兩組必須毫髮無傷
        self.assertEqual(pf.find_by_label(cfg, "16:9")["regions"], R16)
        self.assertEqual(pf.find_by_label(cfg, "21:9")["regions"], R21)

    def test_calibrating_clears_the_derived_mark(self):
        """換算出來的那組，使用者親手重新框選存檔之後就不再標「換算而來」。"""
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1024, 768)
        self.assertEqual(pf.find_by_label(cfg, "4:3")["derived_from"], "16:9")
        pf.sync_active(cfg)
        self.assertIsNone(pf.find_by_label(cfg, "4:3")["derived_from"])

    def test_sync_is_a_noop_without_regions(self):
        self.assertIsNone(pf.sync_active({}))
        self.assertIsNone(pf.sync_active({"regions": "not a dict", "points": {}}))

    def test_no_duplicate_labels(self):
        cfg = self._two_profiles()
        pf.select_for_window(cfg, 1024, 768)
        pf.sync_active(cfg)
        pf.select_for_window(cfg, 1024, 768)
        pf.sync_active(cfg)
        labels = [p["label"] for p in pf.get_profiles(cfg)]
        self.assertEqual(len(labels), len(set(labels)))


class TestSaveAsAndRemove(unittest.TestCase):
    def _cfg(self) -> dict:
        cfg = legacy_cfg()
        pf.ensure_profiles(cfg)
        return cfg

    def test_save_as_new_ratio_keeps_the_old_one(self):
        cfg = self._cfg()
        pf.select_for_window(cfg, 1920, 1080)     # 從 21:9 換算出 16:9
        pf.save_as(cfg, 1920, 1080)
        self.assertEqual(len(pf.get_profiles(cfg)), 2)
        self.assertEqual(pf.find_by_label(cfg, "21:9")["regions"], R21)
        # 明確按過「另存為這個比例」就算人工確認過了
        self.assertIsNone(pf.find_by_label(cfg, "16:9")["derived_from"])

    def test_save_as_same_ratio_overwrites_in_place(self):
        cfg = self._cfg()
        cfg["regions"]["table_marker"]["x"] = 0.5
        pf.save_as(cfg, 1937, 817)                # 同樣是 21:9
        self.assertEqual(len(pf.get_profiles(cfg)), 1)
        self.assertEqual(pf.find_by_label(cfg, "21:9")["regions"]["table_marker"]["x"], 0.5)
        self.assertEqual(pf.find_by_label(cfg, "21:9")["client_width"], 1937)

    def test_remove(self):
        cfg = self._cfg()
        self.assertTrue(pf.remove(cfg, "21:9"))
        self.assertEqual(pf.get_profiles(cfg), [])
        self.assertIsNone(cfg["active_profile"])
        self.assertFalse(pf.remove(cfg, "沒有這一組"))

    def test_limit_drops_seeded_profiles_first(self):
        cfg = self._cfg()
        profiles = pf.get_profiles(cfg)
        for i in range(pf.MAX_PROFILES + 3):
            profiles.append(pf._make_profile(f"fake{i}", 1000 + i, 500, {}, {},
                                            seeded_from="21:9"))
        pf._enforce_limit(profiles, keep_label="21:9")
        self.assertLessEqual(len(profiles), pf.MAX_PROFILES)
        # 真正校準過的那一組必須留下來
        self.assertIsNotNone(pf.find_by_label(cfg, "21:9"))


class TestEvictionOrder(unittest.TestCase):
    """profile 數量到上限時，人工校準的那一組必須活到最後。

    背景：主控台現在會自動偵測比例，把遊戲視窗慢慢拖大的過程會經過一堆怪比例
    （2.08:1、1.95:1…），每一種都會生出一組**換算來的** profile。舊版的淘汰順序是
    「seeded_from 的 → 最舊的」，而使用者手工校準的那組通常正好是最舊的第一組
    —— 拖幾次視窗就足以把它擠掉，而且完全沒有徵兆。
    """

    def _profiles(self) -> list[dict]:
        hand = pf._make_profile("21:9", 1843, 778, {"a": 1}, {"b": 2})
        derived = pf._make_profile("16:9", 1600, 900, {}, {})
        derived["derived_from"] = "21:9"
        seeded = pf._make_profile("4:3", 1200, 900, {}, {}, seeded_from="21:9")
        return [hand, derived, seeded]

    def test_seeded_goes_first(self):
        profiles = self._profiles()
        self.assertEqual(pf._pick_victim(profiles, keep_label=None).get("label"), "4:3")

    def test_derived_goes_before_hand_calibrated(self):
        profiles = [p for p in self._profiles() if not p.get("seeded_from")]
        self.assertEqual(pf._pick_victim(profiles, keep_label=None).get("label"), "16:9")

    def test_hand_calibrated_survives_a_flood_of_derived_profiles(self):
        profiles = [pf._make_profile("21:9", 1843, 778, {"a": 1}, {"b": 2})]
        for i in range(pf.MAX_PROFILES + 5):
            junk = pf._make_profile(f"{1.9 + i / 100:.2f}:1", 1900 + i, 1000, {}, {})
            junk["derived_from"] = "21:9"
            profiles.append(junk)
            pf._enforce_limit(profiles, keep_label=junk["label"])
        self.assertLessEqual(len(profiles), pf.MAX_PROFILES)
        survivor = [p for p in profiles if p.get("label") == "21:9"]
        self.assertEqual(len(survivor), 1, "手工校準的 21:9 被擠掉了")
        self.assertEqual(survivor[0]["regions"], {"a": 1})

    def test_keep_label_is_never_evicted(self):
        profiles = self._profiles()
        victim = pf._pick_victim(profiles, keep_label="4:3")
        self.assertNotEqual(victim.get("label"), "4:3")

    def test_nothing_to_evict_returns_none(self):
        only = pf._make_profile("21:9", 1843, 778, {}, {})
        self.assertIsNone(pf._pick_victim([only], keep_label="21:9"))


class TestSameLabelDifferentAspect(unittest.TestCase):
    """好唸的名字容許 4%，共用校準只容許 2% —— 中間那一段不可以吃掉人工校準。

    `label_for` 的 LABEL_TOLERANCE 是 0.04，`find_match` 用的 tolerance 是 0.02。
    所以有一段尺寸會拿到「跟現有那組同名、但其實是另一種比例」的標籤，而
    `select_for_window` 原本是「同名就丟掉」——手工校準的成果會被一組換算來的
    悄悄取代，而且完全沒有徵兆。

    實例：手工校準的 21:9 是 1937x817（2.3708）。視窗變成 3420x1500（2.28）時，
    label_for 仍然叫它「21:9」（差 2.3%，命名範圍內），但跟 2.3708 差 3.83%
    （超過共用範圍）。主控台改成「尺寸一變就自動偵測」之後這條路很容易踩到。
    """

    HAND = (1937, 817)          # 使用者手工校準的 21:9
    CLASH = (3420, 1500)        # 也叫 21:9，但比例差 3.83%

    def _cfg(self) -> dict:
        cfg = {
            "aspect_ratio_tolerance": 0.02,
            "regions": {"table_marker": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}},
            "points": {"high_button": {"x": 0.75, "y": 0.46}},
            "calibration": {"client_width": self.HAND[0], "client_height": self.HAND[1]},
            "calibration_profiles": [],
            "active_profile": None,
        }
        pf.ensure_profiles(cfg)
        return cfg

    def test_the_two_sizes_really_do_collide_on_the_label(self):
        """先確認這個測試在測真的東西：同名、但比例差超過容許範圍。"""
        self.assertEqual(pf.label_for(*self.HAND), pf.label_for(*self.CLASH))
        delta = pf.relative_delta(self.CLASH[0] / self.CLASH[1],
                                  self.HAND[0] / self.HAND[1])
        self.assertGreater(delta, 0.02)
        self.assertLess(delta, pf.LABEL_TOLERANCE)

    def test_hand_calibration_survives_a_same_label_derive(self):
        cfg = self._cfg()
        pf.select_for_window(cfg, *self.CLASH)
        survivors = [p for p in pf.get_profiles(cfg)
                     if (p["client_width"], p["client_height"]) == self.HAND]
        self.assertEqual(len(survivors), 1, "手工校準的那一組被換掉了")
        self.assertTrue(pf.is_hand_calibrated(survivors[0]))
        self.assertEqual(survivors[0]["regions"]["table_marker"],
                         {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4})

    def test_the_derived_profile_gets_a_size_qualified_label(self):
        cfg = self._cfg()
        selection = pf.select_for_window(cfg, *self.CLASH)
        self.assertEqual(selection["label"], f"21:9 ({self.CLASH[0]}x{self.CLASH[1]})")
        self.assertEqual(cfg["active_profile"], selection["label"])

    def test_labels_stay_unique(self):
        """清單裡出現兩個同名項目，下拉選單就永遠只挑得到第一個。"""
        cfg = self._cfg()
        pf.select_for_window(cfg, *self.CLASH)
        pf.select_for_window(cfg, *self.HAND)
        pf.select_for_window(cfg, *self.CLASH)
        labels = [p["label"] for p in pf.get_profiles(cfg)]
        self.assertEqual(len(labels), len(set(labels)), labels)

    def test_a_derived_profile_may_still_be_replaced(self):
        """換算來的隨時能再算一次，不需要為它保留名字。"""
        cfg = self._cfg()
        pf.select_for_window(cfg, *self.CLASH)
        before = len(pf.get_profiles(cfg))
        pf.select_for_window(cfg, self.CLASH[0], self.CLASH[1])
        self.assertEqual(len(pf.get_profiles(cfg)), before)

    def test_save_as_does_not_create_a_duplicate_label(self):
        cfg = self._cfg()
        cfg["calibration"] = {"client_width": self.CLASH[0], "client_height": self.CLASH[1]}
        created = pf.save_as(cfg, *self.CLASH)
        labels = [p["label"] for p in pf.get_profiles(cfg)]
        self.assertEqual(len(labels), len(set(labels)), labels)
        self.assertEqual(created["label"], f"21:9 ({self.CLASH[0]}x{self.CLASH[1]})")
        self.assertTrue(any((p["client_width"], p["client_height"]) == self.HAND
                            for p in pf.get_profiles(cfg)))

    def test_save_as_still_overwrites_the_same_aspect(self):
        """同一種比例（誤差在容許範圍內）按「另存」仍然是覆蓋，不是一直新增。"""
        cfg = self._cfg()
        before = len(pf.get_profiles(cfg))
        pf.save_as(cfg, 3440, 1440)          # 跟 1937x817 差 0.76%，算同一種
        self.assertEqual(len(pf.get_profiles(cfg)), before)
        self.assertEqual(pf.find_by_label(cfg, "21:9")["client_width"], 3440)


class TestIsHandCalibrated(unittest.TestCase):
    def test_plain_profile_counts_as_hand_calibrated(self):
        self.assertTrue(pf.is_hand_calibrated(pf._make_profile("21:9", 1937, 817, {}, {})))

    def test_seeded_and_derived_do_not(self):
        seeded = pf._make_profile("16:9", 1600, 900, {}, {}, seeded_from="21:9")
        self.assertFalse(pf.is_hand_calibrated(seeded))
        derived = pf._make_profile("4:3", 1200, 900, {}, {})
        derived["derived_from"] = "21:9"
        self.assertFalse(pf.is_hand_calibrated(derived))


if __name__ == "__main__":
    unittest.main()
