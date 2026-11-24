from live_translator.asr.filters import clean_text, is_hallucination
from live_translator.subtitles.formats import Cue, merge_short, parse_json3, to_srt
from live_translator.subtitles.youtube import video_id_of
from live_translator.translate.base import Translator, parse_batch
from live_translator.translate.postprocess import clean_output, postprocess


def test_clean_output_strips_think_and_prefix():
    assert clean_output("<think>\n</think>\n\n譯文：大家晚安！") == "大家晚安！"
    assert clean_output("「好可怕」") == "好可怕"


def test_postprocess_converts_to_taiwan_terms():
    out = postprocess("这个软件的视频质量很好", "s2twp", {})
    assert "軟體" in out and "影片" in out


def test_glossary_applied_after_opencc():
    assert postprocess("谢谢スパチャ", "s2twp", {"スパチャ": "SC"}) == "謝謝SC"


def test_parse_batch():
    raw = "1|大家好\n2|今天要玩恐怖遊戲\n垃圾行\n5|超出範圍"
    assert parse_batch(raw, 3) == {1: "大家好", 2: "今天要玩恐怖遊戲"}


class FakeTranslator(Translator):
    def __init__(self, reply):
        super().__init__({"opencc": "s2twp"}, {})
        self.reply = reply

    def chat(self, system, user):
        return self.reply


def test_translate_batch_falls_back_per_line():
    tr = FakeTranslator("1|第一句")
    # 第 2 行解析不到 → 退回逐句翻譯（這裡 fake 一律回 "1|第一句"，clean 後保留原樣）
    out = tr.translate_batch(["一", "二"])
    assert out[0] == "第一句" and len(out) == 2


def test_hallucination_filter():
    assert is_hallucination("ご視聴ありがとうございました")
    assert is_hallucination("...")
    assert not is_hallucination("今日はホラーゲームをやります")
    assert clean_text("あああああああああ") == "あああ"
    assert clean_text("I have 1000000 dollars") == "I have 1000000 dollars"
    assert is_hallucination("Thanks for watching!")


def test_looks_incomplete():
    from live_translator.asr.filters import looks_incomplete

    assert looks_incomplete("今日はホラーゲームをやろうと思ってて", "ja")
    assert looks_incomplete("めっちゃ怖いんだけど", "ja")
    assert looks_incomplete("配信始めるから", "ja")
    assert not looks_incomplete("配信始めるから、", "ja")  # 「、」結尾不再等待：實測延遲較大且錯誤率較高
    assert not looks_incomplete("ありがとうございます！", "ja")
    assert not looks_incomplete("今日は晴れです。", "ja")
    assert looks_incomplete("So today we're going to play and", "en")
    assert looks_incomplete("I think that,", "en")
    assert not looks_incomplete("That was insane!", "en")


def test_sakura_prompt_and_batch_fallback():
    from live_translator.translate.sakura import SakuraTranslator

    class Fake(SakuraTranslator):
        def __init__(self, reply):
            super().__init__({"model": "sakura", "opencc": "s2twp"}, {"スパチャ": "SC"})
            self.reply = reply
            self.prompts = []

        def chat(self, system, user, options=None):
            self.prompts.append(user)
            return self.reply

    tr = Fake("谢谢大家的软件")
    assert tr.translate("みんなスパチャありがとう", [("前", "前文譯文")]) == "謝謝大家的軟體"
    assert "历史翻译：前文译文" in tr.prompts[0] and "スパチャ->SC" in tr.prompts[0]
    # 行數不符 → 逐句翻
    assert tr.translate_batch(["一", "二"]) == ["謝謝大家的軟體", "謝謝大家的軟體"]


def test_only_relevant_glossary_in_prompt():
    from live_translator.translate.sakura import SakuraTranslator

    class Fake(SakuraTranslator):
        def __init__(self):
            super().__init__({"model": "sakura", "opencc": "s2twp"}, {"スパチャ": "SC", "草": "笑死"})
            self.prompts = []

        def chat(self, system, user, options=None):
            self.prompts.append(user)
            return "好"

    tr = Fake()
    tr.translate("たくさんありがとう")
    assert "草->" not in tr.prompts[-1] and "スパチャ->" not in tr.prompts[-1]
    tr.translate("スパチャありがとう")
    assert "スパチャ->SC" in tr.prompts[-1] and "草->" not in tr.prompts[-1]


