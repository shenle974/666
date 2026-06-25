import shutil


def find_ffmpeg():
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def main():
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path is None:
        print(
            "[ComfyUI-SpeechTextQC] ffmpeg was not found. "
            "Install requirements.txt to use the bundled imageio-ffmpeg fallback, "
            "or install ffmpeg manually and add it to PATH."
        )
    else:
        print(f"[ComfyUI-SpeechTextQC] ffmpeg detected: {ffmpeg_path}")

    print(
        "[ComfyUI-SpeechTextQC] Speech recognition uses local faster-whisper. "
        "The selected model downloads on first use."
    )
    print(
        "[ComfyUI-SpeechTextQC] Translation can use node api_key, "
        "or provider env vars: OPENAI_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY."
    )


if __name__ == "__main__":
    main()
