import difflib
import json
import os
import re
import shutil
import string
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path


try:
    import folder_paths
except Exception:
    folder_paths = None


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".srt",
    ".ass",
    ".vtt",
}

SPEECH_MODEL_OPTIONS = [
    "whisper-1",
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
]

TEXT_MODEL_OPTIONS = [
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-4o",
]

DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
MAX_AUDIO_UPLOAD_BYTES = 24 * 1024 * 1024


def _input_dir():
    if folder_paths is not None:
        try:
            return Path(folder_paths.get_input_directory())
        except Exception:
            pass
    return Path.cwd()


def _output_dir():
    if folder_paths is not None:
        try:
            return Path(folder_paths.get_output_directory())
        except Exception:
            pass
    return Path.cwd()


def resolve_path(value):
    raw = (value or "").strip().strip("\"'")
    if not raw:
        return None

    path = Path(raw).expanduser()
    if path.is_absolute() and path.exists():
        return path

    candidates = [
        _input_dir() / raw,
        Path.cwd() / raw,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"找不到文件: {raw}。请填写绝对路径，或把文件放到 ComfyUI/input 后填写文件名。"
    )


def list_input_files(extensions):
    root = _input_dir()
    matches = []
    if root.exists():
        for current_root, _, files in os.walk(root):
            for file_name in files:
                path = Path(current_root) / file_name
                if path.suffix.lower() in extensions:
                    matches.append(str(path.relative_to(root)))
    return sorted(matches) or [""]


def list_input_videos():
    return list_input_files(VIDEO_EXTENSIONS)


def list_input_text_files():
    return list_input_files(TEXT_EXTENSIONS)


def ensure_ffmpeg():
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "未找到 ffmpeg。请安装插件依赖 `imageio-ffmpeg`，或手动安装 ffmpeg 并加入 PATH。"
            "在 ComfyUI 的 Python 环境里执行: "
            "`python -m pip install -r custom_nodes/666/ComfyUI-SpeechTextQC/requirements.txt`。"
        ) from exc


def extract_audio(video_path, audio_path):
    ffmpeg_exe = ensure_ffmpeg()
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    subprocess.run(cmd, check=True)

    if audio_path.stat().st_size > MAX_AUDIO_UPLOAD_BYTES:
        raise RuntimeError(
            "抽取后的音频超过 24MB。请缩短视频，或先把视频分段后再进行质检。"
        )


def _strip_srt_or_vtt(text):
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _strip_ass(text):
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) == 10:
            cleaned = re.sub(r"\{[^}]*\}", "", parts[9])
            cleaned = cleaned.replace(r"\N", "\n").replace(r"\n", "\n")
            lines.append(cleaned.strip())
    return "\n".join(item for item in lines if item)


def read_text_file(path):
    text = path.read_text(encoding="utf-8-sig").strip()
    suffix = path.suffix.lower()
    if suffix in {".srt", ".vtt"}:
        return _strip_srt_or_vtt(text)
    if suffix == ".ass":
        return _strip_ass(text)
    return text


def load_reference_text(reference_text, reference_text_file):
    file_path = resolve_path(reference_text_file) if (reference_text_file or "").strip() else None
    if file_path is not None:
        return read_text_file(file_path).strip()
    return (reference_text or "").strip()