def test_leaked_prompt_prefix_removed():
    assert clean_output("目前這句： 現在第一張專輯") == "現在第一張專輯"


def test_routing_by_language():
    from live_translator.translate.base import build_translator
    from live_translator.translate.sakura import SakuraTranslator

    tr = build_translator({"provider": "ollama", "model": "qwen3:8b",
                           "models": {"ja": "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3.7:IQ4_XS"}}, {})
    assert isinstance(tr.pick("ja"), SakuraTranslator)
    assert tr.pick("en").model == "qwen3:8b"


def test_prompts_follow_language():
    from live_translator.translate.base import batch_system_prompt, system_prompt

    assert "英文翻中" in system_prompt("en") and "um" in system_prompt("en")
    assert "日文翻中" in system_prompt("ja")
    assert "英文字幕行" in batch_system_prompt("en")


def test_video_id():
    assert video_id_of("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1") == "dQw4w9WgXcQ"
    assert video_id_of("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id_of("https://www.youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_json3_and_srt():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 3000, "segs": [{"utf8": "こんにちは"}]},
        {"tStartMs": 2000, "dDurationMs": 2000, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2500, "dDurationMs": 2000, "segs": [{"utf8": "今日は"}, {"utf8": "晴れ"}]},
    ]}
    cues = parse_json3(data)
    assert [c.source for c in cues] == ["こんにちは", "今日は晴れ"]
    assert cues[0].end == 2.5
    cues[0].target = "你好"
    srt = to_srt(cues)
    assert "00:00:00,000 --> 00:00:02,500\n你好\nこんにちは" in srt


def test_merge_short():
    cues = [Cue(0, 0.5, "あ"), Cue(0.5, 1.0, "い"), Cue(3, 5, "う")]
    out = merge_short(cues)
    assert [c.source for c in out] == ["あい", "う"]


def test_quietest_point_cuts_at_pause():
    import numpy as np

    from live_translator.asr.streamer import quietest_point

    sr = 16000
    loud = np.random.default_rng(0).normal(0, 0.3, sr * 12).astype(np.float32)
    loud[int(sr * 8.0):int(sr * 8.5)] = 0.0   # 8 秒處有半秒換氣
    cut = quietest_point(loud)
    assert int(sr * 8.0) <= cut <= int(sr * 8.5)


def test_mixed_english_routes_to_general_model():
    from live_translator.translate.base import build_translator
    from live_translator.translate.router import has_english, text_lang
    from live_translator.translate.sakura import SakuraTranslator

    assert has_english("ちょっと待って、what is this?")
    assert has_english("これ、so cuteじゃない？")
    assert not has_english("身長152cmのCDとMVとVIP席")
    assert not has_english("今日はありがとう")
    assert text_lang("I'm gonna have it now!", "ja") == "en"
    assert text_lang("今日はね、really happy", "ja") == "ja"

    tr = build_translator({"provider": "ollama", "model": "qwen3:8b",
                           "models": {"ja": "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3.7:IQ4_XS"}}, {})
    assert isinstance(tr.pick("ja", "今日はありがとう"), SakuraTranslator)
    assert tr.pick("ja", "ちょっと待って、what is this?").model == "qwen3:8b"


def test_mid_sentence_fillers_and_tw_fixes():
    from live_translator.asr.filters import is_hallucination, looks_incomplete
    from live_translator.translate.postprocess import postprocess

    assert looks_incomplete("という人はね今回のね", "ja")
    assert looks_incomplete("それはさ", "ja")
    assert not looks_incomplete("よろしくお願いしまーす", "ja")
    assert is_hallucination("次回予告")
    assert postprocess("爆音发布会", "s2twp", {}) == "爆音發表會"


