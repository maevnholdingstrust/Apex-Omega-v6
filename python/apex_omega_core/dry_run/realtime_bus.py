from collections import deque


class RealtimeBus:
    def __init__(self, maxlen: int = 1000):
        self.events = deque(maxlen=maxlen)

    def emit(self, event_type: str, payload: dict) -> dict:
        event = {'event': event_type, 'payload': payload}
        self.events.append(event)
        return event
