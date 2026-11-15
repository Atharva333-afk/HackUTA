import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types
from livekit import rtc
from livekit.agents import AgentServer, JobContext, AutoSubscribe, cli

from shared_api import send_translation

load_dotenv(Path(__file__).with_name(".env"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asl-agent")

server = AgentServer()

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CLIP_SECONDS = 5
TARGET_FPS = 8

INTENTS = {
    "statement",
    "question",
    "command",
    "greeting",
    "other",
}

PROMPT = """
Translate the American Sign Language signing in this silent video into
natural English.

Consider both hands, their movement, body position, and facial cues.

Do not describe the video.
Do not answer the signer.
Do not invent missing information.

If the signing is unclear, incomplete, or cannot be translated reliably,
return empty original_text and clean_text and intent "other".

Return only JSON with:
source: "asl"
original_text: literal English translation
clean_text: natural English with the same meaning
intent: one of statement, question, command, greeting, other
"""


def translate_video(client, video_bytes):
    response = client.models.generate_content(
        model=MODEL,
        contents=types.Content(
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        data=video_bytes,
                        mime_type="video/mp4",
                    ),
                    video_metadata=types.VideoMetadata(fps=TARGET_FPS),
                ),
                types.Part(text=PROMPT),
            ]
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )

    result = json.loads(response.text or "{}")

    original = result.get("original_text", "")
    clean = result.get("clean_text", "")
    intent = result.get("intent", "other")

    if intent not in INTENTS:
        intent = "other"

    return {
        "source": "asl",
        "original_text": str(original).strip(),
        "clean_text": str(clean).strip(),
        "intent": intent,
    }


def encode_video(frames):
    if not frames:
        raise ValueError("No frames received")

    height, width = frames[0].shape[:2]

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "phone_signing.mp4"

        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            TARGET_FPS,
            (width, height),
        )

        if not writer.isOpened():
            raise RuntimeError("Could not open MP4 encoder")

        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()

        return path.read_bytes()


async def process_clip(client, frames):
    try:
        log.info("Sending %s frames to Gemini", len(frames))

        video_bytes = await asyncio.to_thread(
            encode_video,
            frames,
        )

        result = await asyncio.to_thread(
            translate_video,
            client,
            video_bytes,
        )

        if not result["clean_text"]:
            log.info("Gemini could not confidently translate this clip")
            return

        log.info("ASL translation: %s", result["clean_text"])

        sent = await asyncio.to_thread(
            send_translation,
            result,
        )

        log.info("Sent ASL translation to shared backend: %s", sent)

    except Exception:
        log.exception("ASL translation failed")


async def read_video(track, client):
    stream = rtc.VideoStream(
        track,
        format=rtc.VideoBufferType.RGB24,
        capacity=1,
    )

    frames = []
    clip_started = time.monotonic()
    last_frame_time = 0

    try:
        async for event in stream:
            now = time.monotonic()

            # Limit Gemini input to about 8 FPS
            if now - last_frame_time < 1 / TARGET_FPS:
                continue

            last_frame_time = now

            frame = event.frame

            rgb = np.frombuffer(
                frame.data,
                dtype=np.uint8,
            ).reshape(
                frame.height,
                frame.width,
                3,
            )

            # OpenCV VideoWriter expects BGR
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            frames.append(bgr.copy())

            if now - clip_started >= CLIP_SECONDS:
                clip = frames
                frames = []
                clip_started = now

                asyncio.create_task(
                    process_clip(client, clip)
                )

    finally:
        await stream.aclose()


@server.rtc_session(agent_name="asl-agent")
async def entrypoint(ctx: JobContext):
    key = os.getenv("GEMINI_API_KEY")

    if not key:
        raise RuntimeError("GEMINI_API_KEY missing from .env")

    client = genai.Client(api_key=key)

    tasks = {}

    @ctx.room.on("track_subscribed")
    def on_track(track, publication, participant):
        log.info(
            "Track %s from %s",
            publication.name,
            participant.identity,
        )

        if track.kind == rtc.TrackKind.KIND_VIDEO:
            log.info("Phone video connected")

            tasks[publication.sid] = asyncio.create_task(
                read_video(track, client)
            )

    @ctx.room.on("track_unsubscribed")
    def on_unsubscribe(track, publication, participant):
        task = tasks.pop(publication.sid, None)

        if task:
            task.cancel()

    async def cleanup():
        for task in tasks.values():
            task.cancel()

        await asyncio.gather(
            *tasks.values(),
            return_exceptions=True,
        )

        client.close()

    ctx.add_shutdown_callback(cleanup)

    await ctx.connect(
        auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL
    )

    log.info(
        "Connected to %s; waiting for phone video",
        ctx.room.name,
    )


if __name__ == "__main__":
    cli.run_app(server)
