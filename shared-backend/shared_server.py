"""Single-session message relay for a trusted hackathon LAN."""
import os
from pathlib import Path
from threading import Lock
from typing import Literal
from secrets import compare_digest
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name('.env'))
TOKEN = os.getenv('SHARED_API_TOKEN', '').strip()
if not TOKEN:
    raise RuntimeError('Set SHARED_API_TOKEN in shared-backend/.env')
app = FastAPI(title='ASL / English message relay')
messages = []
lock = Lock()


def authorize(x_api_token: str = Header(default='')):
    if not compare_digest(x_api_token.encode(), TOKEN.encode()):
        raise HTTPException(status_code=401, detail='Invalid shared API token')


class Message(BaseModel):
    source: Literal['asl', 'english']
    original_text: str = Field(min_length=1, max_length=4000)
    clean_text: str = Field(min_length=1, max_length=4000)
    intent: Literal['statement', 'question', 'command', 'greeting', 'other'] = 'other'


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/messages', dependencies=[Depends(authorize)])
def send_message(message: Message):
    if not message.clean_text.strip():
        raise HTTPException(status_code=422, detail='Empty caption')
    with lock:
        data = message.model_dump()
        data['id'] = len(messages) + 1
        messages.append(data)
    return {'status': 'success', 'message': data}


@app.get('/messages/{source}', dependencies=[Depends(authorize)])
def get_messages(source: Literal['asl', 'english'], after: int = 0):
    with lock:
        return [m for m in messages if m['source'] == source and m['id'] > after]
