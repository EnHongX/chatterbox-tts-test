import torch
import torchaudio as ta
import perth

# ---- 绕过 Perth 水印异常 ----
if getattr(perth, "PerthImplicitWatermarker", None) is None:
    class DummyWatermarker:
        def __call__(self, wav, sample_rate):
            return wav
        def apply_watermark(self, wav, sample_rate=None):
            return wav

    perth.PerthImplicitWatermarker = DummyWatermarker

from chatterbox.tts_turbo import ChatterboxTurboTTS

device = "mps" if torch.backends.mps.is_available() else "cpu"

model = ChatterboxTurboTTS.from_pretrained(device=device)

text = "This is a dark and suspenseful narration. [chuckle] Something was moving behind the door."

wav = model.generate(
    text,
    audio_prompt_path="input/dark_gaming_voice_prompt.mp3"
)

ta.save("output/test-turbo.wav", wav, model.sr)
print("done: output/test-turbo.wav")