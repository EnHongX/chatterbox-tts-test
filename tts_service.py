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
        "min_tokens": 120,
        "tokens_per_word": 4,
        "max_tokens": 260,
        "sentence_split_threshold": 140,
    },
    "standard": {
        "label": "标准",
        "min_tokens": 180,
        "tokens_per_word": 6,
        "max_tokens": 520,
        "sentence_split_threshold": 220,
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

    def _split_text(self, text: str, threshold: int) -> list[str]:
        normalized = " ".join(text.split())
        if len(normalized) <= threshold:
            return [normalized]

        parts = re.split(r"(?<=[.!?])\s+", normalized)
        segments: list[str] = []
        current = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if not current:
                current = part
                continue

            candidate = f"{current} {part}"
            if len(candidate) <= threshold:
                current = candidate
            else:
                segments.append(current)
                current = part

        if current:
            segments.append(current)

        return segments or [normalized]

    def _estimate_max_gen_len(self, text: str, mode: str) -> int:
        config = GENERATION_MODES[mode]
        word_count = max(1, len(text.split()))
        estimated = config["min_tokens"] + word_count * config["tokens_per_word"]
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
        segments = self._split_text(clean_text, GENERATION_MODES[mode]["sentence_split_threshold"])
        started_at = perf_counter()

        generated_segments: list[torch.Tensor] = []
        for segment in segments:
            max_gen_len = self._estimate_max_gen_len(segment, mode)
            generated_segments.append(self._generate_segment(model, segment, max_gen_len=max_gen_len))

        if len(generated_segments) == 1:
            wav = generated_segments[0]
        else:
            gap = torch.zeros(1, int(model.sr * 0.18))
            stitched: list[torch.Tensor] = []
            for index, segment_wav in enumerate(generated_segments):
                if index > 0:
                    stitched.append(gap)
                stitched.append(segment_wav)
            wav = torch.cat(stitched, dim=1)

        ta.save(str(output_path), wav, model.sr)
        elapsed_seconds = perf_counter() - started_at

        return GenerationResult(
            status="completed",
            elapsed_seconds=elapsed_seconds,
            output_path=output_path,
            reference_audio_path=reference_audio_path,
            mode=mode,
            segment_count=len(segments),
        )


tts_service = TTSService()
