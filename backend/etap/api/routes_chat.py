"""Public product assistant for the AptoriQ landing page.

The browser never sees provider credentials. The endpoint is deliberately narrow: it
answers questions about AptoriQ and supported exam workflows, not arbitrary prompts.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, status

from ..ingest.providers import ProviderError, build_provider
from .schemas import ChatIn, ChatOut

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_MESSAGES = 10
MAX_MESSAGE_CHARS = 2_000

PRODUCT_CONTEXT = """You are Ask AptoriQ, the concise product guide on the public
AptoriQ website. AptoriQ converts an English question-paper PDF plus answer key into
a realistic computer-based test. Teachers verify extracted questions, publish papers and
assign them to cohorts. Students attempt papers with server-authoritative timing, palette
states, review controls, offline buffering and refresh recovery.

Working today: digital-PDF ingestion, scoring, student exam runtime, student results,
teacher paper publishing and cohort assignment. Still planned: the correction editor,
deterministic temperament metric engine, archetypes, AI narrative reports, teacher cohort
analytics, validated vision extraction for real scanned papers, and an authenticated
personal tutor using student profiles, concept mastery and relevant syllabus context.

Supported profiles: NEET, NEET PG, JEE Main, JEE Advanced, GATE, UPSC Prelims, CAT and
custom. Do not describe behavioural archetypes as personality diagnoses. Do not claim a
planned feature is available. If asked about unrelated subjects, politely steer back to
AptoriQ, exam attempts, or using the site. Keep answers under 120 words and practical.
Return strict JSON only in this shape: {"reply": "your answer"}.
"""


@router.post("", response_model=ChatOut)
def chat(payload: ChatIn) -> ChatOut:
    if not payload.messages:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Send at least one message.")
    if len(payload.messages) > MAX_MESSAGES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Keep the conversation to the latest {MAX_MESSAGES} messages.",
        )

    transcript: list[str] = []
    for message in payload.messages:
        if message.role not in {"user", "assistant"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Message role must be user or assistant.",
            )
        content = message.content.strip()
        if not content or len(content) > MAX_MESSAGE_CHARS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Each message must contain 1–{MAX_MESSAGE_CHARS} characters.",
            )
        transcript.append(f"{message.role.upper()}: {content}")

    try:
        provider = build_provider()
        result = provider.complete(PRODUCT_CONTEXT + "\nConversation:\n" + "\n".join(transcript))
    except ProviderError as error:
        message = str(error)
        if "No vision provider configured" in message or "No API key" in message:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Ask AptoriQ needs an LLM API key on the server. Configure OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY.",
            ) from error
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The AI provider could not answer right now. Please try again shortly.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The AI provider could not answer right now. Please try again shortly.",
        ) from error

    reply = result.get("reply") if isinstance(result, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"The AI provider returned an unexpected response: {json.dumps(result)[:120]}",
        )
    return ChatOut(reply=reply.strip())
