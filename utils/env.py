"""Central .env loader — always use this instead of bare load_dotenv()."""

import os
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"), override=True)