def split_reference_lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def normalize_text(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    punctuation = string.punctuation + "，。！？；：“”‘’（）【】《》、…—·￥"
    text = re.sub(f"[{re.escape(punctuation)}]", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def similarity_ratio(reference, transcript):
    normalized_ref = normalize_text(reference)
    normalized_transcript = normalize_text(transcript)
    if not normalized_ref and not normalized_transcript:
        return 1.0
    if not normalized_ref or not normalized_transcript:
        return 0.0

    try:
        from rapidfuzz import fuzz

        return fuzz.ratio(normalized_ref, normalized_transcript) / 100.0
    except ImportError:
        return difflib.SequenceMatcher(None, normalized_ref, normalized_transcript).ratio()


def tokenize_for_diff(text):
    normalized = unicodedata.normalize("NFKC", text or "")
    if re.search(r"[\u3400-\u9fff]", normalized):
        return [char for char in normalized if not char.isspace()]
    return re.findall(r"\w+|[^\w\s]", normalized, flags=re.UNICODE)


def preview_tokens(tokens, max_len=40):
    text = "".join(tokens)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def build_diff_summary(reference, transcript, max_items=8):
    ref_tokens = tokenize_for_diff(reference)
    hyp_tokens = tokenize_for_diff(transcript)
    matcher = difflib.SequenceMatcher(None, ref_tokens, hyp_tokens)

    issues = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        ref_part = preview_tokens(ref_tokens[i1:i2])
        hyp_part = preview_tokens(hyp_tokens[j1:j2])
        if tag == "delete":
            issues.append(f"缺失参考内容: 「{ref_part}」")
        elif tag == "insert":
            issues.append(f"识别/译文多出内容: 「{hyp_part}」")
        elif tag == "replace":
            issues.append(f"内容不一致: 参考「{ref_part}」 / 识别译文「{hyp_part}」")
        if len(issues) >= max_items:
            issues.append("差异较多，已截断。")
            break

    return issues


def format_segments(segments):
    lines = []
    for item in segments:
        start = item.get("start")
        end = item.get("end")
        text = item.get("text", "")
        if start is None or end is None:
            lines.append(text)
        else:
            lines.append(f"[{float(start):.2f}s - {float(end):.2f}s] {text}")
    return "\n".join(line for line in lines if line)


def make_openai_client(api_key, api_base_url):
    key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "缺少 API key。请在节点 api_key 填写，或设置环境变量 OPENAI_API_KEY。"
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "缺少 openai 依赖。请在 ComfyUI 的 Python 环境里执行 "
            "`pip install -r custom_nodes/ComfyUI-SpeechTextQC/requirements.txt`。"
        ) from exc

    kwargs = {"api_key": key}
    base_url = (api_base_url or "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def object_to_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def parse_segment(segment):
    data = object_to_dict(segment)
    text = (data.get("text") or "").strip()
    if not text:
        return None
    start = data.get("start")
    end = data.get("end")
    parsed = {"text": text}
    if start is not None:
        parsed["start"] = round(float(start), 3)
    if end is not None:
        parsed["end"] = round(float(end), 3)
    return parsed


def split_text_to_units(text):
    parts = re.split(r"(?<=[。！？!?；;.!?])\s+|(?<=[。！？!?；;.!?])", text or "")
    units = []
    for part in parts:
        cleaned = part.strip()
        if cleaned:
            units.append({"text": cleaned})
    return units or ([{"text": text.strip()}] if (text or "").strip() else [])


def transcribe_audio_api(client, audio_path, speech_model, language):
    model = (speech_model or "whisper-1").strip()
    lang = (language or "").strip() or None
    supports_verbose_segments = model == "whisper-1"

    params = {
        "model": model,
        "response_format": "verbose_json" if supports_verbose_segments else "json",
    }
    if supports_verbose_segments:
        params["timestamp_granularities"] = ["segment"]
    if lang:
        params["language"] = lang

    with audio_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(file=audio_file, **params)

    data = object_to_dict(response)
    transcript = (data.get("text") or getattr(response, "text", "") or "").strip()
    raw_segments = data.get("segments") or []
    segments = []
    for raw_segment in raw_segments:
        segment = parse_segment(raw_segment)
        if segment:
            segments.append(segment)

    if not segments and transcript:
        segments = split_text_to_units(transcript)

    detected_language = data.get("language") or lang or "auto"
    return transcript, segments, detected_language


def extract_json_object(text):
    content = (text or "").strip()
    if not content:
        raise ValueError("API 返回为空，无法解析 JSON。")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        raise ValueError(f"API 没有返回 JSON 对象: {content[:200]}")
    return json.loads(match.group(0))


def response_text(response):
    direct = getattr(response, "output_text", None)
    if direct:
        return direct

    data = object_to_dict(response)
    chunks = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                text = content.get("text")
                if text:
                    chunks.append(text)
    return "\n".join(chunks).strip()


def translate_texts_to_chinese(client, text_model, texts):
    source_items = [text or "" for text in texts]
    if not source_items:
        return []
    if not any(item.strip() for item in source_items):
        return source_items

    payload = {
        "items": [{"index": index, "text": text} for index, text in enumerate(source_items)]
    }
    prompt = (
        "你是视频语音质检工具的一部分。请把 items 里的 text 翻译为简体中文。"
        "保持原意，不要补写、解释或合并句子。"
        "只返回 JSON 对象，格式为 {\"translations\":[{\"index\":0,\"text\":\"...\"}]}。\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    model = (text_model or "gpt-4o-mini").strip()

    try:
        response = client.responses.create(model=model, input=prompt)
        parsed = extract_json_object(response_text(response))
    except Exception as responses_error:
        try:
            response = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是翻译 API。只返回 JSON，不要返回 Markdown。"
                            "把输入文本逐条翻译为简体中文。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            parsed = extract_json_object(response.choices[0].message.content)
        except Exception as chat_error:
            raise RuntimeError(
                "翻译 API 调用失败。Responses API 错误: "
                f"{responses_error}; Chat Completions 兼容调用错误: {chat_error}"
            ) from chat_error

    translated = list(source_items)
    for item in parsed.get("translations", []) or []:
        try:
            index = int(item.get("index"))
        except Exception:
            continue
        if 0 <= index < len(translated):
            translated[index] = (item.get("text") or "").strip()
    return translated


def group_transcript_units(reference_lines, transcript_units):
    if not reference_lines:
        return []
    if not transcript_units:
        return [[] for _ in reference_lines]

    ref_lengths = [max(1, len(normalize_text(line))) for line in reference_lines]
    total_ref_length = sum(ref_lengths)
    total_units = len(transcript_units)
    groups = []
    start = 0
    consumed_ratio = 0.0

    for index, ref_length in enumerate(ref_lengths):
        if index == len(ref_lengths) - 1:
            end = total_units
        else:
            consumed_ratio += ref_length / total_ref_length
            end = round(consumed_ratio * total_units)
            end = max(start + 1, min(end, total_units - (len(ref_lengths) - index - 1)))
        groups.append(transcript_units[start:end])
        start = end
    return groups


def merge_group_text(group):
    return "".join(item.get("text", "").strip() for item in group).strip()


def group_start_end(group):
    starts = [item.get("start") for item in group if item.get("start") is not None]
    ends = [item.get("end") for item in group if item.get("end") is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def build_qc_rows(reference_lines, transcript_segments, translated_lines, sentence_threshold):
    groups = group_transcript_units(reference_lines, transcript_segments)
    rows = []
    for index, reference in enumerate(reference_lines):
        group = groups[index] if index < len(groups) else []
        original = merge_group_text(group)
        translated = translated_lines[index] if index < len(translated_lines) else ""
        score = similarity_ratio(reference, translated)
        start, end = group_start_end(group)
        issues = build_diff_summary(reference, translated, max_items=5)
        rows.append(
            {
                "index": index + 1,
                "passed": score >= float(sentence_threshold),
                "similarity": round(float(score), 4),
                "start": start,
                "end": end,
                "reference": reference,
                "transcript_original": original,
                "transcript_zh": translated,
                "issues": issues or ["未发现明显差异。"],
            }
        )
    return rows


def build_report(
    passed,
    overall_similarity,
    sentence_threshold,
    similarity_threshold,
    detected_language,
    video,
    reference,
    transcript,
    transcript_zh,
    segments,
    rows,
):
    failed_rows = [row for row in rows if not row["passed"]]
    report_lines = [
        f"质检结果: {'通过' if passed else '未通过'}",
        f"整体相似度: {overall_similarity:.4f}",
        f"整体阈值: {float(similarity_threshold):.4f}",
        f"单句阈值: {float(sentence_threshold):.4f}",
        f"识别语言: {detected_language}",
        f"失败句数: {len(failed_rows)} / {len(rows)}",
        f"视频: {video}",
        "",
        "参考文本:",
        reference,
        "",
        "视频原语言转写:",
        transcript or "(无转写内容)",
        "",
        "中文译文:",
        transcript_zh or "(无译文)",
        "",
        "时间轴转写:",
        format_segments(segments) or "(无转写内容)",
        "",
        "逐句对照:",
    ]

    for row in rows:
        time_range = ""
        if row["start"] is not None and row["end"] is not None:
            time_range = f" [{row['start']:.2f}s - {row['end']:.2f}s]"
        report_lines.extend(
            [
                "",
                f"{row['index']}. {'通过' if row['passed'] else '未通过'}"
                f" | 相似度 {row['similarity']:.4f}{time_range}",
                f"参考: {row['reference']}",
                f"识别原文: {row['transcript_original'] or '(空)'}",
                f"中文译文: {row['transcript_zh'] or '(空)'}",
                "差异: " + "；".join(row["issues"]),
            ]
        )
    return "\n".join(report_lines)


class SpeechTextConsistencyQC:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "视频绝对路径，或 ComfyUI/input 里的文件名，例如 demo.mp4",
                    },
                ),
                "reference_text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "粘贴或连接上游节点输出的参考文案；每行作为一句",
                    },
                ),
                "api_key": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "可选；为空时读取 OPENAI_API_KEY",
                    },
                ),
                "api_base_url": (
                    "STRING",
                    {
                        "default": DEFAULT_API_BASE_URL,
                        "multiline": False,
                        "placeholder": "OpenAI 或兼容服务 Base URL",
                    },
                ),
                "speech_model": (SPEECH_MODEL_OPTIONS, {"default": "whisper-1"}),
                "text_model": (TEXT_MODEL_OPTIONS, {"default": "gpt-4o-mini"}),
                "language": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "可选：zh / en / ja；留空自动识别",
                    },
                ),
                "similarity_threshold": (
                    "FLOAT",
                    {"default": 0.92, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "sentence_threshold": (
                    "FLOAT",
                    {"default": 0.86, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
            },
            "optional": {
                "reference_text_file": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "可选：txt/srt/ass/vtt 文件绝对路径，或 ComfyUI/input 里的文件名",
                    },
                ),
            },
        }

    RETURN_TYPES = ("BOOLEAN", "FLOAT", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "passed",
        "overall_similarity",
        "transcript_original",
        "transcript_zh",
        "qc_report",
        "qc_json",
    )
    FUNCTION = "run"
    CATEGORY = "video/audio_qc"

    def run(
        self,
        video_path,
        reference_text,
        api_key,
        api_base_url,
        speech_model,
        text_model,
        language,
        similarity_threshold,
        sentence_threshold,
        reference_text_file="",
    ):
        video = resolve_path(video_path)
        if video is None:
            raise ValueError("video_path 不能为空。")

        reference = load_reference_text(reference_text, reference_text_file)
        if not reference:
            raise ValueError("reference_text 或 reference_text_file 至少需要提供一个。")

        reference_lines = split_reference_lines(reference)
        if not reference_lines:
            raise ValueError("参考文案需要至少包含一行有效文本。")

        client = make_openai_client(api_key, api_base_url)

        with tempfile.TemporaryDirectory(prefix="speech_text_qc_") as tmp_dir:
            audio_path = Path(tmp_dir) / "audio.mp3"
            extract_audio(video, audio_path)
            transcript, segments, detected_language = transcribe_audio_api(
                client,
                audio_path,
                speech_model=speech_model,
                language=language,
            )

        groups = group_transcript_units(reference_lines, segments)
        grouped_original_texts = [merge_group_text(group) for group in groups]
        translated_lines = translate_texts_to_chinese(client, text_model, grouped_original_texts)
        transcript_zh = "\n".join(translated_lines).strip()

        rows = build_qc_rows(
            reference_lines,
            segments,
            translated_lines,
            sentence_threshold=sentence_threshold,
        )
        overall_similarity = similarity_ratio("\n".join(reference_lines), transcript_zh)
        passed = (
            overall_similarity >= float(similarity_threshold)
            and all(row["passed"] for row in rows)
        )

        qc_payload = {
            "passed": passed,
            "overall_similarity": round(float(overall_similarity), 4),
            "similarity_threshold": float(similarity_threshold),
            "sentence_threshold": float(sentence_threshold),
            "detected_language": detected_language,
            "video_path": str(video),
            "speech_model": speech_model,
            "text_model": text_model,
            "reference_text": reference,
            "transcript_original": transcript,
            "transcript_zh": transcript_zh,
            "segments": segments,
            "rows": rows,
        }
        qc_report = build_report(
            passed=passed,
            overall_similarity=float(overall_similarity),
            sentence_threshold=sentence_threshold,
            similarity_threshold=similarity_threshold,
            detected_language=detected_language,
            video=video,
            reference=reference,
            transcript=transcript,
            transcript_zh=transcript_zh,
            segments=segments,
            rows=rows,
        )

        return (
            passed,
            float(overall_similarity),
            transcript,
            transcript_zh,
            qc_report,
            json.dumps(qc_payload, ensure_ascii=False, indent=2),
        )


