"""主流程：F9 啟動/停止事件監聽 + 主迴圈（畫面偵測 -> 決策 -> 點擊）。"""
from __future__ import annotations

import threading
import time

from .capture import GameCapture
from .controller import HotkeyManager, MouseController
from . import profiles as profiles_mod
from .geometry import aspect_ratio_delta, scale_factor
from .handeval import Card, classify_hand, hand_name
from .logger import log
from .paths import resolve_data_path
from .cardparts import MIN_PART_COVERAGE, RANKS, SUITS, missing_parts, unusable_parts
from .defaults_layout import BUNDLED_MARKER_WIDTH
from .recognize import (
    CardReader,
    load_card_templates,
    load_part_templates,
    load_single_template,
    part_sources,
)
from .state_machine import IDLE_SLOT_MAX_VALUE, detect_frame, expected_marker_scale
from .stats import DailyStats
from .strategy import decide_high_or_low, decide_hold, should_continue_highlow


class Bot:
    def __init__(self, config: dict, dry_run: bool = False):
        self.cfg = config
        self.dry_run = dry_run

        self.capture = GameCapture(config["window_title_substring"])
        self.mouse = MouseController(
            self.capture,
            hold_range=(
                float(config.get("click_hold_min_sec", 0.06)),
                float(config.get("click_hold_max_sec", 0.12)),
            ),
        )
        self.card_templates = load_card_templates()
        self.part_templates = load_part_templates()
        self.reader = self._build_reader()
        self.ui_templates = self._load_ui_templates(config)
        self.stats = DailyStats()

        self.running = False
        self._stop_flag = threading.Event()

        self._last_slot_signature = None
        self._pending_slot_signature = None
        self._pending_slot_count = 0

        self._last_highlow_label = None
        self._pending_highlow_label = None
        self._pending_highlow_count = 0

        self._seen_table_since_start = False
        self._logo_ever_matched = False
        self._missing_table_ticks = 0
        self._status_ticks = 0
        self._awaiting_draw_result = False
        self._draw_confirm_at = 0.0
        self._highlow_chain = 0
        self._last_hl_win_prob = 0.5
        self._last_hl_choice = "high"

        # 動作節奏：遊戲有時候會漏掉點擊（畫面還在跑動畫、或那一格沒收到滑鼠事件），
        # 舊版只用一個「這個對話框已經處理過」的旗標，漏掉就永遠卡在那裡不再點。
        # 改成記「為了哪個畫面、在什麼時候點的」，超過重試秒數畫面還沒變就再點一次。
        self._acted_state: str | None = None
        self._acted_at = 0.0
        self._act_count = 0
        self._idle_since: float | None = None

        if self.ui_templates.get("table_marker") is None:
            log("[警告] 尚未設定牌桌標記樣板 (table_marker.png)，將無法自動偵測『已達每日上限』，請先完成校準。")

        if config.get("pyautogui_failsafe", True):
            import pyautogui
            pyautogui.FAILSAFE = True

    def _build_reader(self) -> CardReader:
        return CardReader(
            card_templates=self.card_templates,
            part_templates=self.part_templates,
            threshold=self.cfg.get("match_threshold", 0.83),
            min_margin=self.cfg.get("min_match_margin", 0.02),
            part_min_score=self.cfg.get("part_min_score", 0.72),
            part_min_margin=self.cfg.get("part_min_margin", 0.05),
        )

    def _report_unusable_parts(self) -> None:
        """開始前提醒：哪些樣板檔裁壞了、不會被拿來比對。

        載入時是安靜跳過的（使用者的檔案一律不刪），不講的話他永遠不知道
        自己在「校準還沒對上這個長寬比」的狀態下存了幾個廢檔。
        """
        try:
            from .paths import parts_dir
            bad = unusable_parts(parts_dir())
        except Exception:
            return
        if not bad:
            return
        names = "、".join(f"{n}({c})" for n, c in bad[:8])
        log(f"[樣板] 有 {len(bad)} 個樣板檔裁壞了（圖案只佔畫布不到 "
            f"{MIN_PART_COVERAGE:.0%}，等於一個小點），已略過不用：{names}"
            f"{' …' if len(bad) > 8 else ''}")
        log("        通常是「校準還沒對上目前的長寬比」時按了儲存，"
            "角落只切到花色的一小角。到「點數/花色樣板」分頁刪掉再重抓即可。")

    def _report_part_sources(self) -> None:
        """開始前提醒：哪些點數／花色還在用內建（比較糊）的樣板。"""
        try:
            sources = part_sources()
        except Exception:
            return
        for kind, title in (("rank", "點數"), ("suit", "花色")):
            table = sources.get(kind) or {}
            names = RANKS if kind == "rank" else SUITS
            missing = [n for n in names if n not in table]
            borrowed = [n for n in names if table.get(n) is False]
            if missing:
                log(f"[樣板] 還沒有任何{title}樣板：{' '.join(missing)}")
            if borrowed:
                log(
                    f"[樣板] 這些{title}還在用內建樣板（內建是縮圖放大的，比較容易認錯）："
                    f"{' '.join(borrowed)} —— 在「點數/花色樣板」分頁抓一次自己的就會自動改用你的。"
                )

    def _load_ui_templates(self, config: dict) -> dict:
        mapping = {
            "table_marker": "table_marker_image",
            "draw_prompt": "draw_prompt_image",
            "congrats": "congrats_marker_image",
            "challenge": "challenge_marker_image",
            "fail": "fail_marker_image",
            "poker_fail": "poker_fail_marker_image",
            "max_win": "max_win_marker_image",
        }
        loaded = {}
        for key, cfg_key in mapping.items():
            rel = config.get("templates", {}).get(cfg_key, "")
            loaded[key] = load_single_template(resolve_data_path(rel)) if rel else None
        return loaded

    # ---------- 熱鍵callback ----------

    def toggle(self) -> None:
        self.running = not self.running
        if self.running:
            mode = "除錯模式（只判斷、不點擊）" if self.dry_run else "正式模式（會實際點擊）"
            log(f"=== 已啟動 (F9)，{mode} ===")
            loaded = [name for name, img in self.ui_templates.items() if img is not None]
            card_n = sum(len(v) for v in self.card_templates.values())
            miss_rank, miss_suit = missing_parts(self.part_templates)
            n_rank = len(self.part_templates.get("rank") or {})
            n_suit = len(self.part_templates.get("suit") or {})
            log(
                f"畫面標記樣板：{('、'.join(loaded) if loaded else '無')}；"
                f"點數樣板 {n_rank}/13、花色樣板 {n_suit}/4；整張卡面樣板 {card_n} 張"
            )
            if miss_rank or miss_suit:
                log(
                    f"[提醒] 還缺點數 {('、'.join(miss_rank) or '無')}；"
                    f"花色 {('、'.join(miss_suit) or '無')}。"
                    "缺的那些牌會辨識失敗，可到「點數/花色樣板」分頁補齊。"
                )
            self._report_part_sources()
            self._report_unusable_parts()
            limit = int(self.cfg.get("daily_max_wins", 2))
            done = self._max_win_count()
            log(f"今日已達最高獲得金額 {done}/{limit} 次"
                + ("（已用完，一偵測到上限畫面就會停止）" if done >= limit else ""))
            if self.ui_templates.get("max_win") is None:
                log(
                    "[提醒] 還沒有「已達最高獲得金額」的標記樣板，無法自動判斷每日上限。"
                    "請到「校準」分頁校準『已達最高獲得金額標記』與『上限畫面的「再玩一次」』。"
                )

            if not self.reader.ready:
                log(
                    "[警告] 完全沒有牌面樣板，手牌與比大小一定會是 0/5、認不到牌。"
                    "請先到「點數/花色樣板」分頁蒐集（只要 13 個點數 + 4 個花色）。"
                )
            if not self.capture.locate():
                log("[警告] 目前找不到遊戲視窗，請確認遊戲已開啟且標題設定正確。")
            else:
                self._check_window_geometry()
            self._reset_round_state()
        else:
            log("=== 已停止 (F9) ===")

    def _check_window_geometry(self) -> None:
        """依視窗當下的長寬比挑一組校準，並回報縮放倍率是否還夠用。

        座標採用比例式，所以視窗**等比例**縮放沒問題；長寬比一改變，遊戲的排版
        也會跟著動，所以改成每種比例各存一組校準（見 src/profiles.py），
        這裡負責挑出對應的那一組。挑不到就借最接近的頂著，並把「這是借的」講清楚。
        """
        cur_w, cur_h = self.capture.get_client_size()
        if cur_w <= 0 or cur_h <= 0:
            return

        selection = profiles_mod.select_for_window(self.cfg, cur_w, cur_h)
        # 記住這次的比例，_maybe_reselect_profile() 才不會在第一個 tick 又挑一次
        self._last_aspect = profiles_mod.aspect_of(cur_w, cur_h)
        log(f"視窗尺寸 {cur_w}x{cur_h}（{profiles_mod.label_for(cur_w, cur_h)}）"
            f"　{profiles_mod.summarize_selection(selection)}")
        if not selection.get("matched"):
            log("[警告] 目前這個長寬比沒有專屬校準，位置會歪。"
                "請到「校準」分頁按『另存為這個比例的校準』再重新框選一次。")

        ref = self.cfg.get("calibration", {})
        ref_w, ref_h = ref.get("client_width", 0), ref.get("client_height", 0)
        if ref_w <= 0 or ref_h <= 0:
            return  # 沒有記錄校準尺寸，無法比較縮放倍率

        delta = aspect_ratio_delta(cur_w, cur_h, ref_w, ref_h)
        scale = scale_factor(cur_w, ref_w)
        if delta > self.cfg.get("aspect_ratio_tolerance", 0.02):
            log(f"（這組校準是在 {ref_w}x{ref_h} 量的，長寬比差 {delta:.1%}）")
        if scale < 0.6:
            log(
                f"[警告] 視窗已縮小到校準時的 {scale:.2f} 倍，卡牌像素變少可能造成辨識率下降；"
                "若常常認錯牌，可調低 config.json 的 match_threshold 或放大視窗。"
            )

        tmpl_scale = expected_marker_scale(self.cfg, cur_w, cur_h)
        tmpl_w = self.cfg.get("templates", {}).get("capture_client_width") or BUNDLED_MARKER_WIDTH
        if abs(tmpl_scale - 1.0) > 0.05:
            log(
                f"畫面標記樣板是在 {tmpl_w} 寬的視窗下擷取的，目前 {cur_w} 寬，"
                f"比對時會把樣板放大/縮小 {tmpl_scale:.2f} 倍。"
                "若標記分數偏低，建議在「校準」分頁對著實機畫面重新框選一次六個標記"
                "（框完當下就會用目前解析度重存樣板）；重存後不要再按「套用截圖預設框選」，"
                "那會把樣板還原成 1024 寬的內建預設圖。"
            )

    def emergency_stop(self) -> None:
        if self.running:
            self.running = False
            log("=== 緊急停止 (F10) ===")

    def _reset_round_state(self) -> None:
        self._seen_table_since_start = False
        self._logo_ever_matched = False
        self._missing_table_ticks = 0
        self._status_ticks = 0
        self._awaiting_draw_result = False
        self._draw_confirm_at = 0.0
        self._last_slot_signature = None
        self._pending_slot_signature = None
        self._pending_slot_count = 0
        self._last_highlow_label = None
        self._pending_highlow_label = None
        self._pending_highlow_count = 0
        self._highlow_chain = 0
        self._last_hl_win_prob = 0.5
        self._last_hl_choice = "high"
        self._acted_state = None
        self._acted_at = 0.0
        self._act_count = 0
        self._idle_since = None

    # ---------- 動作節奏 ----------

    STATE_NAMES = {
        "challenge": "翻倍對話框",
        "cashout": "翻倍對話框（兌現）",
        "congrats": "過關畫面",
        "fail": "失敗畫面",
        "max_win": "已達最高獲得金額",
        "draw": "選牌畫面",
        "highlow": "比大小畫面",
        "idle": "待機",
    }

    def _should_act(self, state: str) -> bool:
        """現在該不該（再）點一次？

        三種情況：
        1. 剛點過還沒過冷卻 → 不動。遊戲需要時間跑動畫，這段期間畫面認不出來
           是正常的，硬要動作就會亂點（例如比大小按完「大」之後又去點「投注並開始」）。
        2. 畫面換了 → 直接動作。
        3. 畫面沒換、而且已經超過重試秒數 → 遊戲多半漏掉了上一次點擊，再點一次。
        """
        now = time.time()
        elapsed = now - self._acted_at
        if elapsed < float(self.cfg.get("action_cooldown_sec", 1.2)):
            return False
        if self._acted_state != state:
            return True
        return elapsed >= float(self.cfg.get("action_retry_sec", 2.5))

    def _act(self, state: str, key: str) -> bool:
        """為某個畫面點擊某個按鈕，並記下時間，供冷卻與重試判斷。"""
        repeat = self._acted_state == state
        self._act_count = self._act_count + 1 if repeat else 1
        self._acted_state = state
        self._acted_at = time.time()
        if repeat:
            log(
                f"[重試] 畫面還停在「{self.STATE_NAMES.get(state, state)}」，"
                f"遊戲可能沒收到上一次點擊，再點一次（第 {self._act_count} 次）"
            )
        return self._click_point(key)

    # ---------- 主迴圈 ----------

    def run_forever(self) -> None:
        interval = self.cfg.get("capture_interval_sec", 0.4)
        log("Bot 已就緒，按 F9 開始/停止，按 F10 緊急停止，按 Ctrl+C 結束程式。")
        while not self._stop_flag.is_set():
            time.sleep(interval)
            if not self.running:
                continue
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                log(f"[錯誤] 主迴圈發生例外: {e!r}")

    def stop_program(self) -> None:
        self._stop_flag.set()

    def reload(self, config: dict, dry_run: bool | None = None) -> None:
        """啟動前重新載入設定、樣板與除錯開關。"""
        self.cfg = config
        if dry_run is not None:
            self.dry_run = dry_run
        self.capture.window_title_substring = config.get("window_title_substring", "")
        self.card_templates = load_card_templates()
        self.part_templates = load_part_templates()
        self.reader = self._build_reader()
        self.ui_templates = self._load_ui_templates(config)

    def _maybe_reselect_profile(self) -> None:
        """跑到一半改視窗大小也要跟著換校準組。

        只在「長寬比」變了才重挑 —— 等比例縮放本來就不影響比例座標，
        每次都重挑只會在 log 裡刷一堆沒意義的訊息。
        """
        try:
            cur_w, cur_h = self.capture.get_client_size()
        except Exception:
            return
        if cur_w <= 0 or cur_h <= 0:
            return
        aspect = profiles_mod.aspect_of(cur_w, cur_h)
        previous = getattr(self, "_last_aspect", None)
        if previous is not None and profiles_mod.relative_delta(aspect, previous) <= 0.01:
            return
        self._last_aspect = aspect
        if previous is None:
            return  # 啟動時已經由 _check_window_geometry 挑過了
        selection = profiles_mod.select_for_window(self.cfg, cur_w, cur_h)
        log(f"[視窗比例改變] {cur_w}x{cur_h}（{profiles_mod.label_for(cur_w, cur_h)}）"
            f"　{profiles_mod.summarize_selection(selection)}")

    def _tick(self) -> None:
        if not self.capture.is_window_valid():
            if not self.capture.locate():
                log("找不到遊戲視窗，等待中...")
                return

        self._maybe_reselect_profile()
        frame = detect_frame(self.capture, self.cfg, self.reader, self.ui_templates)
        self._status_ticks += 1

        if frame.on_table:
            self._logo_ever_matched = True
            self._missing_table_ticks = 0
        elif frame.any_dialog:
            # 對話框（過關 / 翻倍 / 失敗 / 湊牌失敗）會把整個牌桌連同左上角 logo
            # 一起模糊掉，logo 認不出來是正常現象，不能拿來當「已離開牌桌」的證據。
            self._missing_table_ticks = 0

        if self._status_ticks == 1:
            log(f"畫面標記樣板縮放倍率 = {frame.ui_scores.get('_scale', 1.0):.2f}x")

        if self._status_ticks == 1 or self._status_ticks % 8 == 0:
            scores = frame.ui_scores
            n_cards = sum(1 for s in frame.slot_cards if s is not None)
            hl = frame.highlow_card[0] if frame.highlow_card else "-"
            log(
                "偵測中 "
                f"牌桌={self._fmt_score(scores.get('table'))} "
                f"選牌={self._fmt_score(scores.get('draw'))} "
                f"過關={self._fmt_score(scores.get('congrats'))} "
                f"翻倍={self._fmt_score(scores.get('challenge'))} "
                f"失敗={self._fmt_score(scores.get('fail'))} "
                f"湊牌失敗={self._fmt_score(scores.get('poker_fail'))} "
                f"上限={self._fmt_score(scores.get('max_win'))} "
                f"手牌={n_cards}/5 比大小={hl}"
            )

        # 只要認得出目前是哪個畫面，就不算「待機」，把待機計時歸零
        if frame.any_dialog or frame.is_draw or frame.highlow_card is not None:
            self._idle_since = None

        # 「已達最高獲得金額，遊戲結束」要排在最前面：這個畫面同時會有結算面板，
        # 底下那幾個標記有機會擦邊命中，先攔下來才不會被當成一般的失敗/過關。
        if frame.is_max_win:
            self._awaiting_draw_result = False
            self._handle_max_win()
            return

        # 對話框會模糊/蓋住 logo，必須先處理
        if frame.is_poker_fail:
            self._awaiting_draw_result = False
            self._handle_fail(kind="poker")
            return
        if frame.is_fail:
            self._awaiting_draw_result = False
            self._handle_fail(kind="highlow")
            return
        if frame.is_challenge:
            self._handle_challenge()
            return
        if frame.is_congrats:
            self._awaiting_draw_result = False
            self._handle_congrats()
            return

        # 按下「替換」之後，遊戲要跑發牌動畫、結算、再切到比大小，中間可能好幾秒
        # 都認不出任何畫面。這段期間只是「等」，**不可以**因為等太久就自作主張當成
        # 湊牌失敗去點「再一次」——舊版就是這樣，畫面慢一點就把已經過關的一局打斷。
        #
        # 真正的湊牌失敗一定會出現 poker_fail_marker（上面已經先攔截並處理），
        # 所以這裡只負責安靜等待，等到看得懂畫面為止。
        if self._awaiting_draw_result:
            waited = time.time() - self._draw_confirm_at
            if frame.is_draw or frame.highlow_card is not None:
                # 畫面已經切過去了（回到選牌／進入比大小），結束等待
                self._awaiting_draw_result = False
            elif waited >= float(self.cfg.get("draw_result_wait_sec", 15.0)):
                log(
                    f"[選牌結果] 等了 {waited:.0f} 秒仍認不出畫面，回到一般偵測"
                    "（不會自己判定湊牌失敗）"
                )
                self._awaiting_draw_result = False
                self._idle_since = None
            else:
                self._missing_table_ticks = 0
                return

        # 牌桌 logo 只用來判斷「玩到一半被踢出去」，不能擋住開始點擊
        if (
            self._logo_ever_matched
            and not frame.on_table
            and self.ui_templates.get("table_marker") is not None
        ):
            self._missing_table_ticks += 1
            if self._missing_table_ticks >= int(self.cfg.get("exit_table_ticks", 25)):
                log(
                    f"偵測到已離開牌桌畫面（logo 相似度 {frame.table_marker_score:.0%}，"
                    "可能已達每日次數上限），自動停止事件。"
                )
                self.running = False
                self.stats.record_event("auto_stop_exit_table", {})
                return

        if frame.is_draw:
            recognized = [s for s in frame.slot_cards if s is not None]
            if len(recognized) == 5:
                self._handle_draw_phase(frame)
            elif self._status_ticks % 8 == 1:
                self._explain_missing_cards(frame)
            return
        if frame.highlow_card is not None:
            self._handle_highlow_phase(frame)
            return

        self._handle_idle(frame)

    # ---------- 各階段處理 ----------

    def _handle_draw_phase(self, frame) -> None:
        labels = tuple(s[0] for s in frame.slot_cards)

        if labels == self._last_slot_signature:
            # 同一手牌還在畫面上 = 遊戲沒收到「替換」。等超過重試秒數就補按一次。
            #
            # 只補按「替換」，不重點保留的牌：那五個是**切換**，如果剛才的保留其實
            # 有生效，再點一次會全部取消，變成五張全換掉，比不動作還糟。
            if self._should_act("draw"):
                self._act("draw", "draw_confirm")
                self._awaiting_draw_result = True
                self._draw_confirm_at = time.time()
            return

        if labels == self._pending_slot_signature:
            self._pending_slot_count += 1
        else:
            self._pending_slot_signature = labels
            self._pending_slot_count = 1
        if self._pending_slot_count < 2:
            return  # 需連續兩次讀到相同結果才視為畫面穩定，避免動畫過程誤判

        self._last_slot_signature = labels
        try:
            cards = [Card.from_label(l) for l in labels]
        except ValueError as e:
            log(f"[警告] 手牌標籤無法解析（{e}），請檢查 card_templates 是否混入非卡牌圖片")
            return
        for c in cards:
            if c.rank != "JK":
                self.stats.record_card(c.label)

        category, _ = classify_hand(cards)
        decision = decide_hold(cards, self.stats, samples=self.cfg.get("monte_carlo_samples", 3000))
        keep_idx = sorted(set(range(5)) - set(decision.discard_idx))
        log(
            f"[選牌階段] 手牌={labels} 目前牌型={hand_name(category)} | "
            f"點擊保留={keep_idx}（沒點的會被替換）| "
            f"換牌後門票(>=兩對)機率={decision.p_qualify:.1%} 預期牌型={decision.expected_hand}"
        )

        if self.dry_run:
            log("[dry-run] 未執行實際點擊")
            return

        # 這個遊戲是「點要留下的牌」再按替換，不是點要丟掉的牌。
        # 連點多下時遊戲容易漏收，每一下之間留一點間隔。
        gap = float(self.cfg.get("multi_click_gap_sec", 0.18))
        for idx in keep_idx:
            self.mouse.click_point(self.cfg["points"]["hold_toggles"][idx])
            time.sleep(gap)
        confirm = self.cfg["points"].get("draw_confirm")
        if confirm and (confirm["x"] or confirm["y"]):
            self.mouse.click_point(confirm)
        self.stats.bump("rounds_started")
        self._acted_state = "draw"
        self._acted_at = time.time()
        self._act_count = 1
        self._awaiting_draw_result = True
        self._draw_confirm_at = time.time()

    def _handle_highlow_phase(self, frame) -> None:
        label, score = frame.highlow_card

        if label == self._last_highlow_label:
            # 同一張牌還在畫面上 = 遊戲沒收到「大／小」，超過重試秒數就再按一次
            if self._should_act(f"highlow:{label}"):
                btn = "high_button" if self._last_hl_choice == "high" else "low_button"
                self._act(f"highlow:{label}", btn)
            return

        if label == self._pending_highlow_label:
            self._pending_highlow_count += 1
        else:
            self._pending_highlow_label = label
            self._pending_highlow_count = 1
        if self._pending_highlow_count < 2:
            return

        self._last_highlow_label = label
        try:
            card = Card.from_label(label)
        except ValueError as e:
            log(f"[警告] 比大小牌面標籤無法解析（{e}），請檢查 card_templates")
            return
        if card.rank != "JK":
            self.stats.record_card(card.label)
        self._highlow_chain += 1

        decision = decide_high_or_low(card.rank, self.stats, ace_high=self.cfg.get("ace_high", True))
        self._last_hl_win_prob = decision.win_prob
        self._last_hl_choice = decision.choice

        log(
            f"[比大小階段] 目前牌={label} 建議={decision.choice.upper()} 預估勝率={decision.win_prob:.1%} "
            f"(連續第 {self._highlow_chain} 次)。同點數會重抽，不計勝負。"
        )
        note = getattr(self.reader, "last_note", "")
        if note:
            log(f"　　※ {note}")

        if self.dry_run:
            log("[dry-run] 未執行實際點擊")
            return

        # 這個畫面只能選大或小，兌現是在之後的「要挑戰嗎？」對話框
        btn_key = "high_button" if decision.choice == "high" else "low_button"
        self._act(f"highlow:{label}", btn_key)
        self.stats.record_event(
            "highlow_guess",
            {"card": label, "choice": decision.choice, "win_prob": decision.win_prob},
        )

    def _explain_missing_highlow(self) -> None:
        """比大小的牌為什麼讀不到。

        這條路以前完全沒有任何診斷：比大小畫面沒有畫面標記，讀不到牌就一路掉到
        `_handle_idle`，log 只會叫使用者去調標記門檻 —— 而那個畫面沒有標記。
        使用者只看得到「比大小=—」，完全不知道是框沒對準、卡身找不到、
        還是點數分數差一點。
        """
        region = (self.cfg.get("regions") or {}).get("highlow_card") or {}
        if region.get("w", 0) <= 0:
            log("　　比大小：『比大小：目前已翻開的牌』還沒校準，這個畫面永遠認不出來。")
            return
        try:
            roi = self.capture.grab_region(region)
            height, width = roi.shape[:2]
            why = self.reader.explain_rightmost(roi, width, height)
        except Exception as e:  # noqa: BLE001
            why = f"讀取失敗 {e!r}"
        log(f"　　比大小認不到牌：{why}")

    def _explain_missing_cards(self, frame) -> None:
        """手牌沒認齊時，把每一格「差在哪」印出來，不要只說『請補樣板』。

        分數不夠 → 缺那個點數/花色的樣板；領先不足 → 兩個候選太像，多存幾張。
        """
        missing = [i for i, s in enumerate(frame.slot_cards) if s is None]
        log(f"已看到選牌畫面，但手牌只認出 {5 - len(missing)}/5，以下是認不出來的那幾格：")
        regions = self.cfg["regions"]["card_slots"]
        for i in missing:
            try:
                roi = self.capture.grab_region(regions[i])
                h, w = roi.shape[:2]
                why = self.reader.explain(roi, w, h)
            except Exception as e:  # noqa: BLE001
                why = f"讀取失敗 {e!r}"
            log(f"   第 {i + 1} 張：{why}")

    def _fmt_score(self, score) -> str:
        if score is None or score < 0:
            return "無"
        return f"{float(score):.0%}"

    def _click_point(self, key: str) -> bool:
        point = self.cfg["points"].get(key)
        if not point or not (point.get("x") or point.get("y")):
            log(f"[警告] 尚未校準「{key}」，跳過點擊")
            return False
        if self.dry_run:
            log(f"[dry-run] 將點擊 {key}")
            return True
        self.mouse.click_point(point)
        return True

    def _handle_congrats(self) -> None:
        if not self._should_act("congrats"):
            return
        log("[過關畫面] 點擊繼續，進入翻倍對話框")
        self._act("congrats", "click_continue")
        self._awaiting_draw_result = False
        self._last_highlow_label = None
        self._pending_highlow_label = None
        self._pending_highlow_count = 0

    def _handle_challenge(self) -> None:
        # 剛過關第一次翻倍一定要進；之後才依勝率決定要不要兌現
        first_entry = self._highlow_chain == 0
        keep_going = first_entry or should_continue_highlow(
            self._last_hl_win_prob, self._highlow_chain, self.cfg
        )
        # 挑戰與兌現算成兩個不同的畫面狀態，這樣中途改變主意時會馬上重按，
        # 而不是被「同一個對話框已經處理過」擋掉
        state = "challenge" if keep_going else "cashout"
        if not self._should_act(state):
            return
        if keep_going:
            log(f"[翻倍對話] 進行挑戰（目前連勝 {self._highlow_chain}）")
            self._act(state, "challenge_button")
        else:
            log(f"[翻倍對話] 取消兌現（預估下一手勝率 {self._last_hl_win_prob:.1%}）")
            self._act(state, "cashout_button")
            self._highlow_chain = 0
        self._last_highlow_label = None

    def _handle_fail(self, kind: str = "fail") -> None:
        if not self._should_act("fail"):
            return
        if kind == "poker":
            log("[湊牌失敗] 點擊再一次")
        else:
            log("[失敗畫面] 再一次，開始新的一局")
        self._act("fail", "retry_button")
        self._awaiting_draw_result = False
        self._highlow_chain = 0
        self._last_slot_signature = None
        self._last_highlow_label = None

    def _handle_max_win(self) -> None:
        """「已達最高獲得金額，遊戲結束」——今天的兩次額度用掉一次。

        第 1 次：畫面停在結算頁，按「再玩一次」還能繼續玩第二輪。
        第 2 次：遊戲會直接把牌桌關掉，沒得再玩了，這時就收工。

        次數存在當天的統計檔（max_win_count），所以中途重開程式也不會重數。
        """
        if self._acted_state != "max_win":
            # 第一次看到這個畫面才計數；同一個畫面的重試不重複累加
            self.stats.bump("max_win_count")
            self.stats.record_event("max_win", {"count": self._max_win_count()})

        count = self._max_win_count()
        limit = int(self.cfg.get("daily_max_wins", 2))

        if count >= limit:
            log(
                f"[達到上限] 今天第 {count} 次達到最高獲得金額（每日上限 {limit} 次），"
                "遊戲即將關閉牌桌，自動停止。"
            )
            self.running = False
            self.stats.record_event("auto_stop_daily_limit", {"count": count})
            return

        if not self._should_act("max_win"):
            return
        log(f"[達到上限] 今天第 {count}/{limit} 次，點擊「再玩一次」繼續下一輪")
        self._act("max_win", "max_win_retry")
        self._highlow_chain = 0
        self._last_slot_signature = None
        self._last_highlow_label = None

    def _max_win_count(self) -> int:
        try:
            return int(self.stats.data.get("max_win_count", 0))
        except (TypeError, ValueError):
            return 0

    def _handle_idle(self, frame) -> None:
        """畫面認不出來時才會走到這裡。

        剛點完按鈕、遊戲在跑動畫的那一兩秒也是「認不出來」，這時候絕對不能急著
        去點「投注並開始」—— 舊版就是這樣在比大小按完「大」之後多點了一下。
        所以要求「連續認不出來超過 idle_confirm_sec」才動作。
        """
        now = time.time()
        if self._idle_since is None:
            self._idle_since = now
            return
        if now - self._idle_since < float(self.cfg.get("idle_confirm_sec", 1.5)):
            return
        if not self._should_act("idle"):
            return

        # 「認不出畫面」不等於「在等你下注」。
        #
        # 2026-08-21 的實機 log：比大小畫面上牌認不出來（比大小=-），所有標記都沒過，
        # 於是一路掉到這裡，每 2.5 秒點一次「投注並開始」，第 4、5、6 次…… 永遠不會結束。
        # 那顆按鈕在比大小畫面上什麼都不會發生，但也就永遠不會有進展。
        #
        # 所以改成**正面確認**：投注畫面的特徵是五個牌位都蓋著深色牌背。
        # 這個判斷不需要任何樣板，只靠亮度，所以樣板缺失或認不到牌時依然可靠。
        if not frame.looks_like_betting(
            float(self.cfg.get("idle_slot_max_value", IDLE_SLOT_MAX_VALUE))
        ):
            if self._status_ticks % 12 == 1:
                shown = frame.slot_values or "（沒量到）"
                log(f"[待機] 畫面認不出來，但五個牌位的亮度 {shown} 不像投注畫面"
                    f"（要全部 ≤ {self.cfg.get('idle_slot_max_value', IDLE_SLOT_MAX_VALUE)}），"
                    "所以不點「投注並開始」。")
                # **比大小畫面沒有任何畫面標記**，它是靠「讀到那張牌」來判斷的。
                # 所以牌讀不到時，叫使用者「看哪個標記分數最接近門檻」是錯的方向
                # —— 那個畫面根本沒有標記可以調。直接把牌讀不到的原因印出來。
                self._explain_missing_highlow()
                log("　　若上面說的是標記分數問題，才到「設定」分頁調低對應的門檻。")
            return

        self._last_slot_signature = None
        self._pending_slot_signature = None
        self._pending_slot_count = 0
        self._last_highlow_label = None
        self._pending_highlow_label = None
        self._pending_highlow_count = 0
        log("[待機] 點擊投注並開始")
        self._act("idle", "start_round")


def build_and_run(config: dict, dry_run: bool = False) -> None:
    bot = Bot(config, dry_run=dry_run)
    hotkeys = HotkeyManager(on_toggle=bot.toggle, on_emergency_stop=bot.emergency_stop)
    hotkeys.start()
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        log("使用者中斷，程式結束。")
    finally:
        hotkeys.stop()
