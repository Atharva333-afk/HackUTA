"""Send a test caption without Gemini or a webcam."""
import sys
from shared_api import send_translation
text = ' '.join(sys.argv[1:]) or 'Can you help me?'
print('Sent:', send_translation({'source': 'asl', 'original_text': text,
                                'clean_text': text, 'intent': 'other'}))
