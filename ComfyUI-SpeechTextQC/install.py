import shutil


def main():
    if shutil.which("ffmpeg") is None:
        print(
            "[ComfyUI-SpeechTextQC] ffmpeg was not found. "
            "Install ffmpeg before running the speech QC node."
        )
    else:
        print("[ComfyUI-SpeechTextQC] ffmpeg detected.")

    print(
        "[ComfyUI-SpeechTextQC] Set OPENAI_API_KEY or fill api_key in the node "
        "before using API transcription."
    )


if __name__ == "__main__":
    main()
