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
        "[ComfyUI-SpeechTextQC] Set OPENAI_API_KEY or fill api_key in the node "
        "before using API transcription."
    )


if __name__ == "__main__":
    main()
