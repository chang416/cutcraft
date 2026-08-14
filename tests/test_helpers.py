from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


HELPERS = Path(__file__).resolve().parents[1] / "skills" / "cutcraft" / "helpers"
sys.path.insert(0, str(HELPERS))

from apply_transcript_corrections import CorrectionError, apply_corrections, main as corrections_main
from analyze_media import parse_black, parse_freeze, parse_scene_times, parse_silence
from asset_registry import validate_entry
from export_transcript import parse_srt
from project_state import MANIFEST_NAME, load_state, update_state
from project_workspace import ProjectNameError, create_project_workspace, sanitize_project_name
from render import build_subtitles_filter, resolve_subtitle_font, video_map_label
from transcribe import normalize_local_result


SRT = """1
00:00:00,000 --> 00:00:01,000
哈囉 AI

2
00:00:01,100 --> 00:00:02,000
這是測試
"""


class WorkspaceTests(unittest.TestCase):
    def test_chinese_name_and_new_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "CutCraft"
            first = create_project_workspace(root, "台北 街訪", new=False)
            second = create_project_workspace(root, "台北 街訪", new=True)
            self.assertEqual(first["project_name"], "台北 街訪")
            self.assertEqual(second["project_name"], "台北 街訪-2")
            self.assertTrue(Path(first["final_dir"]).is_dir())
            self.assertTrue((Path(first["work_dir"]) / "transcripts").is_dir())
            self.assertTrue((Path(first["work_dir"]) / "proxies").is_dir())
            self.assertTrue((Path(first["project_dir"]) / MANIFEST_NAME).is_file())

    def test_unsafe_names_are_rejected(self) -> None:
        for value in ("", ".", "..", "a/b", "a\\b"):
            with self.subTest(value=value), self.assertRaises(ProjectNameError):
                sanitize_project_name(value)

    def test_project_state_is_resumable_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = create_project_workspace(Path(tmp) / "CutCraft", "可恢復專案")
            project_dir = Path(summary["project_dir"])
            updated = update_state(project_dir, status="awaiting-approval", checkpoint="creative-checkpoint", note="previews ready")
            self.assertEqual(updated["revision"], 2)
            self.assertEqual(load_state(project_dir)["history"][-1]["note"], "previews ready")


class TranscriptTests(unittest.TestCase):
    def test_cue_and_exact_replacements_preserve_timing(self) -> None:
        corrected, changes = apply_corrections(
            SRT,
            [{"cue": 1, "replacement": "哈囉 CutCraft"}],
            [{"old": "測試", "new": "驗證", "cue": 2}],
        )
        self.assertIn("哈囉 CutCraft", corrected)
        self.assertIn("這是驗證", corrected)
        self.assertEqual([cue[1] for cue in parse_srt(corrected)], [cue[1] for cue in parse_srt(SRT)])
        self.assertEqual(len(changes), 2)

    def test_ambiguous_exact_replacement_fails_closed(self) -> None:
        repeated = SRT.replace("這是測試", "AI 測試")
        with self.assertRaises(CorrectionError):
            apply_corrections(repeated, [], [{"old": "AI", "new": "CutCraft"}])

    def test_missing_cue_fails_closed(self) -> None:
        with self.assertRaises(CorrectionError):
            apply_corrections(SRT, [{"cue": 99, "replacement": "不存在"}], [])

    def test_in_place_creates_backup_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "master.srt"
            srt.write_text(SRT, encoding="utf-8")
            corrections_main([
                "--srt", str(srt),
                "--corrections", json.dumps({"cue_replacements": [{"cue": 2, "replacement": "修正完成"}]}, ensure_ascii=False),
                "--in-place",
            ])
            self.assertIn("修正完成", srt.read_text(encoding="utf-8"))
            self.assertEqual(len(list(Path(tmp).glob("master.srt.backup-*"))), 1)
            audit = json.loads(Path(f"{srt}.changes.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["changes"][0]["cue"], 2)


class SubtitleRenderTests(unittest.TestCase):
    def test_unfiltered_source_uses_a_stream_specifier_not_filter_label(self) -> None:
        self.assertEqual(video_map_label(has_video_filter=False), "0:v")
        self.assertEqual(video_map_label(has_video_filter=True), "[outv]")

    def test_filter_supplies_resolved_font_directory_to_libass(self) -> None:
        rendered = build_subtitles_filter(
            Path("/tmp/master.srt"),
            "FontName=Noto Serif TC,FontSize=22,Bold=1",
        )
        self.assertIn(":fontsdir='", rendered)
        self.assertIn(":force_style='FontName=Noto Serif TC,FontSize=22,Bold=1'", rendered)

    def test_all_default_styles_prefer_installed_cjk_font(self) -> None:
        if resolve_subtitle_font("high-contrast-outline") == "Helvetica":
            self.skipTest("No installed CJK default font on this machine")
        self.assertNotEqual(resolve_subtitle_font("natural-sentence"), "Helvetica")
        self.assertNotEqual(resolve_subtitle_font("bold-overlay"), "Helvetica")


class AnalysisTests(unittest.TestCase):
    def test_ffmpeg_diagnostics_are_parsed(self) -> None:
        log = """
        pts_time:1.25
        [silencedetect] silence_start: 2.0
        [silencedetect] silence_end: 3.5 | silence_duration: 1.5
        [blackdetect] black_start:4 black_end:5.25 black_duration:1.25
        [freezedetect] freeze_start: 6
        [freezedetect] freeze_duration: 2.5
        [freezedetect] freeze_end: 8.5
        """
        self.assertEqual(parse_scene_times(log), [1.25])
        self.assertEqual(parse_silence(log)[0]["duration"], 1.5)
        self.assertEqual(parse_black(log)[0]["end"], 5.25)
        self.assertEqual(parse_freeze(log)[0]["duration"], 2.5)


class ProviderAndAssetTests(unittest.TestCase):
    def test_local_transcript_normalizes_to_word_schema(self) -> None:
        result = normalize_local_result({
            "language": "zh",
            "text": "你好",
            "segments": [{"words": [{"word": "你好", "start": 0.1, "end": 0.5}]}],
        }, "mlx-whisper")
        self.assertEqual(result["words"][0]["type"], "word")
        self.assertEqual(result["_cutcraft_provider"], "mlx-whisper")

    def test_third_party_asset_requires_license_url(self) -> None:
        entry = {"title": "Track", "kind": "music", "source": "https://example.com", "license": "CC BY 4.0"}
        self.assertIn("license_url is required for third-party licensed assets", validate_entry(entry))


if __name__ == "__main__":
    unittest.main()
