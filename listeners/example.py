import re

CHAT_ID = -1000000000000

SIGNAL = re.compile(
    r"Synthetic signal: (?P<side>BUY|SELL) (?P<symbol>[A-Z]+) at (?P<price>[\d.]+)"
)


def listen(post):
    for match in SIGNAL.finditer(post.text or ""):
        yield match.groupdict()
