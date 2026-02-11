from collections import deque
from typing import Deque, Dict, Any, List

class ConversationMemory:
    def __init__(self, max_turns: int = 8):
        self.buffer: Deque[Dict[str,str]] = deque(maxlen=max_turns)

    def add_user(self, content: str):
        self.buffer.append({"role":"user", "content":content})

    def add_assistant(self, content: str):
        self.buffer.append({"role":"assistant", "content":content})

    def context(self) -> List[Dict[str,str]]:
        return list(self.buffer)
