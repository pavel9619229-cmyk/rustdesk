from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ANDROID_RES = ROOT / "flutter" / "android" / "app" / "src" / "main" / "res"

BACKGROUND = "#F5F7F4"
INK = "#172226"
TEAL = "#087F8C"
CORAL = "#F25F5C"


def draw_mark(size: int, transparent: bool = False, monochrome: bool = False) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    background = (0, 0, 0, 0) if transparent else BACKGROUND
    image = Image.new("RGBA", (canvas_size, canvas_size), background)
    draw = ImageDraw.Draw(image)

    def point(x: float, y: float) -> tuple[int, int]:
        return round(x * canvas_size), round(y * canvas_size)

    stroke = round(canvas_size * 0.105)
    teal = "#FFFFFF" if monochrome else TEAL
    coral = "#FFFFFF" if monochrome else CORAL

    draw.arc(
        (*point(0.18, 0.16), *point(0.70, 0.78)),
        start=112,
        end=300,
        fill=teal,
        width=stroke,
    )
    draw.arc(
        (*point(0.30, 0.22), *point(0.82, 0.84)),
        start=292,
        end=480,
        fill=coral,
        width=stroke,
    )

    radius = round(stroke * 0.52)
    for x, y, color in (
        (0.267, 0.695, teal),
        (0.693, 0.305, coral),
    ):
        center_x, center_y = point(x, y)
        draw.ellipse(
            (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
            fill=color,
        )

    center_x, center_y = point(0.5, 0.5)
    center_radius = round(canvas_size * 0.085)
    draw.ellipse(
        (
            center_x - center_radius,
            center_y - center_radius,
            center_x + center_radius,
            center_y + center_radius,
        ),
        fill="#FFFFFF" if monochrome else INK,
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def save_android_assets() -> None:
    densities = {
        "mdpi": 48,
        "hdpi": 72,
        "xhdpi": 96,
        "xxhdpi": 144,
        "xxxhdpi": 192,
    }
    for density, launcher_size in densities.items():
        directory = ANDROID_RES / f"mipmap-{density}"
        directory.mkdir(parents=True, exist_ok=True)
        launcher = draw_mark(launcher_size)
        launcher.save(directory / "ic_launcher.png")
        launcher.save(directory / "ic_launcher_round.png")
        draw_mark(round(launcher_size * 2.25), transparent=True).save(
            directory / "ic_launcher_foreground.png"
        )
        draw_mark(round(launcher_size * 0.5), transparent=True, monochrome=True).save(
            directory / "ic_stat_logo.png"
        )


def save_windows_assets(master: Image.Image) -> None:
    icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    for destination in (
        ROOT / "res" / "icon.ico",
        ROOT / "flutter" / "windows" / "runner" / "resources" / "app_icon.ico",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        master.save(destination, format="ICO", sizes=icon_sizes)


def main() -> None:
    master = draw_mark(1024)
    master.save(ROOT / "res" / "icon.png")
    save_android_assets()
    save_windows_assets(master)


if __name__ == "__main__":
    main()