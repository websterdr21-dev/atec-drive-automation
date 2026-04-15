import os
from utils.env import load as load_env
load_env()

print("API key loaded:", bool(os.getenv("ANTHROPIC_API_KEY")))

from utils.extract import extract_serial_from_photo

result = extract_serial_from_photo("WhatsApp Image 2026-03-30 at 16.03.24 (2).jpeg")
print(result)
