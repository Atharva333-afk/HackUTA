"""Caption screen the signer sees on FaceTime.

The ASL agent publishes this as its video track. It shows the latest English
reply (from the hearing person, via the shared server) in large text, plus a
smaller line confirming what the signer's last message was translated as.
"""
import asyncio
import logging
import time

import cv2
import numpy as np
from livekit import rtc

from shared_api import receive_english

log = logging.getLogger("asl-agent.captions")

# Portrait, to fill a phone screen on FaceTime.
WIDTH = 720
HEIGHT = 1280
FPS = 5
POLL_SECONDS = 1.0

BACKGROUND = (18, 24, 38)      # deep navy (RGB)
ACCENT = (240, 190, 70)        # warm gold
TEXT = (245, 245, 245)
MUTED = (150, 160, 180)

FONT = cv2.FONT_HERSHEY_DUPLEX


class CaptionBoard:
    """Holds what the signer should see and draws it as RGBA frames."""

    def __init__(self):
        self.reply = ""            # latest English reply for the signer
        self.reply_at = 0.0
        self.you_signed = ""       # how the signer's last clip was translated

    def set_reply(self, text):
        self.reply = text.strip()
        self.reply_at = time.monotonic()

    def set_you_signed(self, text):
        self.you_signed = text.strip()

    def render(self):
        img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        img[:] = BACKGROUND

        _draw_text(img, "THEY SAID", 60, 110, 0.9, ACCENT, 2)
        cv2.line(img, (60, 135), (WIDTH - 60, 135), ACCENT, 2)

        if self.reply:
            # Larger text for short replies, smaller for long ones.
            scale = 2.0 if len(self.reply) <= 60 else 1.4
            line_height = int(cv2.getTextSize("Ag", FONT, scale, 3)[0][1] * 1.9)
            y = 160 + line_height
            for line in _wrap_to_width(self.reply, WIDTH - 120, scale, 3)[:9]:
                _draw_text(img, line, 60, y, scale, TEXT, 3)
                y += line_height

            # Highlight new replies for 3 seconds.
            if time.monotonic() - self.reply_at < 3:
                cv2.rectangle(img, (40, 160), (WIDTH - 40, y), ACCENT, 3)
        else:
            _draw_text(img, "Waiting for a reply...", 60, 230, 1.1, MUTED, 2)

        if self.you_signed:
            y = HEIGHT - 260
            cv2.line(img, (60, y - 50), (WIDTH - 60, y - 50), MUTED, 1)
            _draw_text(img, "YOU SIGNED", 60, y, 0.8, MUTED, 2)
            y += 55
            for line in _wrap_to_width(self.you_signed, WIDTH - 120, 1.0, 2)[:3]:
                _draw_text(img, line, 60, y, 1.0, TEXT, 2)
                y += 50

        return cv2.cvtColor(img, cv2.COLOR_RGB2RGBA)


def _wrap_to_width(text, max_width, scale, thickness):
    """Split text into lines that fit max_width pixels at this font size."""
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        width = cv2.getTextSize(candidate, FONT, scale, thickness)[0][0]
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text(img, text, x, y, scale, color, thickness):
    # Hershey fonts are ASCII only; replace anything else so nothing crashes.
    safe = text.encode("ascii", "replace").decode("ascii")
    cv2.putText(img, safe, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


async def publish_captions(room, board):
    """Publish the caption screen as this agent's video track."""
    source = rtc.VideoSource(WIDTH, HEIGHT)
    track = rtc.LocalVideoTrack.create_video_track("captions", source)
    options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
    await room.local_participant.publish_track(track, options)
    log.info("Publishing caption screen")

    while True:
        rgba = board.render()
        frame = rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, rgba.tobytes())
        source.capture_frame(frame)
        await asyncio.sleep(1 / FPS)


async def poll_english_replies(board):
    """Show each new English reply from the shared server on the caption screen."""
    last_id = None  # skip replies sent before this call started
    while True:
        try:
            messages = await asyncio.to_thread(
                receive_english, 0 if last_id is None else last_id
            )
            if last_id is None:
                last_id = messages[-1]["id"] if messages else 0
            else:
                for message in messages:
                    last_id = message["id"]
                    text = message.get("clean_text", "").strip()
                    if text:
                        log.info("English reply: %s", text)
                        board.set_reply(text)
        except Exception as error:
            log.warning("Could not read English replies: %s", error)
        await asyncio.sleep(POLL_SECONDS)
