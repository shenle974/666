# ComfyUI-SpeechTextQC

ComfyUI custom nodes for video speech text QC.

The main node extracts audio from a video, transcribes speech locally with `faster-whisper`, translates the transcript to Chinese through a text API, and compares it with your reference script line by line.

## Nodes

- `Uploaded Video Path`: select a video from the ComfyUI `input` directory.
- `Reference Text Loader`: load reference text from `txt`, `srt`, `ass`, or `vtt` files in the ComfyUI `input` directory.
- `Speech/Text Consistency QC Local`: transcribe locally, translate, align, and generate the QC report.
- `QC Text Viewer/Saver`: display `qc_report` or `qc_json` in ComfyUI and optionally save it as a txt file.

## Install With Git

Clone this repository into ComfyUI's `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/shenle974/666.git
```

Install dependencies in the same Python environment that runs ComfyUI:

```bash
cd /path/to/ComfyUI
python -m pip install -r custom_nodes/ComfyUI-SpeechTextQC/requirements.txt
```

Restart ComfyUI after installation.

## Install With ComfyUI Manager

Until the node is listed in ComfyUI Manager, use Manager's `Install via Git URL` action and paste the Git repository URL.

ComfyUI Manager will clone the repository, install `requirements.txt`, and run `install.py`.

## Requirements

The plugin first looks for system `ffmpeg`. If it is not installed, it uses the bundled `imageio-ffmpeg` dependency from `requirements.txt`.

For most Windows users, installing the plugin dependencies is enough:

```bash
cd /path/to/ComfyUI
python -m pip install -r custom_nodes/666/ComfyUI-SpeechTextQC/requirements.txt
```

You can still install system `ffmpeg` manually if you prefer:

```bash
ffmpeg -version
```

macOS:

```bash
brew install ffmpeg
```

Windows optional manual install:

```powershell
winget install --id Gyan.FFmpeg -e
```

Then restart ComfyUI.

## Translation API

Speech recognition is local and does not need an API key. An API key is only used for Chinese translation.

The node supports OpenAI-compatible text providers:

```text
qwen_dashscope
deepseek
openai
custom_openai_compatible
none
```

Recommended presets:

```text
qwen_dashscope: base_url=https://dashscope.aliyuncs.com/compatible-mode/v1, model=qwen-plus
deepseek: base_url=https://api.deepseek.com, model=deepseek-v4-flash
openai: base_url=https://api.openai.com/v1, model=gpt-4o-mini
```

You can fill `api_key` directly in the node. If `api_key` is empty, the node reads provider-specific environment variables:

```text
qwen_dashscope -> DASHSCOPE_API_KEY
deepseek -> DEEPSEEK_API_KEY
openai -> OPENAI_API_KEY
```

For `custom_openai_compatible`, fill both `api_base_url` and `text_model` yourself.

## Usage

Find the nodes under:

```text
video/audio_qc
```

Typical workflow with a video loader node:

1. Add ComfyUI's video loader node, such as `Load Video`.
2. Put your reference script in `reference_text`, connect it from an upstream text node, or load it with `Reference Text Loader`.
3. Connect the video loader's `video` output to `Speech/Text Consistency QC Local`'s `video` input.
4. Connect `qc_report` to `QC Text Viewer/Saver`.
5. Run the workflow and read the report in `QC Text Viewer/Saver`, or open the saved txt file from ComfyUI `output`.

Reference text is compared line by line. Each non-empty line is treated as one expected sentence.

## Main Inputs

- `video`: connect the output from a video loader node. This is the recommended input.
- `video_path`: optional fallback file name/path under ComfyUI `input`; normally you do not need to fill this.
- `reference_text`: reference script text. Each non-empty line is one sentence.
- `reference_text_file`: optional `txt`, `srt`, `ass`, or `vtt` path. If provided, it overrides `reference_text`.
- `local_model_size`: local faster-whisper model. Start with `small` for Windows CPU; use `medium` or `large-v3` for better accuracy if the computer is strong enough.
- `device`: `auto`, `cpu`, or `cuda`.
- `compute_type`: `auto`, `int8`, `float16`, or `float32`. Use `auto` unless you know the machine.
- `translation_provider`: Chinese translation provider. Choose `qwen_dashscope`, `deepseek`, `openai`, `custom_openai_compatible`, or `none`.
- `api_key`: optional API key for Chinese translation only.
- `api_base_url`: optional for preset providers; required for `custom_openai_compatible`.
- `text_model`: optional for preset providers; required for `custom_openai_compatible`.
- `language`: optional source language hint, such as `th` or `en`. Leave empty for automatic detection.

## Outputs

- `passed`: kept for compatibility; comparison-only mode returns `true`.
- `overall_similarity`: kept for compatibility; comparison-only mode returns `1.0`.
- `transcript_original`: source-language speech transcript.
- `transcript_zh`: simplified Chinese translation.
- `qc_report`: readable Markdown-style QC report.
- `qc_json`: structured JSON with segments, line-by-line reference/transcript pairs, and timestamps.

If your ComfyUI does not have a text preview node, use `QC Text Viewer/Saver` from this plugin.

## Notes

- Local speech recognition uses `faster-whisper`. The selected model downloads on first use into the local model cache.
- Video/audio is not uploaded for speech recognition.
- Chinese translation still uses the configured text API.
- The node uses system `ffmpeg` when available, otherwise it falls back to `imageio-ffmpeg` from Python dependencies. This is designed to work on Windows without manually editing `PATH`.
- Thai and English videos are supported by faster-whisper. For Thai, set `language` to `th` if auto detection is unstable.
