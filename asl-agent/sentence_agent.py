"""Simple ASL video -> English captions prototype. Run: python sentence_agent.py"""
import json
import os
import tempfile
import time
from threading import Thread, Event, Lock
from shared_api import send_translation, receive_english
from pathlib import Path

import cv2
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).with_name('.env'))
MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash')
MAX_SECONDS = 8
INTENTS = {'statement', 'question', 'command', 'greeting', 'other'}

PROMPT = """
Translate the American Sign Language signing in this silent video into
natural English. Consider both hands, their movement, body and facial cues.
Do not describe the video, answer the signer, or invent missing information.
If the signing is unclear, incomplete, or you cannot translate it reliably,
return empty original_text and clean_text and intent 'other'.
Return only JSON with source 'asl', original_text (your literal English
translation), clean_text (natural English with the same meaning), and intent
(one of statement, question, command, greeting, other).
"""


def translate_video(client, video_bytes):
    response = client.models.generate_content(
        model=MODEL,
        contents=types.Content(parts=[
            types.Part(
                inline_data=types.Blob(data=video_bytes, mime_type='video/mp4'),
                video_metadata=types.VideoMetadata(fps=8),
            ),
            types.Part(text=PROMPT),
        ]),
        config=types.GenerateContentConfig(response_mime_type='application/json'),
    )
    result = json.loads(response.text or '{}')
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object from Gemini')
    original = result.get('original_text', '')
    clean = result.get('clean_text', '')
    if not isinstance(original, str) or not isinstance(clean, str):
        raise ValueError('Invalid translation fields from Gemini')
    intent = result.get('intent', 'other')
    return {'source': 'asl', 'original_text': original.strip(),
            'clean_text': clean.strip(),
            'intent': intent if isinstance(intent, str) and intent in INTENTS else 'other'}


def encode_video(frames, elapsed):
    # A temporary MP4 is removed automatically after it is read.
    height, width = frames[0].shape[:2]
    fps = max(1.0, min(60.0, len(frames) / max(elapsed, 0.1)))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'signing.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'),
                                 fps, (width, height))
        if not writer.isOpened():
            writer.release()
            raise RuntimeError('Could not open MP4 video encoder')
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()
        data = path.read_bytes()
        if len(data) > 18_000_000:
            raise ValueError('Video is too large; record a shorter clip')
        return data


def show(frame, status, caption):
    # Keep the original video unchanged; captions are drawn on a separate copy.
    canvas = frame.copy()
    h, w = canvas.shape[:2]
    cv2.rectangle(canvas, (0, 0), (w, 55), (0, 0, 0), -1)
    cv2.putText(canvas, status, (10, 22), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, 'SPACE: start/stop | Q: quit', (10, 44),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    if caption:
        # OpenCV's basic font is ASCII; keep the original Unicode in JSON.
        text = caption.encode('ascii', 'replace').decode('ascii')
        lines, line = [], ''
        for word in text.split():
            candidate = (line + ' ' + word).strip()
            if line and cv2.getTextSize(candidate, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)[0][0] > w - 20:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        lines = lines[:max(1, (h - 75) // 28)]
        top = h - 28 * len(lines) - 12
        cv2.rectangle(canvas, (0, top), (w, h), (0, 0, 0), -1)
        for i, line in enumerate(lines):
            cv2.putText(canvas, line, (10, top + 23 + 28 * i),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imshow('ASL to English Captions', canvas)


def main():
    key = os.getenv('GEMINI_API_KEY')
    if not key:
        raise SystemExit('Add GEMINI_API_KEY to your .env file')
    client = genai.Client(api_key=key)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        cap.release()
        raise SystemExit('Could not open webcam. Close other camera apps.')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    frames, started, caption = [], None, ''
    # Poll independently of camera recording; never send an English reply back.
    stop = Event()
    reply_lock = Lock()
    latest_reply = ''

    def poll():
        nonlocal latest_reply
        last_id = 0
        failed = False
        while not stop.is_set():
            try:
                for message in receive_english(last_id):
                    with reply_lock:
                        latest_reply = message['clean_text']
                    last_id = message['id']
                    print('English reply:', latest_reply)
                failed = False
            except Exception as exc:
                if not failed:
                    print('Shared API unavailable:', exc)
                    failed = True
            stop.wait(1)

    poller = Thread(target=poll, daemon=True)
    poller.start()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print('Could not read camera frame')
                break
            # Use original, unmirrored camera frames for translation.
            if started is not None:
                frames.append(frame.copy())
                elapsed = time.monotonic() - started
                status = f'Recording: {elapsed:.1f}s / {MAX_SECONDS}s'
            else:
                elapsed = 0
                status = 'Ready - record one ASL sentence'
            with reply_lock:
                reply = latest_reply
            displayed = ' | '.join(x for x in (f'ASL: {caption}' if caption else '', f'English: {reply}' if reply else '') if x)
            show(frame, status, displayed)
            keypress = cv2.waitKey(1) & 0xFF
            if keypress in (ord('q'), 27):
                break
            if started is None and keypress == 32:
                started = time.monotonic()
                frames, caption = [], ''
            elif started is not None and (keypress == 32 or elapsed >= MAX_SECONDS):
                started = None
                if len(frames) < 3 or elapsed < 0.5:
                    caption = 'Clip too short. Try again.'
                    continue
                show(frame, 'Translating...', '')
                cv2.waitKey(1)
                try:
                    result = translate_video(client, encode_video(frames, elapsed))
                    caption = result['clean_text'] or 'Unable to translate. Please try again.'
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                    if result['clean_text']:
                        try:
                            sent = send_translation(result)
                            print('Sent to teammate:', sent['id'])
                        except Exception as exc:
                            print('Translation shown locally; delivery failed:', exc)
                except Exception as exc:
                    caption = 'Translation error. Check terminal.'
                    print(f'Translation failed: {type(exc).__name__}: {exc}')
                finally:
                    frames = []
    finally:
        stop.set()
        poller.join(timeout=4)
        cap.release()
        cv2.destroyAllWindows()
        client.close()


if __name__ == '__main__':
    main()
