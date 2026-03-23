# Chatterbox-Turbo Local Web TTS

一个基于本地 `Chatterbox-Turbo` 的最小可用 Web TTS 项目。

这个项目提供一个简单网页，用来输入英文文本、调用本地模型生成语音，并在页面中显示：

- 生成状态
- 总耗时
- 输出文件路径
- 音频试听播放器

项目默认运行在本地虚拟环境 `.venv` 中，适合已经在 Mac 上跑通 `Chatterbox-Turbo` 的场景。

## Features

- 本地 Web 页面，直接在浏览器操作
- 后端使用 Python `FastAPI`
- 参考声音文件以下拉选择方式从 `input/` 目录读取
- 默认参考声音文件：`input/dark_gaming_voice_prompt.mp3`
- 默认输出目录：`output/`
- 输出文件名自动带时间戳
- 支持 `极速 / 标准` 两种生成模式
- 长文本会自动按句子分段生成，减少单次等待
- 集成 Mac 兼容处理，包含 `DummyWatermarker` 兜底
- 前端无重型框架，只有 HTML + 少量原生 JS
- 生成完成后支持一键播放音频、显示文件、打开输出目录

## Project Structure

```text
chatterbox-tts-test/
├── app.py
├── tts_service.py
├── templates/
│   └── index.html
├── scripts/
│   └── test_tts.py
├── input/
│   └── dark_gaming_voice_prompt.mp3
├── output/
└── .venv/
```

## Requirements

- macOS
- Python 3.11
- 已在本地成功安装并跑通 `Chatterbox-Turbo`
- 必须使用项目内虚拟环境 `.venv`

## Python Version

推荐优先使用 `Python 3.11`。

这是因为 `Chatterbox-Turbo` 官方仓库明确说明其开发和测试环境基于 `Python 3.11`。在实际本地尝试中，较新的 Python 版本可能会出现依赖兼容问题。

如果你之前尝试过 `Python 3.14` 并遇到安装或运行失败，建议直接切换回 `Python 3.11`，不要继续使用系统 Python。

## Install

如果你已经有 `.venv` 并且已经安装好依赖，可以直接跳到“Run”。

### 1. Clone project

```bash
git clone <your-repo-url>
cd chatterbox-tts-test
```

### 2. Create virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

激活后终端应类似：

```bash
(.venv) YOU@MY chatterbox-tts-test
```

### 3. Install dependencies

请在已经激活 `.venv` 的前提下安装依赖。

如果你已经有可用环境，可跳过这一步。

```bash
source .venv/bin/activate
pip install fastapi uvicorn
```

`Chatterbox-Turbo`、`torch`、`torchaudio`、`perth` 请按你当前本地已验证成功的方式安装。

## Configuration

### 1. Reference audio

默认参考声音文件路径：

```text
input/dark_gaming_voice_prompt.mp3
```

请确保该文件存在。

你可以从 Chatterbox-Turbo 官方 Demo 页面挑选音频并下载：

