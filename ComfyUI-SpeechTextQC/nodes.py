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

LOCAL_MODEL_OPTIONS = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "large-v3-turbo",
]

TRANSLATION_PROVIDER_OPTIONS = [
    "qwen_dashscope",
    "deepseek",
    "openai",
    "custom_openai_compatible",
    "none",
]

TRANSLATION_PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen_dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "env_key": "DASHSCOPE_API_KEY",
    },
}

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


def _faster_whisper_model_dir():
    if folder_paths is not None:
        models_dir = getattr(folder_paths, "models_dir", None)
        if models_dir:
            return Path(models_dir) / "faster-whisper"
    return Path.cwd() / "models" / "faster-whisper"


def looks_like_video_path(value):
    if not value:
        return False
    try:
        suffix = Path(str(value)).suffix.lower()
    except Exception:
        return False
    return suffix in VIDEO_EXTENSIONS


def resolve_video_object_path(value, depth=0, seen=None):
    if value is None or depth > 5:
        return None
    if seen is None:
        seen = set()

    value_id = id(value)
    if value_id in seen:
        return None
    seen.add(value_id)

    if isinstance(value, (str, os.PathLike)):
        text = str(value)
        if looks_like_video_path(text):
            return resolve_path(text)
        return None

    if isinstance(value, dict):
        preferred_keys = (
            "path",
            "video_path",
            "file_path",
            "filepath",
            "filename",
            "file",
            "name",
        )
        for key in preferred_keys:
            if key in value:
                found = resolve_video_object_path(value.get(key), depth + 1, seen)
                if found is not None:
                    return found
        for item in value.values():
            found = resolve_video_object_path(item, depth + 1, seen)
            if found is not None:
                return found
        return None

    if isinstance(value, (list, tuple)):
        for item in value:
            found = resolve_video_object_path(item, depth + 1, seen)
            if found is not None:
                return found
        return None

    for attr in (
        "path",
        "video_path",
        "file_path",
        "filepath",
        "filename",
        "file",
        "name",
    ):
        try:
            attr_value = getattr(value, attr)
        except Exception:
            continue
        found = resolve_video_object_path(attr_value, depth + 1, seen)
        if found is not None:
            return found

    for method_name in ("get_path", "get_file_path", "get_filename"):
        try:
            method = getattr(value, method_name)
        except Exception:
            continue
        if callable(method):
            try:
                found = resolve_video_object_path(method(), depth + 1, seen)
            except Exception:
                continue
            if found is not None:
                return found

    try:
        value_dict = vars(value)
    except Exception:
        value_dict = None
    if value_dict:
        found = resolve_video_object_path(value_dict, depth + 1, seen)
        if found is not None:
            return found

    return None


def resolve_video_input(video, video_path):
    from_video = resolve_video_object_path(video)
    if from_video is not None:
        return from_video

    from_path = resolve_path(video_path) if (video_path or "").strip() else None
    if from_path is not None:
        return from_path

    raise ValueError(
        "请把“加载视频”的视频输出接到本节点的 video 输入；"
        "如果不用接线，也可以在可选 video_path 里填写视频文件名或路径。"
    )


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


def resolve_translation_settings(provider, api_key, api_base_url, text_model):
    selected_provider = (provider or "qwen_dashscope").strip() or "qwen_dashscope"
    if selected_provider == "none":
        return selected_provider, "", "", ""

    defaults = TRANSLATION_PROVIDER_DEFAULTS.get(selected_provider, {})
    env_key = defaults.get("env_key", "OPENAI_API_KEY")
    key = (api_key or os.environ.get(env_key) or os.environ.get("OPENAI_API_KEY") or "").strip()
    base_url = (api_base_url or "").strip() or defaults.get("base_url", "")
    model = (text_model or "").strip() or defaults.get("model", "")

    if selected_provider == "custom_openai_compatible" and not base_url:
        raise RuntimeError("custom_openai_compatible 需要填写 api_base_url。")
    if not model:
        raise RuntimeError("请填写 text_model，或选择带默认模型的 translation_provider。")

    return selected_provider, key, base_url, model


def make_openai_client(api_key, api_base_url, provider="openai", text_model=""):
    _, key, base_url, _ = resolve_translation_settings(
        provider,
        api_key,
        api_base_url,
        text_model,
    )
    if not key:
        raise RuntimeError(
            "缺少翻译 API key。请在节点 api_key 填写，或设置对应环境变量："
            "OPENAI_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY。"
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "缺少 openai 依赖。请在 ComfyUI 的 Python 环境里执行 "
            "`pip install -r custom_nodes/ComfyUI-SpeechTextQC/requirements.txt`。"
        ) from exc

    kwargs = {"api_key": key}
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


