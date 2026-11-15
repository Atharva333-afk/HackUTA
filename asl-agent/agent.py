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
from captions import CaptionBoard, publish_captions, poll_english_replies

load_dotenv(Path(__file__).with_name(".env"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asl-agent")

server = AgentServer()

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CLIP_SECONDS = 5

# Skip clips with little movement so Gemini's quota is only spent on signing.
# Average pixel change between frames (0-255). Raise to skip more, lower to send more.
MIN_MOTION = float(os.getenv("MIN_MOTION", "3"))

# Free-tier Gemini keys allow few requests per minute, so space them out.
GEMINI_MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL", "13"))
_next_gemini_at = 0.0

# Frames are shrunk to this width before storing, to save memory and upload size.
MAX_FRAME_WIDTH = 640
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
{vocab}
Return only JSON with:
source: "asl"
original_text: literal English translation
clean_text: natural English with the same meaning
intent: one of statement, question, command, greeting, other
"""

# Signs the demo expects. Telling Gemini what to expect makes it far more
# accurate on a small vocabulary. Override with SIGN_VOCAB="HELLO,THANK YOU,...".
SIGN_VOCAB = [
    w.strip()
    for w in os.getenv("SIGN_VOCAB", "YES,NO,PLEASE,THANK YOU,SORRY,HELP").split(",")
    if w.strip()
]
PROMPT = PROMPT.format(
    vocab=(
        "\nThe signer will most likely use one of these signs: "
        + ", ".join(SIGN_VOCAB)
        + ". If the signing matches one of them, use that meaning. "
        "A clip usually contains a single sign.\n"
    )
    if SIGN_VOCAB
    else ""
)


# If a model is overloaded (503) or unavailable (404), try these next, in order.
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3-flash-preview,"
        "gemini-3.5-flash-lite,gemini-3.1-flash-lite",
    ).split(",")
    if m.strip()
]


def translate_video(client, video_bytes):
    last_error = None
    for model in [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]:
        try:
            response = _generate(client, model, video_bytes)
            break
        except Exception as error:
            text = str(error)
            # Each model has its own quota, so on overload, missing model or
            # quota errors, try the next model before giving up.
            if any(code in text for code in ("503", "UNAVAILABLE", "404", "NOT_FOUND", "429", "RESOURCE_EXHAUSTED")):
                log.warning("Gemini model %s unavailable (%s), trying next", model, text[:40])
                if last_error is None or "404" in str(last_error) or "NOT_FOUND" in str(last_error):
                    last_error = error
                continue
            raise
    else:
        raise last_error

    return _parse_result(response)


def _generate(client, model, video_bytes):
    return client.models.generate_content(
        model=model,
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



def _parse_result(response):
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


def clip_motion(frames):
    """Average change between consecutive frames, on small grayscale copies."""
    if len(frames) < 2:
        return 0.0
    small = [
        cv2.cvtColor(cv2.resize(f, (160, 120)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        for f in frames
    ]
    diffs = [np.abs(b - a).mean() for a, b in zip(small, small[1:])]
    return float(np.mean(diffs))


async def process_clip(client, frames, board, check_motion=True):
    global _next_gemini_at
    try:
        motion = clip_motion(frames)
        if check_motion and motion < MIN_MOTION:
            log.info("Skipping still clip (motion %.1f < %.1f)", motion, MIN_MOTION)
            return

        now = time.monotonic()
        if now < _next_gemini_at:
            log.info("Waiting %.0fs for Gemini rate limit; skipping clip", _next_gemini_at - now)
            return
        _next_gemini_at = now + GEMINI_MIN_INTERVAL

        log.info("Sending %s frames to Gemini (motion %.1f)", len(frames), motion)

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

        # Show the signer how their signing was understood.
        board.set_you_signed(result["clean_text"])

        sent = await asyncio.to_thread(
            send_translation,
            result,
        )

        log.info("Sent ASL translation to shared backend: %s", sent)

    except Exception as error:
        if "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error):
            # Google asked us to slow down: pause before the next request.
            _next_gemini_at = time.monotonic() + 30
            log.warning("Gemini rate limit hit; pausing 30s. %s", str(error)[:160])
        else:
            log.exception("ASL translation failed")
    finally:
        # Back to "sign now", showing any Gemini cooldown as a countdown.
        board.set_status("ready", wait_until=_next_gemini_at)


# Signing detection: a clip starts when the hands start moving and ends after
# STOP_STILL_SECONDS of stillness, so a sign is never cut in half.
STOP_STILL_SECONDS = 1.0
PRE_ROLL_FRAMES = 4          # keep ~0.5 s from just before movement started
MIN_CLIP_SECONDS = 0.8
MAX_CLIP_SECONDS = 6.0


def _small_gray(frame):
    return cv2.cvtColor(cv2.resize(frame, (160, 120)), cv2.COLOR_BGR2GRAY).astype(np.int16)


async def read_video(track, client, board):
    stream = rtc.VideoStream(
        track,
        format=rtc.VideoBufferType.RGB24,
        capacity=1,
    )

    recent = []          # rolling pre-roll buffer while idle
    clip = []            # frames of the sign being recorded
    recording = False
    clip_started = 0.0
    still_since = None
    prev_small = None
    smooth = 0.0
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

            height, width = bgr.shape[:2]
            if width > MAX_FRAME_WIDTH:
                scale = MAX_FRAME_WIDTH / width
                bgr = cv2.resize(bgr, (MAX_FRAME_WIDTH, int(height * scale)))
            bgr = bgr.copy()

            small = _small_gray(bgr)
            motion = 0.0 if prev_small is None else float(np.abs(small - prev_small).mean())
            prev_small = small
            smooth = 0.6 * smooth + 0.4 * motion  # damp camera noise

            if not recording:
                recent.append(bgr)
                recent = recent[-PRE_ROLL_FRAMES:]
                if smooth >= MIN_MOTION:
                    recording = True
                    clip = list(recent)
                    clip_started = now
                    still_since = None
                    log.info("Signing started (motion %.1f)", smooth)
                    board.set_status("signing")
                continue

            clip.append(bgr)
            if smooth < MIN_MOTION:
                still_since = still_since or now
            else:
                still_since = None

            ended = still_since is not None and now - still_since >= STOP_STILL_SECONDS
            too_long = now - clip_started >= MAX_CLIP_SECONDS
            if ended or too_long:
                length = now - clip_started
                recording = False
                recent = []
                if length >= MIN_CLIP_SECONDS:
                    log.info("Sign finished: %.1fs clip", length)
                    board.set_status("translating")
                    asyncio.create_task(
                        process_clip(client, clip, board, check_motion=False)
                    )
                else:
                    log.info("Ignoring very short movement (%.1fs)", length)
                    board.set_status("ready")
                clip = []

    finally:
        await stream.aclose()


# Set ASL_AGENT_NAME to run under a different name (e.g. so dispatches reach only this machine).
@server.rtc_session(agent_name=os.getenv("ASL_AGENT_NAME", "asl-agent"))
async def entrypoint(ctx: JobContext):
    key = os.getenv("GEMINI_API_KEY")

    if not key:
        raise RuntimeError("GEMINI_API_KEY missing from .env")

    client = genai.Client(api_key=key)

    tasks = {}
    board = CaptionBoard()

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
                read_video(track, client, board)
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

    # Voice -> sign side: the signer sees English replies as captions on FaceTime.
    tasks["captions"] = asyncio.create_task(
        publish_captions(ctx.room, board)
    )
    tasks["english-replies"] = asyncio.create_task(
        poll_english_replies(board)
    )

    log.info(
        "Connected to %s; waiting for phone video",
        ctx.room.name,
    )


if __name__ == "__main__":
    cli.run_app(server)
