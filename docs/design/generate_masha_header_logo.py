from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "logoMASHAcompact.jpg"
DESTINATION = ROOT / "docs" / "design" / "masha-header-logo.png"


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    radius = min(width, height) / 2

    for y in range(height):
        for x in range(width):
            if (x - center_x) ** 2 + (y - center_y) ** 2 > radius**2:
                pixels[x, y] = (0, 0, 0, 255)

    image.save(DESTINATION)


if __name__ == "__main__":
    main()