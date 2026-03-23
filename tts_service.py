from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

import perth
import torch
import torchaudio as ta


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_REFERENCE_AUDIO = INPUT_DIR / "dark_gaming_voice_prompt.mp3"
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def _patch_perth_watermarker() -> None:
    """
    Mac 上部分环境会因为 Perth 水印对象缺失而报错，这里做兜底处理。
    """
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        class DummyWatermarker:
            def __call__(self, wav, sample_rate):
                return wav

            def apply_watermark(self, wav, sample_rate=None):
                return wav

        perth.PerthImplicitWatermarker = DummyWatermarker


_patch_perth_watermarker()

from chatterbox.tts_turbo import ChatterboxTurboTTS, S3GEN_SIL, punc_norm  # noqa: E402


GENERATION_MODES = {
    "fast": {
        "label": "极速",
        "min_tokens": 40,
        "base_tokens": 18,
        "chars_to_tokens": 0.82,
        "max_tokens": 140,
        "sentence_split_threshold": 90,
        "hard_char_limit": 110,
        "fallback_word_limit": 16,
        "default_pause_seconds": 0.16,
        "paragraph_pause_seconds": 0.28,
    },
    "standard": {
        "label": "标准",
        "min_tokens": 56,
        "base_tokens": 26,
        "chars_to_tokens": 1.0,
        "max_tokens": 220,
        "sentence_split_threshold": 130,
        "hard_char_limit": 150,
        "fallback_word_limit": 22,
        "default_pause_seconds": 0.20,
        "paragraph_pause_seconds": 0.34,
    },
}


