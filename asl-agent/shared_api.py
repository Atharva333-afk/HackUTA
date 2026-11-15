"""HTTP client shared by the webcam and LiveKit agents."""
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))
API_URL = os.getenv('SHARED_API_URL', 'http://127.0.0.1:8000').rstrip('/')
TOKEN = os.getenv('SHARED_API_TOKEN', '')


def send_translation(message):
    if not message.get('clean_text', '').strip():
        return None
    if not TOKEN:
        raise RuntimeError('Set SHARED_API_TOKEN in .env')
    response = requests.post(f'{API_URL}/messages', json=message,
                             headers={'X-API-Token': TOKEN}, timeout=5)
    response.raise_for_status()
    return response.json()['message']


def receive_english(after=0):
    if not TOKEN:
        raise RuntimeError('Set SHARED_API_TOKEN in .env')
    response = requests.get(f'{API_URL}/messages/english', params={'after': after},
                            headers={'X-API-Token': TOKEN}, timeout=3)
    response.raise_for_status()
    return response.json()