def resolve_local_device(device):
    selected = (device or "auto").strip().lower()
    if selected != "auto":
        return selected

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def resolve_compute_type(compute_type, device):
    selected = (compute_type or "auto").strip().lower()
    if selected != "auto":
        return selected
    return "float16" if device == "cuda" else "int8"


def transcribe_audio_local(audio_path, local_model_size, language, device, compute_type):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "缺少 faster-whisper。请在 ComfyUI 的 Python 环境里执行 "
            "`python -m pip install -r custom_nodes/666/ComfyUI-SpeechTextQC/requirements.txt`。"
        ) from exc

    model_size = (local_model_size or "small").strip()
    lang = (language or "").strip() or None
    actual_device = resolve_local_device(device)
    actual_compute_type = resolve_compute_type(compute_type, actual_device)
    model_dir = _faster_whisper_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    try:
        model = WhisperModel(
            model_size,
            device=actual_device,
            compute_type=actual_compute_type,
            download_root=str(model_dir),
        )
    except Exception as exc:
        raise RuntimeError(
            "本地 faster-whisper 模型加载失败。首次运行需要联网下载模型；"
            f"也可以手动把模型放到 {model_dir}。原始错误: {exc}"
        ) from exc

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=lang,
        vad_filter=True,
        beam_size=5,
    )

    segments = []
    for segment in segments_iter:
        text = segment.text.strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": text,
            }
        )

    transcript = "".join(item["text"] for item in segments)
    detected_language = getattr(info, "language", None) or lang or "auto"
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
    model = (text_model or "").strip()
    messages = [
        {
            "role": "system",
            "content": (
                "你是翻译 API。只返回 JSON，不要返回 Markdown。"
                "把输入文本逐条翻译为简体中文。"
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=messages,
        )
        parsed = extract_json_object(response.choices[0].message.content)
    except Exception as json_error:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            parsed = extract_json_object(response.choices[0].message.content)
        except Exception as chat_error:
            raise RuntimeError(
                "翻译 API 调用失败。JSON 模式错误: "
                f"{json_error}; 普通 Chat Completions 错误: {chat_error}"
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


def translate_texts_to_chinese_safe(provider, api_key, api_base_url, text_model, texts):
    selected_provider = (provider or "qwen_dashscope").strip() or "qwen_dashscope"
    if selected_provider == "none":
        return ["" for _ in texts], "未翻译：translation_provider 设置为 none。"

    try:
        resolved_provider, key, resolved_base_url, resolved_model = resolve_translation_settings(
            selected_provider,
            api_key,
            api_base_url,
            text_model,
        )
    except Exception as exc:
        message = f"翻译配置错误：{exc}"
        return [message if (text or "").strip() else "" for text in texts], message
    if not key:
        defaults = TRANSLATION_PROVIDER_DEFAULTS.get(resolved_provider, {})
        env_key = defaults.get("env_key", "OPENAI_API_KEY")
        message = (
            "未翻译：缺少翻译 API key。语音识别已在本地完成，"
            f"请填写 api_key 或设置 {env_key} 后重试翻译。"
        )
        return [message if (text or "").strip() else "" for text in texts], message

    try:
        client = make_openai_client(
            key,
            resolved_base_url,
            provider=resolved_provider,
            text_model=resolved_model,
        )
        return translate_texts_to_chinese(client, resolved_model, texts), ""
    except Exception as exc:
        message = f"翻译失败：{exc}"
        return [message if (text or "").strip() else "" for text in texts], message


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


def build_qc_rows(reference_lines, transcript_segments, translated_lines):
    groups = group_transcript_units(reference_lines, transcript_segments)
    rows = []
    for index, reference in enumerate(reference_lines):
        group = groups[index] if index < len(groups) else []
        original = merge_group_text(group)
        translated = translated_lines[index] if index < len(translated_lines) else ""
        start, end = group_start_end(group)
        rows.append(
            {
                "index": index + 1,
                "start": start,
                "end": end,
                "reference": reference,
                "transcript_original": original,
                "transcript_zh": translated,
            }
        )
    return rows


def build_report(
    detected_language,
    local_model_size,
    device,
    compute_type,
    video,
    reference,
    transcript,
    transcript_zh,
    segments,
    rows,
    translation_error="",
):
    report_lines = [
        "视频语音与参考文案对照",
        f"识别语言: {detected_language}",
        f"本地识别模型: {local_model_size}",
        f"运行设备: {device} / {compute_type}",
        f"视频: {video}",
    ]
    if translation_error:
        report_lines.append(f"翻译提示: {translation_error}")

    report_lines.extend(
        [
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
    )

    for row in rows:
        time_range = ""
        if row["start"] is not None and row["end"] is not None:
            time_range = f" [{row['start']:.2f}s - {row['end']:.2f}s]"
        report_lines.extend(
            [
                "",
                f"{row['index']}.{time_range}",
                f"参考: {row['reference']}",
                f"识别原文: {row['transcript_original'] or '(空)'}",
                f"中文译文: {row['transcript_zh'] or '(空)'}",
            ]
        )
    return "\n".join(report_lines)


class SpeechTextConsistencyQC:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
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
                        "placeholder": "可选；仅用于中文翻译，可填 OpenAI/DeepSeek/百炼 Key",
                    },
                ),
                "translation_provider": (
                    TRANSLATION_PROVIDER_OPTIONS,
                    {"default": "qwen_dashscope"},
                ),
                "api_base_url": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "可选；留空使用服务商默认，custom 时必填",
                    },
                ),
                "local_model_size": (LOCAL_MODEL_OPTIONS, {"default": "small"}),
                "device": (["auto", "cpu", "cuda"], {"default": "auto"}),
                "compute_type": (
                    ["auto", "int8", "float16", "float32"],
                    {"default": "auto"},
                ),
                "text_model": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "可选；如 qwen-plus / deepseek-v4-flash / gpt-4o-mini",
                    },
                ),
                "language": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "可选：th / en；留空自动识别",
                    },
                ),
            },
            "optional": {
                "video_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "备用：视频文件名或路径；通常直接连接 video 即可",
                    },
                ),
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
        video,
        reference_text,
        api_key,
        translation_provider,
        api_base_url,
        local_model_size,
        device,
        compute_type,
        text_model,
        language,
        video_path="",
        reference_text_file="",
    ):
        video_file = resolve_video_input(video, video_path)

        reference = load_reference_text(reference_text, reference_text_file)
        if not reference:
            raise ValueError("reference_text 或 reference_text_file 至少需要提供一个。")

        reference_lines = split_reference_lines(reference)
        if not reference_lines:
            raise ValueError("参考文案需要至少包含一行有效文本。")

        with tempfile.TemporaryDirectory(prefix="speech_text_qc_") as tmp_dir:
            audio_path = Path(tmp_dir) / "audio.mp3"
            extract_audio(video_file, audio_path)
            actual_device = resolve_local_device(device)
            actual_compute_type = resolve_compute_type(compute_type, actual_device)
            transcript, segments, detected_language = transcribe_audio_local(
                audio_path,
                local_model_size=local_model_size,
                language=language,
                device=actual_device,
                compute_type=actual_compute_type,
            )

        groups = group_transcript_units(reference_lines, segments)
        grouped_original_texts = [merge_group_text(group) for group in groups]
        translated_lines, translation_error = translate_texts_to_chinese_safe(
            translation_provider,
            api_key,
            api_base_url,
            text_model,
            grouped_original_texts,
        )
        transcript_zh = "\n".join(translated_lines).strip()

        rows = build_qc_rows(
            reference_lines,
            segments,
            translated_lines,
        )
        overall_similarity = 1.0
        passed = True

        qc_payload = {
            "passed": passed,
            "overall_similarity": round(float(overall_similarity), 4),
            "mode": "comparison_only",
            "detected_language": detected_language,
            "video_path": str(video_file),
            "local_model_size": local_model_size,
            "device": actual_device,
            "compute_type": actual_compute_type,
            "translation_provider": translation_provider,
            "api_base_url": api_base_url,
            "text_model": text_model,
            "translation_error": translation_error,
            "reference_text": reference,
            "transcript_original": transcript,
            "transcript_zh": transcript_zh,
            "segments": segments,
            "rows": rows,
        }
        qc_report = build_report(
            detected_language=detected_language,
            local_model_size=local_model_size,
            device=actual_device,
            compute_type=actual_compute_type,
            video=video_file,
            reference=reference,
            transcript=transcript,
            transcript_zh=transcript_zh,
            segments=segments,
            rows=rows,
            translation_error=translation_error,
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
    "SpeechTextConsistencyQC": "Speech/Text Consistency QC Local",
    "QCTextViewerSaver": "QC Text Viewer/Saver",
}