class UploadedVideoPath:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": (list_input_videos(),),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    FUNCTION = "run"
    CATEGORY = "video/audio_qc"

    def run(self, video):
        path = resolve_path(video)
        if path is None:
            raise ValueError("请先把视频放到 ComfyUI/input 目录，然后刷新节点。")
        return (str(path),)


class ReferenceTextLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text_file": (list_input_text_files(),),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("reference_text",)
    FUNCTION = "run"
    CATEGORY = "video/audio_qc"

    def run(self, text_file):
        path = resolve_path(text_file)
        if path is None:
            raise ValueError("请先把 txt/srt/ass/vtt 文件放到 ComfyUI/input 目录，然后刷新节点。")
        return (read_text_file(path).strip(),)


class QCTextViewerSaver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "连接 qc_report 或 qc_json",
                    },
                ),
                "save_to_file": ("BOOLEAN", {"default": True}),
                "filename_prefix": (
                    "STRING",
                    {
                        "default": "speech_text_qc_report",
                        "multiline": False,
                    },
                ),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("saved_path", "text")
    FUNCTION = "run"
    CATEGORY = "video/audio_qc"
    OUTPUT_NODE = True

    def run(self, text, save_to_file, filename_prefix):
        content = text or ""
        saved_path = ""

        if save_to_file:
            safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename_prefix or "speech_text_qc_report")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = _output_dir() / f"{safe_prefix}_{timestamp}.txt"
            output_path.write_text(content, encoding="utf-8")
            saved_path = str(output_path)

        return {
            "ui": {
                "text": [content],
                "saved_path": [saved_path],
            },
            "result": (saved_path, content),
        }


NODE_CLASS_MAPPINGS = {
    "UploadedVideoPath": UploadedVideoPath,
    "ReferenceTextLoader": ReferenceTextLoader,
    "SpeechTextConsistencyQC": SpeechTextConsistencyQC,
    "QCTextViewerSaver": QCTextViewerSaver,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "UploadedVideoPath": "Uploaded Video Path",
    "ReferenceTextLoader": "Reference Text Loader",
    "SpeechTextConsistencyQC": "Speech/Text Consistency QC API",
    "QCTextViewerSaver": "QC Text Viewer/Saver",
}