def get_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def list_reference_audios() -> list[str]:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    audio_files = [
        path.relative_to(BASE_DIR).as_posix()
        for path in sorted(INPUT_DIR.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]
    return audio_files


@dataclass
class GenerationResult:
    status: str
    elapsed_seconds: float
    output_path: Path
    reference_audio_path: Path
    mode: str
    segment_count: int


@dataclass
class SegmentPlan:
    text: str
    pause_after_seconds: float


class TTSService:
    def __init__(self) -> None:
        self._model = None
        self._model_lock = threading.Lock()
        self._conditionals_cache: dict[str, object] = {}
        self._conditionals_lock = threading.Lock()

    def _load_model(self) -> ChatterboxTurboTTS:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = ChatterboxTurboTTS.from_pretrained(device=get_device())
        return self._model

    def _clone_value(self, value):
        if torch.is_tensor(value):
            return value.detach().clone()
        if isinstance(value, dict):
            return {key: self._clone_value(sub_value) for key, sub_value in value.items()}
        if isinstance(value, list):
            return [self._clone_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._clone_value(item) for item in value)
        return value

    def _clone_conditionals(self, conds):
        t3_cls = conds.t3.__class__
        t3_data = {
            key: self._clone_value(value)
            for key, value in conds.t3.__dict__.items()
        }
        gen_data = self._clone_value(conds.gen)
        cloned = conds.__class__(t3_cls(**t3_data), gen_data)
        return cloned

    def _set_cached_conditionals(self, model: ChatterboxTurboTTS, reference_audio_path: Path) -> None:
        cache_key = str(reference_audio_path)

        with self._conditionals_lock:
            cached_conds = self._conditionals_cache.get(cache_key)

        if cached_conds is None:
            model.prepare_conditionals(str(reference_audio_path), exaggeration=0.0, norm_loudness=True)
            cached_conds = self._clone_conditionals(model.conds)
            with self._conditionals_lock:
                self._conditionals_cache[cache_key] = cached_conds

        model.conds = self._clone_conditionals(cached_conds).to(device=model.device)

    def preload(self) -> None:
        """
        在服务启动后后台预热模型和默认参考音频，减少首次生成等待。
        """
        try:
            model = self._load_model()
            if DEFAULT_REFERENCE_AUDIO.exists():
                self._set_cached_conditionals(model, DEFAULT_REFERENCE_AUDIO.resolve())
        except Exception:
            # 预热失败不应影响服务启动，首次真实请求时会再按正常逻辑加载。
            pass

    def _split_text_for_mode(self, text: str, mode: str) -> list[SegmentPlan]:
        config = GENERATION_MODES[mode]
        threshold = config["sentence_split_threshold"]
        hard_char_limit = config["hard_char_limit"]
        fallback_word_limit = config["fallback_word_limit"]

        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return [SegmentPlan(text="", pause_after_seconds=0.0)]

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
        plans: list[SegmentPlan] = []

        for paragraph_index, paragraph in enumerate(paragraphs):
            units = self._extract_rhythm_units(
                paragraph=paragraph,
                threshold=threshold,
                hard_char_limit=hard_char_limit,
                fallback_word_limit=fallback_word_limit,
            )
            segments = self._merge_units_by_rhythm(units)

            for segment_index, segment_text in enumerate(segments):
                is_last_in_paragraph = segment_index == len(segments) - 1
                has_more_paragraphs = paragraph_index < len(paragraphs) - 1
                pause_after_seconds = self._estimate_pause_after(
                    segment_text=segment_text,
                    default_pause_seconds=config["default_pause_seconds"],
                    paragraph_pause_seconds=config["paragraph_pause_seconds"],
                    paragraph_break=is_last_in_paragraph and has_more_paragraphs,
                )
                plans.append(SegmentPlan(text=segment_text, pause_after_seconds=pause_after_seconds))

        if plans:
            plans[-1].pause_after_seconds = 0.0

        return plans

    def _extract_rhythm_units(
        self,
        paragraph: str,
        threshold: int,
        hard_char_limit: int,
        fallback_word_limit: int,
    ) -> list[str]:
        raw_lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        source_units = raw_lines if len(raw_lines) > 1 else [paragraph.strip()]
        units: list[str] = []

        for unit in source_units:
            units.extend(self._split_unit_by_punctuation(unit, threshold, hard_char_limit, fallback_word_limit))

        return [unit for unit in units if unit]

    def _split_unit_by_punctuation(
        self,
        text: str,
        threshold: int,
        hard_char_limit: int,
        fallback_word_limit: int,
    ) -> list[str]:
        text = " ".join(text.split()).strip()
        if not text:
            return []
        if len(text) <= threshold:
            return [text]

        sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()]
        if len(sentence_parts) > 1:
            results: list[str] = []
            for sentence in sentence_parts:
                results.extend(self._split_unit_by_punctuation(sentence, threshold, hard_char_limit, fallback_word_limit))
            return results

        clause_parts = [
            part.strip()
            for part in re.split(r"(?<=[,;:])\s+|(?<=\])\s+|(?<=—)\s+", text)
            if part.strip()
        ]
        if len(clause_parts) > 1:
            results: list[str] = []
            for clause in clause_parts:
                results.extend(self._split_unit_by_punctuation(clause, threshold, hard_char_limit, fallback_word_limit))
            return results

        if len(text) <= hard_char_limit:
            return [text]

        return self._split_by_words_as_fallback(text, fallback_word_limit)

    def _merge_units_by_rhythm(self, units: list[str]) -> list[str]:
        merged: list[str] = []
        index = 0

        while index < len(units):
            current = units[index].strip()
            while index + 1 < len(units) and self._should_merge_with_next(current, units[index + 1]):
                current = f"{current} {units[index + 1].strip()}"
                index += 1
            merged.append(current)
            index += 1

        return merged

    def _should_merge_with_next(self, current: str, next_unit: str) -> bool:
        current = current.strip()
        next_unit = next_unit.strip()
        if not current or not next_unit:
            return False

        if current.endswith(("—", "-", ":")):
            return True
        if current.endswith(("…", "...")) and len(next_unit) <= 40:
            return True
        if next_unit.startswith(("“", "\"", "‘", "'")):
            return len(current) <= 80
        return False

    def _split_by_words_as_fallback(self, text: str, max_words: int) -> list[str]:
        words = text.split()
        chunks: list[str] = []
        for index in range(0, len(words), max_words):
            chunk = " ".join(words[index:index + max_words]).strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def _estimate_pause_after(
        self,
        segment_text: str,
        default_pause_seconds: float,
        paragraph_pause_seconds: float,
        paragraph_break: bool,
    ) -> float:
        pause = paragraph_pause_seconds if paragraph_break else default_pause_seconds
        stripped = segment_text.rstrip()

        if stripped.endswith(("…", "...", "—", ":")):
            pause += 0.08
        elif stripped.endswith(("?", "!")):
            pause += 0.04
        elif len(stripped) <= 24:
            pause += 0.03

        return pause

    def _estimate_max_gen_len(self, text: str, mode: str) -> int:
        config = GENERATION_MODES[mode]
        char_count = max(1, len(text))
        punctuation_bonus = min(20, len(re.findall(r"[,.!?;:…]", text)) * 4)
        estimated = config["base_tokens"] + int(char_count * config["chars_to_tokens"]) + punctuation_bonus
        estimated = max(config["min_tokens"], estimated)
        return min(config["max_tokens"], estimated)

    def _generate_segment(self, model: ChatterboxTurboTTS, text: str, max_gen_len: int) -> torch.Tensor:
        normalized_text = punc_norm(text)
        text_tokens = model.tokenizer(normalized_text, return_tensors="pt", padding=True, truncation=True)
        text_tokens = text_tokens.input_ids.to(model.device)

        speech_tokens = model.t3.inference_turbo(
            t3_cond=model.conds.t3,
            text_tokens=text_tokens,
            temperature=0.8,
            top_k=1000,
            top_p=0.95,
            repetition_penalty=1.2,
            max_gen_len=max_gen_len,
        )

        speech_tokens = speech_tokens[speech_tokens < 6561]
        speech_tokens = speech_tokens.to(model.device)
        silence = torch.tensor([S3GEN_SIL, S3GEN_SIL, S3GEN_SIL]).long().to(model.device)
        speech_tokens = torch.cat([speech_tokens, silence])

        wav, _ = model.s3gen.inference(
            speech_tokens=speech_tokens,
            ref_dict=model.conds.gen,
            n_cfm_timesteps=2,
        )
        wav = wav.squeeze(0).detach().cpu()
        watermarked_wav = model.watermarker.apply_watermark(wav.numpy(), sample_rate=model.sr)
        return torch.from_numpy(watermarked_wav).unsqueeze(0)

    def generate(
        self,
        text: str,
        audio_prompt_path: str | Path | None = None,
        mode: str = "standard",
    ) -> GenerationResult:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Text cannot be empty.")
        if mode not in GENERATION_MODES:
            raise ValueError(f"Unsupported mode: {mode}")

        reference_audio_path = Path(audio_prompt_path or DEFAULT_REFERENCE_AUDIO)
        if not reference_audio_path.is_absolute():
            reference_audio_path = BASE_DIR / reference_audio_path
        reference_audio_path = reference_audio_path.resolve()

        if not reference_audio_path.exists():
            raise FileNotFoundError(f"Reference audio not found: {reference_audio_path}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"tts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

        model = self._load_model()
        self._set_cached_conditionals(model, reference_audio_path)
        segment_plans = self._split_text_for_mode(clean_text, mode)
        started_at = perf_counter()

        stitched: list[torch.Tensor] = []
        for segment_plan in segment_plans:
            max_gen_len = self._estimate_max_gen_len(segment_plan.text, mode)
            segment_wav = self._generate_segment(model, segment_plan.text, max_gen_len=max_gen_len)
            stitched.append(segment_wav)

            if segment_plan.pause_after_seconds > 0:
                pause_samples = int(model.sr * segment_plan.pause_after_seconds)
                stitched.append(torch.zeros((1, pause_samples), dtype=segment_wav.dtype))

        wav = stitched[0] if len(stitched) == 1 else torch.cat(stitched, dim=1)

        ta.save(str(output_path), wav, model.sr)
        elapsed_seconds = perf_counter() - started_at

        return GenerationResult(
            status="completed",
            elapsed_seconds=elapsed_seconds,
            output_path=output_path,
            reference_audio_path=reference_audio_path,
            mode=mode,
            segment_count=len(segment_plans),
        )


tts_service = TTSService()