def test_prompt_uses_recent_context():
    import queue

    from live_translator.asr.streamer import StreamingRecognizer

    class Eng:
        current_lang = "ja"

    r = StreamingRecognizer.__new__(StreamingRecognizer)
    from collections import deque
    r._recent = deque(["前回ファンミで発売させていただいたハッピの", "海外に来てね", "って声がたくさんあったんですけど"], maxlen=8)
    r.prompt_chars = 150
    r.prompt_seed = ""
    p = r._prompt()
    assert p.startswith("前回ファンミ") and p.endswith("あったんですけど")
    r.prompt_chars = 20
    assert r._prompt() == "海外に来てねって声がたくさんあったんですけど"[-len(r._prompt()):]


def test_streaming_loop_emits_finals():
    """辨識主迴圈跑得起來：兩段「說話」中間有停頓 → 產生兩句定稿。"""
    import queue
    import time

    import numpy as np

    from live_translator.asr.streamer import StreamingRecognizer

    class FakeEngine:
        current_lang = "ja"

        def detect(self, audio):
            return "ja"

        def transcribe(self, audio, final, prompt=None, lang=None):
            return "テストです。"

    class FakeVad:
        threshold = 0.5

        def feed(self, chunk):
            return 1.0 if float(np.sqrt(np.mean(chunk ** 2))) > 0.05 else 0.0

        def reset(self):
            pass

    q: "queue.Queue[np.ndarray]" = queue.Queue()
    events = []
    r = StreamingRecognizer({}, q, events.append, FakeEngine())
    r.vad = FakeVad()
    sr = 16000
    loud = (np.random.default_rng(1).normal(0, 0.3, sr // 10)).astype(np.float32)
    quiet = np.zeros(sr // 10, np.float32)
    for block in [loud] * 15 + [quiet] * 15 + [loud] * 15 + [quiet] * 15:
        q.put(block)
    r.start()
    deadline = time.time() + 10
    while time.time() < deadline and sum(e.state == "final" for e in events) < 2:
        time.sleep(0.05)
    r.stop()
    finals = [e for e in events if e.state == "final"]
    assert len(finals) == 2 and finals[0].start < finals[1].start


def test_corrections_applied_longest_first(tmp_path):
    from live_translator.config import apply_corrections, load_corrections

    p = tmp_path / "g.yaml"
    p.write_text("terms: {}\ncorrections:\n  イン タイ: イン台北\n  ライブ イン タイ: ライブイン台北\n", encoding="utf-8")
    c = load_corrections(p)
    assert c[0][0] == "ライブ イン タイ"
    assert apply_corrections("ウタノライブ イン タイ", c) == "ウタノライブイン台北"


def test_srt_logger_drops_retracted(tmp_path):
    from live_translator.events import SubtitleEvent as E
    from live_translator.srt_log import SrtLogger

    log = SrtLogger(tmp_path)
    log.on_event(E(1, "translated", "イン タイ", "泰國", 1, 2))
    log.on_event(E(1, "retract", "イン タイ"))
    log.on_event(E(2, "translated", "イン台北遊びに", "歡迎來台北", 1, 4))
    log.flush()
    text = log.path.read_text(encoding="utf-8")
    assert "泰國" not in text and "歡迎來台北" in text


def test_merge_short_lines_when_enabled():
    import queue
    import time

    import numpy as np

    from live_translator.asr.streamer import StreamingRecognizer

    class Eng:
        current_lang = "ja"
        calls = []

        def detect(self, a):
            return "ja"

        def transcribe(self, audio, final, prompt=None, lang=None):
            if final:
                self.calls.append(audio.size)
            return f"句{audio.size // 1600}"

    class Vad:
        threshold = 0.5

        def feed(self, c):
            return 1.0 if float(np.abs(c).mean()) > 0.05 else 0.0

        def reset(self):
            pass

    q: "queue.Queue[np.ndarray]" = queue.Queue()
    ev = []
    r = StreamingRecognizer({"merge_gap_s": 0.8, "merge_short_s": 3.5, "min_silence_ms": 300,
                             "incomplete_hold_ms": 0}, q, ev.append, Eng())
    r.vad = Vad()
    loud, quiet = np.full(1600, 0.3, np.float32), np.zeros(1600, np.float32)
    for b in [loud] * 10 + [quiet] * 4 + [loud] * 10 + [quiet] * 20:
        q.put(b)
    r.start()
    t = time.time() + 10
    while time.time() < t and not any(e.state == "retract" for e in ev):
        time.sleep(0.05)
    time.sleep(0.3)
    r.stop()
    states = [e.state for e in ev if e.state in ("final", "retract")]
    assert states == ["final", "retract", "final"]


def test_zhi_conversion_fix():
    from live_translator.translate.postprocess import fix_tw_terms

    assert fix_tw_terms("感覺就像是隻有神才知道") == "感覺就像是只有神才知道"
    assert fix_tw_terms("我隻是說說") == "我只是說說"
    assert fix_tw_terms("一隻有翅膀的鳥") == "一隻有翅膀的鳥"
    assert fix_tw_terms("那隻貓") == "那隻貓"


def test_segmenter_splits_and_carries_fragment():
    import time

    from live_translator.events import SubtitleEvent as E
    from live_translator.segmenter import Segmenter

    out = []
    seg = Segmenter(out.append, hold_s=0.3)
    seg.feed(E(1, "final", "それはそう、誰にも分からなくて。今", start=0, end=4, lang="ja"))
    finals = [e for e in out if e.state == "final"]
    assert [e.source for e in finals] == ["それはそう、誰にも分からなくて。"]
    held = [e for e in out if e.state == "partial"]
    assert held and held[-1].source == "今"
    # 下一句來了 → 碎片接在前面
    seg.feed(E(2, "final", "神のみぞ知るみたいな感じなんですよね。", start=4.2, end=7, lang="ja"))
    finals = [e for e in out if e.state == "final"]
    assert finals[-1].source == "今神のみぞ知るみたいな感じなんですよね。"
    assert finals[-1].id == 200 and finals[0].id == 100
    # 碎片等太久 → 自己成一句
    out.clear()
    seg.feed(E(3, "final", "わかった。じゃあ", start=8, end=9, lang="ja"))
    time.sleep(0.5)
    assert [e.source for e in out if e.state == "final"] == ["わかった。", "じゃあ"]


def test_segmenter_multi_sentence_timing():
    from live_translator.events import SubtitleEvent as E
    from live_translator.segmenter import Segmenter

    out = []
    Segmenter(out.append).feed(E(5, "final", "ありがとう。よろしくお願いします！", start=10, end=13, lang="ja"))
    f = [e for e in out if e.state == "final"]
    assert len(f) == 2 and f[0].start == 10 and abs(f[1].end - 13) < 1e-6 and f[0].end == f[1].start


def test_opencc_traditional_input_protected():
    from live_translator.translate.postprocess import postprocess

    assert postprocess("請不要干擾別人", "s2twp", {}) == "請不要干擾別人"
    assert postprocess("政府干预市场", "s2twp", {}) == "政府干預市場"
    assert postprocess("台北公演", "s2twp", {}) == "台北公演"
    assert postprocess("这是台湾的软件", "s2twp", {}) == "這是台灣的軟體"


def test_fillers_removed_but_meaning_kept():
    from live_translator.translate.fillers import strip_ja_fillers, strip_zh_fillers

    assert strip_ja_fillers("で、えっと、日本に来る皆さんは、") == "で、日本に来る皆さんは、"
    assert strip_ja_fillers("あの人が来た") == "あの人が来た"
    assert strip_ja_fillers("その子に特別感を") == "その子に特別感を"
    assert strip_ja_fillers("で、えーと") == ""
    assert strip_ja_fillers("んー") == ""
    assert strip_zh_fillers("呃——周邊販售就是這種感覺，呃——事後郵購也安排好了，") == "周邊販售就是這種感覺，事後郵購也安排好了"
    assert strip_zh_fillers("好難啊。") == "好難啊。"
    assert strip_zh_fillers("那個人很好") == "那個人很好"
    assert strip_zh_fillers("我說——你們聽好") == "我說，你們聽好"
    assert strip_zh_fillers("嗯——") == ""


def test_ellipsis_means_unfinished():
    from live_translator.asr.filters import looks_incomplete

    assert looks_incomplete("爆音上映会アルバ…", "ja")
    assert looks_incomplete("and then we...", "en")


def test_worker_drops_filler_only_lines():
    from live_translator.events import SubtitleEvent as E
    from live_translator.translate.worker import TranslationWorker

    class T:
        def translate(self, text, ctx=(), lang="ja"):
            return "譯:" + text

    out = []
    w = TranslationWorker(T(), out.append)
    res = w._translate([E(1, "final", "えーと、", lang="ja")])
    assert [e.state for e in res] == ["retract"]
    res = w._translate([E(2, "final", "えー、物販はこんな感じ", lang="ja")])
    assert res[0].state == "translated" and res[0].target == "譯:物販はこんな感じ"


def test_refine_compare_finds_misheard_words(tmp_path):
    from live_translator.refine import Line, compare, read_live_srt

    live = [Line(0, 3, "あれは無等はないですが。"), Line(3, 6, "よろしくお願いします。")]
    refined = [Line(0, 2.5, "アルバム等はないですが"), Line(2.5, 6, "よろしくお願いします")]
    d = compare(live, refined)
    assert len(d) == 1 and "無等" in d[0][1] and "アルバム等" in d[0][2]
    # 兩邊斷句不同但內容相同 → 不算差異
    assert compare([Line(0, 6, "ありがとう。よろしく")], [Line(0, 3, "ありがとう"), Line(3, 6, "よろしく")]) == []
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:03,000\n謝謝\nありがとう\n\n", encoding="utf-8")
    ln = read_live_srt(srt)[0]
    assert ln.start == 1.5 and ln.target == "謝謝" and ln.source == "ありがとう"


def test_punctuation_only_translation_dropped():
    from live_translator.events import SubtitleEvent as E
    from live_translator.translate.fillers import strip_ja_fillers
    from live_translator.translate.worker import TranslationWorker

    assert strip_ja_fillers("んんっ!") == ""

    class T:
        def translate(self, text, ctx=(), lang="ja"):
            return "！？"

    res = TranslationWorker(T(), lambda e: None)._translate([E(1, "final", "ふぅ", lang="ja")])
    assert [e.state for e in res] == ["retract"]


def test_worker_reuses_translation_for_same_source():
    """同一句原文（往回拉時間軸又聽到）沿用第一次的譯文；前文不放同一句（I15 吐出提示詞的成因）。"""
    from live_translator.events import SubtitleEvent as E
    from live_translator.translate.worker import TranslationWorker

    seen = []

    class T:
        n = 0

        def translate(self, text, ctx=(), lang="ja"):
            seen.append((text, [s for s, _ in ctx]))
            T.n += 1
            return f"譯文{T.n}"

    w = TranslationWorker(T(), lambda e: None, memory_size=3000)
    a = w._translate([E(1, "final", "いっぱい聴いてくれてありがとう!", lang="ja")])[0].target
    w._translate([E(2, "final", "アルバムの方の", lang="ja")])
    b = w._translate([E(3, "final", "いっぱい聴いてくれて ありがとう！", lang="ja")])[0].target
    assert a == b == "譯文1" and len(seen) == 2
    # 關掉翻譯記憶時會重翻，但前文不能有同一句原文
    w2 = TranslationWorker(T(), lambda e: None, memory_size=0)
    w2._translate([E(1, "final", "同じ文です", lang="ja")])
    w2._translate([E(2, "final", "同じ文です", lang="ja")])
    assert seen[-1] == ("同じ文です", [])


def test_worker_repairs_leaked_or_kana_output():
    """譯文吐出提示詞或殘留假名 → 不帶前文重翻；壞掉的譯文不放進前文。"""
    from live_translator.events import SubtitleEvent as E
    from live_translator.translate.quality import problems
    from live_translator.translate.worker import TranslationWorker

    assert problems("謝謝！ 參考以下術語表（可為空，格式為src->dst #備註）：", "x") == ["leak"]
    assert problems("接下來也請多多關照。ジューツー老師", "じゅーじゅーさん") == ["kana"]
    assert problems("masu～。在亞馬遜買得到", "ますー。アマゾンで") == ["romaji"]
    assert problems("拿到VIP門票的人", "ビップ席") == []
    assert problems("好辣", "spicy") == []
    assert problems("ウタノ要來啦！", "ウタノが来る", allowed=["ウタノ"]) == []   # 詞彙表刻意保留的假名

    class T:
        def translate(self, text, ctx=(), lang="ja"):
            if ctx:
                return "專輯這邊的 參考以下術語表：根據以上術語表的對應關係，翻譯成簡體中文："
            return "謝謝大家聽我唱了這麼多歌！"

    w = TranslationWorker(T(), lambda e: None)
    w.context.append(("前の文", "前一句"))
    r = w._translate([E(1, "final", "いっぱい聴いてくれてありがとう!", lang="ja")])[0]
    assert r.target == "謝謝大家聽我唱了這麼多歌！"

    class K:
        def translate(self, text, ctx=(), lang="ja"):
            return "ジューツー老師"

        def translate_other(self, text, ctx=(), lang="ja"):
            return "啾啾老師" if text == "好" else "フフ"

    w = TranslationWorker(K(), lambda e: None)
    assert w._translate([E(1, "final", "好", lang="ja")])[0].target == "啾啾老師"
    r = w._translate([E(2, "final", "じゅーじゅーさん", lang="ja")])[0]
    assert r.state == "translated"            # 都重翻失敗：還是顯示，但不放進前文與記憶
    assert [s for s, _ in w.context] == ["好"] and "じゅーじゅーさん" not in w.memory


def test_replay_reuses_subtitles_after_seek_back():
    """往回拉時間軸重看：已翻過的段落不重翻，播到時送出當時的字幕（I15）。"""
    from live_translator.events import SubtitleEvent as E
    from live_translator.replay import MediaReplay

    r = MediaReplay()
    # 第一次看：stream 0 秒 = 影片 100 秒
    for s in range(0, 30):
        r.anchor(s, "vid", 100 + s)
    assert r.allow(E(1, "final", "いっぱい聴いてくれてありがとう!", start=10, end=13))
    r.on_event(E(1, "translated", "いっぱい聴いてくれてありがとう!", "謝謝大家這麼捧場！", 10, 13, "ja"))
    assert r.due(11) == []                      # 第一輪已經顯示過，不重送
    # stream 30 秒時拉回影片 105 秒
    r.anchor(30, "vid", 105, seek=True)
    for s in range(31, 50):
        r.anchor(s, "vid", 105 + s - 30)
    assert r.due(33) == []                       # 影片 108 秒，還沒播到 110
    cues = r.due(35.2)                           # 影片 110.2 秒
    assert [c.target for c in cues] == ["謝謝大家這麼捧場！"]
    assert r.due(36) == []                       # 同一輪只送一次
    # 重新辨識出來、落在已翻過範圍的句子不再翻；辨識中的字也不顯示
    assert not r.allow(E(2, "final", "いっぱい聴いてくれて", start=35.1, end=37.5))
    assert not r.allow(E(3, "partial", "いっぱい", start=35.1, end=36))
    # 講完後等停頓時送的辨識中文字（時間已超過快取那句的結尾）也不顯示
    assert not r.allow(E(3, "partial", "いっぱい聴いてくれてありがとう", start=35.1, end=39.0))
    # 沒翻過的範圍照常
    assert r.allow(E(4, "final", "新しい話", start=40, end=43))
    # 句子中間拉過時間軸：時間不可靠，不存
    r.anchor(50, "vid", 300, seek=True)
    r.on_event(E(5, "translated", "x", "跨過拉動", 49, 52, "ja"))
    assert all(c.target != "跨過拉動" for c in r._cues["vid"])
    # 被收回的句子移除；廣告中不推算影片時間
    r.on_event(E(1, "retract", ""))
    assert r._cues["vid"] == []
    r.anchor(60, "vid", 5, ad=True)
    assert r.media_at(61) is None


def test_recognizer_tick_hook_and_alloc_id():
    import queue

    import numpy as np

    from live_translator.asr.streamer import StreamingRecognizer

    class FakeEngine:
        current_lang = "ja"

        def detect(self, audio):
            return "ja"

        def transcribe(self, audio, final, prompt=None, lang=None):
            return ""

    q: "queue.Queue[np.ndarray]" = queue.Queue()
    r = StreamingRecognizer({}, q, lambda e: None, FakeEngine())
    ticks = []
    r.on_tick = lambda t: ticks.append((t, r.alloc_id()))
    r.start()
    for _ in range(3):
        q.put(np.zeros(1600, np.float32))
    import time

    for _ in range(50):
        if len(ticks) >= 3:
            break
        time.sleep(0.05)
    r.stop()
    assert [round(t, 1) for t, _ in ticks[:3]] == [0.1, 0.2, 0.3]
    assert [i for _, i in ticks[:3]] == [1, 2, 3]


def test_hymt_prompt_follows_official_format():
    """Hy-MT2：依官方模型卡的提示格式；名稱含 hy-mt 自動選用。"""
    from live_translator.translate import build_translator
    from live_translator.translate.hymt import HyMTTranslator

    t = build_translator({"provider": "ollama", "model": "hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M",
                          "opencc": "s2twp"}, {"アルバム": "專輯", "たくさん": "笑死"})
    assert isinstance(t, HyMTTranslator)
    p = t._prompt("アルバムの方も", [])
    assert p.startswith("参考下面的翻译：\nアルバム 翻译成 专辑")
    assert "たくさん" not in p
    assert p.endswith("将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\nアルバムの方も")
    p = t._prompt("ありがとう", [("前の文", "前一句")])
    assert "【背景信息】\n前の文" in p and p.endswith("【待翻译文本】\nありがとう")


def test_hymt_small_uses_raw_prompt():
    """Hy-MT2-1.8B 在 Ollama 的對話範本是壞的：改用 raw 模式自己組官方格式（E66）。"""
    from live_translator.translate import build_translator

    sent = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class Client:
        def post(self, url, json=None, **kw):
            sent.append((url, json))
            return Resp({"response": "就在那一刻。"} if url.endswith("/api/generate")
                        else {"message": {"content": "就在那一刻。"}})

    small = build_translator({"provider": "ollama", "model": "hf.co/tencent/Hy-MT2-1.8B-GGUF:Q8_0",
                              "opencc": "s2twp"}, {})
    small.client = Client()
    assert small.translate("And that was the moment", lang="en") == "就在那一刻。"
    url, body = sent[-1]
    assert url.endswith("/api/generate") and body["raw"] is True
    assert body["prompt"].startswith("<｜hy_begin▁of▁sentence｜><｜hy_User｜>")
    assert body["prompt"].endswith("And that was the moment<｜hy_Assistant｜>")
    assert "<｜hy_EOT｜>" in body["options"]["stop"]

    big = build_translator({"provider": "ollama", "model": "hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M",
                            "opencc": "s2twp"}, {})
    big.client = Client()
    big.translate("And that was the moment", lang="en")
    assert sent[-1][0].endswith("/api/chat")


def _run_recognizer(cfg, engine, blocks, want_finals=1, vad=None):
    import queue
    import time

    import numpy as np

    from live_translator.asr.streamer import StreamingRecognizer

    class FakeVad:
        threshold = 0.5

        def feed(self, chunk):
            return 1.0 if float(np.sqrt(np.mean(chunk ** 2))) > 0.05 else 0.0

        def reset(self):
            pass

    q: "queue.Queue[np.ndarray]" = queue.Queue()
    events = []
    r = StreamingRecognizer(cfg, q, events.append, engine)
    r.vad = vad or FakeVad()
    for b in blocks:
        q.put(b)
    r.start()
    deadline = time.time() + 10
    while time.time() < deadline and sum(e.state == "final" for e in events) < want_finals:
        time.sleep(0.05)
    r.stop()
    return [e for e in events if e.state == "final"]


def test_sparse_final_retried_without_prompt():
    """前文提示讓 Whisper 只寫出句尾時（I16），不給前文重聽、取比較完整的結果。"""
    import numpy as np

    class Eng:
        current_lang = "ja"
        calls = []

        def detect(self, audio):
            return "ja"

        def transcribe(self, audio, final, prompt=None, lang=None):
            if final:
                Eng.calls.append(prompt)
            return "最後だけ。" if prompt else "最初から最後まで全部ちゃんと聞き取れた長い文です。"

    sr = 16000
    loud = (np.random.default_rng(2).normal(0, 0.3, sr // 10)).astype(np.float32)
    quiet = np.zeros(sr // 10, np.float32)
    blocks = [loud] * 60 + [quiet] * 15
    cfg = {"prompt_seed": "例文。", "retry_sparse": True, "sparse_min_s": 5.0, "max_utterance_s": 30, "soft_max_s": 30,
           "sentence_cut": False}
    finals = _run_recognizer(cfg, Eng(), blocks)
    assert finals and finals[0].source.startswith("最初から")
    assert Eng.calls[:2] == ["例文。", None]
    Eng.calls.clear()
    finals = _run_recognizer({**cfg, "retry_sparse": False}, Eng(), blocks)
    assert finals[0].source == "最後だけ。" and Eng.calls == ["例文。"]


def test_end_threshold_splits_on_soft_pause():
    """連續偵測：句間停頓時「開始說話」機率仍偏高，另用 end_threshold／keep 判斷說完（E63）。"""
    import numpy as np

    class Eng:
        current_lang = "ja"

        def detect(self, audio):
            return "ja"

        def transcribe(self, audio, final, prompt=None, lang=None):
            return "テストです。"

    class SoftVad:
        """說話時機率 1；停頓時最高機率 0.6（超過 0.5），但最後一個視窗只有 0.3。"""
        threshold = 0.5

        def __init__(self, end_threshold):
            self.end_threshold = end_threshold
            self.keep = 0.0

        def feed(self, chunk):
            loud = float(np.sqrt(np.mean(chunk ** 2))) > 0.05
            self.keep = 1.0 if loud else 0.3
            return 1.0 if loud else 0.6

        def reset(self):
            pass

    sr = 16000
    loud = (np.random.default_rng(3).normal(0, 0.3, sr // 10)).astype(np.float32)
    quiet = np.zeros(sr // 10, np.float32)
    blocks = [loud] * 15 + [quiet] * 9 + [loud] * 15 + [quiet] * 12
    cfg = {"max_utterance_s": 30, "soft_max_s": 30, "sentence_cut": False}
    assert len(_run_recognizer(cfg, Eng(), blocks, want_finals=2, vad=SoftVad(0.8))) == 2
    assert len(_run_recognizer(cfg, Eng(), blocks, want_finals=2, vad=SoftVad(0.25))) == 1


def test_streaming_vad_keep_modes():
    import importlib.util

    import numpy as np
    import pytest

    if importlib.util.find_spec("silero_vad") is None:
        pytest.skip("沒有安裝 silero-vad")
    from live_translator.asr.vad import StreamingSpeechDetector

    d = StreamingSpeechDetector(end_threshold=0.8, keep="last")
    assert d.end_threshold == 0.8 and d.threshold == 0.5
    x = (np.random.default_rng(0).normal(0, 0.01, 1600)).astype(np.float32)
    p = d.feed(x)
    assert 0.0 <= d.keep <= p <= 1.0
    assert StreamingSpeechDetector().end_threshold == 0.5


def test_glossary_hotwords_switch():
    from live_translator.config import glossary_hotwords

    g = {"白玖ウタノ": "白玖ウタノ", "Universe": "宇宙", "公演": "公演"}
    assert glossary_hotwords({}, g) == ["白玖ウタノ", "公演"]
    assert glossary_hotwords({"glossary_hotwords": False}, g) == []


def test_cer_ops_splits_error_types():
    """評分：拆成錯字／漏字／多字；剪輯字幕當答案時看「漏聽＋聽錯」(S+D)/N。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from eval_asr import cer, cer_ops

    assert cer_ops("あいうえおかき", "あいXえおく") == (2, 0, 1, 6)
    assert cer_ops("あいう", "あいうえお") == (0, 2, 0, 5)
    s, d, i, n = cer_ops("今日は大阪弁でしゃべってないじゃん雨", "今日は雨")
    assert (s, d) == (0, 0) and i > 0 and abs(cer("今日は大阪弁でしゃべってないじゃん雨", "今日は雨") - i / n) < 1e-9
