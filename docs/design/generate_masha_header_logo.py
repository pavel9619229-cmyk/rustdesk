from collections import deque
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "logoMASHAcompact.jpg"
DESTINATION = ROOT / "docs" / "design" / "masha-header-logo.png"


def is_background(pixel: tuple[int, int, int]) -> bool:
    return all(channel >= 235 for channel in pixel)


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    background = set()
    pending = deque()

    for x in range(width):
        pending.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        pending.extend(((0, y), (width - 1, y)))

    while pending:
        x, y = pending.popleft()
        if (x, y) in background or not is_background(pixels[x, y][:3]):
            continue
        background.add((x, y))
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= next_x < width and 0 <= next_y < height:
                pending.append((next_x, next_y))

    for x, y in background:
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)

    image.save(DESTINATION)


if __name__ == "__main__":
    main()