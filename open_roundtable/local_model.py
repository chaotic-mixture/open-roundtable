"""Address of the owner-operated local model service."""
import os


def ollama_origin():
    port = int(os.environ.get('ROUNDTABLE_OLLAMA_PORT', '11434'))
    if not 1 <= port <= 65535:
        raise ValueError('local model port must be between 1 and 65535')
    return f'http://127.0.0.1:{port}'