[Chatterbox Turbo Demo Page](https://resemble-ai.github.io/chatterbox_turbo_demopage/)

下载后把音频文件放到项目的 `input/` 目录中，例如：

```text
input/dark_gaming_voice_prompt.mp3
```

如果你要更换参考声音：

1. 把新的音频文件放进 `input/`
2. 刷新页面
3. 在网页的参考声音下拉框中选择对应文件

例如：

```text
input/my_voice_prompt.wav
```

### 2. Output directory

生成音频会输出到：

```text
output/
```

输出文件名格式类似：

```text
output/tts_20260323_142530.wav
```

### 3. Generation modes

页面支持两种模式：

- `极速`：优先缩短等待时间，适合快速试听和调参考音色
- `标准`：更稳一些，适合正式导出

对于较长文本，后端会自动按句子切分后逐段生成，再拼接成一个输出文件。

## Run

所有命令都默认基于项目内 `.venv`。

### Start server

```bash
cd /YOUR-PATH/chatterbox-tts-test
source .venv/bin/activate
python app.py
```

或者：

```bash
cd /YOUR-PATH/chatterbox-tts-test
.venv/bin/python app.py
```

### Open web page

服务启动后，在浏览器访问：

```text
http://127.0.0.1:8010
```

也可以用：

```text
http://localhost:8010
```

## How To Use

1. 打开网页
2. 在文本框输入英文文本
3. 在下拉框中选择参考声音文件
4. 选择生成模式：
   - 极速
   - 标准
5. 点击“生成音频”
6. 等待页面显示：
   - 当前状态
   - 总耗时
   - 输出文件路径
   - 音频播放器
   - 当前使用的生成模式
   - 分段数量
7. 生成完成后可使用快捷按钮：
   - 播放音频
   - 显示文件
   - 打开输出目录

## API

### `GET /api/config`

返回默认配置：

```json
{
  "default_reference_audio_path": "input/dark_gaming_voice_prompt.mp3",
  "output_dir": "output",
  "reference_audio_options": [
    "input/dark_gaming_voice_prompt.mp3"
  ],
  "generation_modes": [
    { "value": "fast", "label": "极速" },
    { "value": "standard", "label": "标准" }
  ],
  "default_generation_mode": "standard"
}
```

### `POST /api/generate`

请求示例：

```bash
curl -X POST http://127.0.0.1:8010/api/generate \
  -H "Content-Type: application/json" \
  -d '{"text":"This is a local web TTS test.","reference_audio_path":"input/dark_gaming_voice_prompt.mp3","mode":"fast"}'
```

返回示例：

```json
{
  "status": "completed",
  "elapsed_seconds": 12.34,
  "output_file_path": "output/tts_20260323_142530.wav",
  "audio_url": "/output/tts_20260323_142530.wav",
  "reference_audio_path": "/YOUR-PATH/chatterbox-tts-test/input/dark_gaming_voice_prompt.mp3",
  "mode": "fast",
  "segment_count": 2
}
```

## Mac Compatibility

项目中已经保留对 Mac 的兼容处理：

- 优先使用 `mps`
- 若 `mps` 不可用则回退到 `cpu`
- 集成 `DummyWatermarker` 兜底，避免部分 `Perth` 水印对象异常导致 Turbo 报错

兼容逻辑位置：

- `tts_service.py`

## Development Notes

- 页面模板：`templates/index.html`
- Web 服务入口：`app.py`
- TTS 封装：`tts_service.py`
- 现有命令行测试脚本：`scripts/test_tts.py`

## Git Ignore

仓库已建议忽略以下内容：

- `.venv/`
- `output/`
- 本地输入音频文件
- 模型缓存目录
- Python 缓存文件
- `.env`

如果你准备开源，这些内容通常不应上传到 GitHub。

## Recommended Open Source Workflow

如果你准备公开仓库，建议：

1. 不要提交 `.venv/`
2. 不要提交本地模型缓存和权重
3. 不要提交 `output/` 中生成的音频
4. 不要提交你自己的参考声音素材
5. 在 README 中写清楚用户需要自行准备 `input/dark_gaming_voice_prompt.mp3`

## Troubleshooting

### 1. Reference audio not found

请检查：

- `input/dark_gaming_voice_prompt.mp3` 是否存在
- 页面中的参考音频路径是否填写正确

### 2. Turbo generation failed

请先确认你本地原有测试脚本可以运行：

```bash
cd /YOUR-PATH/chatterbox-tts-test
source .venv/bin/activate
python scripts/test_tts.py
```

如果这个脚本不能运行，说明问题不在 Web 层，而在本地模型环境本身。

### 3. Browser cannot open page

确认服务已启动，并访问：

```text
http://127.0.0.1:8010
```

## License

根据你的开源计划自行补充，例如：

```text
MIT
```
