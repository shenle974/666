# ComfyUI-SpeechTextQC

ComfyUI custom nodes for video speech text QC.

The main node extracts audio from a video, sends the audio to an OpenAI or OpenAI-compatible transcription API, translates the transcript to Chinese through a text API, and compares it with your reference script line by line.

## Nodes

- `Uploaded Video Path`: select a video from the ComfyUI `input` directory.
- `Reference Text Loader`: load reference text from `txt`, `srt`, `ass`, or `vtt` files in the ComfyUI `input` directory.
- `Speech/Text Consistency QC API`: transcribe, translate, align, and generate the QC report.

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

You need `ffmpeg` available on the command line:

```bash
ffmpeg -version
```

macOS:

```bash
brew install ffmpeg
```

Windows users can install ffmpeg with a package manager or from the official ffmpeg builds, then make sure `ffmpeg.exe` is available in `PATH`.

## API Key

Recommended: set an environment variable before starting ComfyUI:

```bash
export OPENAI_API_KEY="sk-..."
```

You can also fill `api_key` directly in the node. Leaving `api_key` empty makes the node read `OPENAI_API_KEY`.

For OpenAI-compatible services, set `api_base_url` in the node. The default is:

```text
https://api.openai.com/v1
```

## Usage

Find the nodes under:

```text
video/audio_qc
```

Typical workflow:

1. Put your video in ComfyUI `input`, then select it with `Uploaded Video Path`.
2. Put your reference script in `reference_text`, connect it from an upstream text node, or load it with `Reference Text Loader`.
3. Connect the video path and reference text to `Speech/Text Consistency QC API`.
4. Run the workflow and read `qc_report` or pass `qc_json` to downstream nodes.

Reference text is compared line by line. Each non-empty line is treated as one expected sentence.

## Main Inputs

- `video_path`: absolute video path, or a file name/path under ComfyUI `input`.
- `reference_text`: reference script text. Each non-empty line is one sentence.
- `reference_text_file`: optional `txt`, `srt`, `ass`, or `vtt` path. If provided, it overrides `reference_text`.
- `api_key`: optional API key. If empty, `OPENAI_API_KEY` is used.
- `api_base_url`: API base URL for OpenAI or an OpenAI-compatible provider.
- `speech_model`: transcription model. `whisper-1` is the default because it supports segment timestamps.
- `text_model`: text model used for Chinese translation.
- `language`: optional source language hint, such as `zh`, `en`, or `ja`. Leave empty for automatic detection.
- `similarity_threshold`: overall pass threshold.
- `sentence_threshold`: per-line pass threshold.

## Outputs

- `passed`: boolean QC result.
- `overall_similarity`: overall similarity between the reference script and Chinese transcript.
- `transcript_original`: source-language speech transcript.
- `transcript_zh`: simplified Chinese translation.
- `qc_report`: readable Markdown-style QC report.
- `qc_json`: structured JSON with segments, line-by-line scores, timestamps, and issue summaries.

## Notes

- No local speech or translation model is bundled or downloaded.
- The node compresses extracted audio to mp3 before upload. If the extracted audio is still larger than 24MB, split the video and run QC per segment.
- `whisper-1` returns segment timestamps. Newer transcription models may return text without segment timestamps depending on provider support.
