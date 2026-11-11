import hashlib
import math
import re


def tokenize(value: str) -> list[str]:
    return [item for item in re.split(r"[^\w\u4e00-\u9fff]+", value.lower()) if item]


def embed(text: str, dimensions: int = 32) -> list[float]:
    vector = [0.0] * dimensions
    for index, token in enumerate(tokenize(text)):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        slot = int.from_bytes(digest[:2], "big") % dimensions
        vector[slot] += 1.0 + (digest[2] / 255.0) * 0.1
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector

