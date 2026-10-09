# Third-party models and libraries

meeting-scribe code is MIT-licensed. At runtime it downloads or loads the following:

| Component | Use | License | Source |
|---|---|---|---|
| Whisper large-v3-turbo (CTranslate2 conversion) | final transcript (GPU) | MIT | [openai/whisper](https://github.com/openai/whisper), [mobiuslabsgmbh/faster-whisper-large-v3-turbo](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo) |
| faster-whisper / CTranslate2 | Whisper inference | MIT | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| Silero VAD v6 (bundled in faster-whisper) | speech segmentation | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| sherpa-onnx | streaming partial recognizer | Apache-2.0 | [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) |
| sherpa-onnx-streaming-zipformer-korean-2024-06-16 | partial model (option `zipformer-ko`) | see upstream release | [k2-fsa/sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) |
| icefall-asr-ko-streaming-zipformer-174m | partial model (options `kspon174m-*`) | Apache-2.0 | [kangkyu/icefall-asr-ko-streaming-zipformer-174m](https://huggingface.co/kangkyu/icefall-asr-ko-streaming-zipformer-174m) |
| NVIDIA cuBLAS / cuDNN (pip wheels) | CUDA runtime | NVIDIA EULA | [PyPI nvidia-*-cu12](https://pypi.org/project/nvidia-cudnn-cu12/) |
| PyAudioWPatch | WASAPI loopback capture | MIT | [s0d3s/PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) |

Models are not redistributed in this repository; they are fetched from the sources above on first run.
Test fixtures in `tests/fixtures` are synthesized with the Windows "Microsoft Heami" TTS voice from
sentences written for this project (`scripts/make_fixtures.py`).
