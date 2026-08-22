"""打包時把「主機實際生效的樣板」發布進 defaults\\parts\\。

兩層要驗：

1. `cardparts.select_effective_parts()` 挑出來的檔案清單，必須跟
   `load_part_templates()` 實際會用的比對池**一對一**——不能把主機上已經被
   自己樣板整組取代的內建糊圖也搬出去（那會把「糊掉的 7 搶走清楚的 2」
   重新帶給其他電腦），也不能漏掉還在墊背的內建樣板。
2. `build_exe._publish_parts_to_defaults()` 的鏡像行為：新增/更新/移除都要對，
   非樣板檔不碰，來源空的時候完全不動（從全新 checkout 打包不可以把內建
   樣板清空）。
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING = os.path.join(ROOT, "packaging")
for path in (ROOT, PACKAGING):
    if path not in sys.path:
        sys.path.insert(0, path)

import cv2  # noqa: E402

import build_exe  # noqa: E402
from src import cardparts as cp  # noqa: E402


def blob(size, seed=0):
    rng = np.random.default_rng(seed)
    big = (rng.random((size[1] * 2, size[0] * 2)) > 0.4).astype(np.uint8) * 255
    return cv2.resize(big, size, interpolation=cv2.INTER_AREA)


def speck(size):
    w, h = size
    img = np.zeros((h, w), np.uint8)
    img[0:3, 0:3] = 255
    return img


class PartsSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.parts = os.path.join(self.tmp, "card_templates", "parts")
        self.bundled = os.path.join(self.tmp, "defaults", "parts")
        os.makedirs(self.parts)
        os.makedirs(self.bundled)

    def write(self, folder, fname, seed=None, image=None):
        kind = fname.split("_")[0]
        img = image if image is not None else blob(cp.PART_SIZES[kind], seed or 1)
        cv2.imwrite(os.path.join(folder, fname), img)

    def bundle_and_copy(self, fname, seed):
        """內建樣板＋card_templates 裡那份原封複本（正常安裝的狀態）。"""
        self.write(self.bundled, fname, seed)
        shutil.copy2(os.path.join(self.bundled, fname),
                     os.path.join(self.parts, fname))


class TestSelectEffectiveParts(PartsSetup):
    def test_selection_matches_what_the_loader_actually_uses(self):
        # suit 組 (S, C)：兩個標籤各 3 張自己的 → 整組丟內建
        for i in range(3):
            self.write(self.parts, f"suit_S_{10 + i}.png", seed=10 + i)
            self.write(self.parts, f"suit_C_{10 + i}.png", seed=20 + i)
        self.bundle_and_copy("suit_S_1.png", seed=30)   # 已被取代的內建
        # rank 組：自己的不夠 → 內建墊背要跟著出貨
        self.write(self.parts, "rank_5_9.png", seed=40)
        self.bundle_and_copy("rank_5_1.png", seed=41)
        self.bundle_and_copy("rank_A_1.png", seed=42)
        # 裁壞的小點：不出貨
        self.write(self.parts, "rank_7_1.png", image=speck(cp.PART_SIZES["rank"]))

        selected = cp.select_effective_parts(self.parts, self.bundled)
        self.assertNotIn("suit_S_1.png", selected, "已淘汰的內建糊圖被搬出去了")
        self.assertIn("rank_5_1.png", selected, "還在墊背的內建樣板被漏掉了")
        self.assertIn("rank_5_9.png", selected)
        self.assertIn("rank_A_1.png", selected)
        self.assertNotIn("rank_7_1.png", selected, "裁壞的小點被出貨了")

        # 決定性驗證：把選出的檔案複製到乾淨資料夾單獨載入，
        # 比對池要跟主機上 load_part_templates(parts, bundled) 一模一樣。
        shipped = os.path.join(self.tmp, "shipped")
        os.makedirs(shipped)
        for name in selected:
            shutil.copy2(os.path.join(self.parts, name),
                         os.path.join(shipped, name))
        on_master = cp.load_part_templates(self.parts, self.bundled)
        on_consumer = cp.load_part_templates(shipped, None)
        for kind in ("rank", "suit", "pip"):
            self.assertEqual(set(on_master[kind]), set(on_consumer[kind]),
                             f"{kind} 的標籤集合不一致")
            for key, imgs in on_master[kind].items():
                other = on_consumer[kind][key]
                # 比對是「取池子裡的最高分」，順序無關 —— 主機池把自己的排前面、
                # 出貨後照檔名排序，所以用多重集合比內容，不比順序。
                self.assertEqual(sorted(i.tobytes() for i in imgs),
                                 sorted(i.tobytes() for i in other),
                                 f"{kind}_{key} 的樣板內容不一致")

    def test_empty_or_missing_source_selects_nothing(self):
        self.assertEqual(cp.select_effective_parts(
            os.path.join(self.tmp, "nowhere"), self.bundled), [])
        self.assertEqual(cp.select_effective_parts(self.parts, self.bundled), [])


class TestPublishMirror(PartsSetup):
    def _publish(self):
        """對暫存資料夾跑發布步驟，並把它的訊息**吞掉**。

        這些訊息（「主機實際生效的 6 張」「⚠ 這一套還缺：點數 A、2、3…」）
        描述的是測試自己捏出來的迷你假資料，不是使用者的真實樣板。
        2026-08-23 有人跑完測試套件看到這幾行，以為自己的樣板全毀了 ——
        測試的演練輸出混進主控台只會嚇人，全部收進 buffer。
        """
        original = build_exe.ROOT
        build_exe.ROOT = self.tmp
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                build_exe._publish_parts_to_defaults()
        finally:
            build_exe.ROOT = original

    def test_mirror_adds_updates_and_removes(self):
        for i in range(3):
            self.write(self.parts, f"suit_H_{i + 1}.png", seed=50 + i)
            self.write(self.parts, f"suit_D_{i + 1}.png", seed=60 + i)
        # defaults 裡的舊狀態：一張內容過期、一張已經沒人用
        self.write(self.bundled, "suit_H_1.png", seed=99)      # 會被更新
        self.write(self.bundled, "suit_H_9.png", seed=98)      # 會被移除
        with open(os.path.join(self.bundled, "notes.txt"), "w") as f:
            f.write("keep me")                                  # 非樣板檔不碰

        self._publish()

        names = sorted(os.listdir(self.bundled))
        self.assertIn("suit_H_1.png", names)
        self.assertIn("suit_D_3.png", names)
        self.assertNotIn("suit_H_9.png", names, "沒人用的舊內建檔應該被移除")
        self.assertIn("notes.txt", names, "非樣板檔被誤刪")
        # 更新真的發生了：內容要等於主機那份
        with open(os.path.join(self.bundled, "suit_H_1.png"), "rb") as a, \
                open(os.path.join(self.parts, "suit_H_1.png"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_empty_source_leaves_defaults_untouched(self):
        """從全新 checkout（沒有 card_templates）打包，不可以把內建樣板清空。"""
        self.write(self.bundled, "rank_A_1.png", seed=70)
        shutil.rmtree(self.parts)
        self._publish()
        self.assertEqual(sorted(os.listdir(self.bundled)), ["rank_A_1.png"])


if __name__ == "__main__":
    unittest.main()
