"""Azure OpenAI STT via the `audio/transcriptions` REST endpoint.

Reuses the same Azure OpenAI resource and key as the chat client, so a single
`AZURE_OPENAI_API_KEY` covers both transcriptions and chat completions. The
resource must contain an audio-capable deployment (for example `whisper` or
`gpt-4o-transcribe`); `AZURE_OPENAI_STT_DEPLOYMENT` is the deployment name,
not the model name.

Azure's endpoint differs from OpenAI's in two ways:
- the deployment is part of the path, not the multipart `model` field;
- authentication is the `api-key` header, not `Authorization: Bearer`.
"""

from app.services.stt.base import TranscriptCallback
from app.services.stt.openai_batch import OpenAIBatchSTT


class AzureBatchSTT(OpenAIBatchSTT):
    """Rolling-segment transcription against an Azure OpenAI deployment."""

    def __init__(
        self,
        on_transcript: TranscriptCallback,
        *,
        api_key: str,
        endpoint: str,
        deployment: str,
        api_version: str,
        segment_seconds: float = 3.0,
    ) -> None:
        base = endpoint.rstrip("/")
        url = f"{base}/openai/deployments/{deployment}/audio/transcriptions"
        super().__init__(
            on_transcript,
            url=url,
            headers={"api-key": api_key},
            provider_label="Azure OpenAI",
            model=None,  # the deployment in the URL identifies the model
            params={"api-version": api_version},
            warmup_url=base,
            segment_seconds=segment_seconds,
        )
        self._deployment = deployment
