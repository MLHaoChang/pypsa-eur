# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Authenticated, temporary audio transcription for every chat provider."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from db.models import User
from deps import require_user
from harness.providers import transcription
from services import dictation
from services.upload_guard import read_capped

router = APIRouter()


@router.get("/config")
def config(_user: User = Depends(require_user)):
    return {
        "available": bool(transcription.credential()),
        "model": transcription.MODEL,
        "languages": list(dictation.LANGUAGES),
        "max_bytes": dictation.MAX_AUDIO_BYTES,
        "max_recording_seconds": dictation.MAX_RECORDING_SECONDS,
    }


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    glossary: str = Form(""),
    user: User = Depends(require_user),
):
    try:
        hint, terms = dictation.validate_options(language, glossary)
        with dictation.reserve_transcription(str(user.id)):
            audio = await read_capped(file, dictation.MAX_AUDIO_BYTES)
            filename, mime = dictation.audio_format(audio, file.content_type)
            try:
                text = await transcription.transcribe_audio(
                    audio, filename, mime, hint, terms
                )
            except transcription.TranscriptionError as exc:
                raise HTTPException(
                    exc.status, detail={"code": exc.kind, "message": exc.message}
                ) from exc
            return {"text": text, "model": transcription.MODEL, "language": hint}
    finally:
        await file.close()
