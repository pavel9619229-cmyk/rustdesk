from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ANDROID_RES = ROOT / "flutter" / "android" / "app" / "src" / "main" / "res"
BIG_LOGO = ROOT / "logoMASHAbig.jpg"
COMPACT_LOGO = ROOT / "logoMASHAcompact.jpg"


def square_icon(source: Image.Image, size: int) -> Image.Image:
    icon = Image.new("RGB", (size, size), "white")
    fitted = source.copy()
    fitted.thumbnail((size, size), Image.Resampling.LANCZOS)
    icon.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return icon


def android_status_icon(source: Image.Image, size: int) -> Image.Image:
    grayscale = square_icon(source, size).convert("L")
    alpha = grayscale.point(lambda value: 255 - value)
    icon = Image.new("RGBA", (size, size), "white")
    icon.putalpha(alpha)
    return icon


def save_android_assets(compact_logo: Image.Image) -> None:
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
        launcher = square_icon(compact_logo, launcher_size)
        launcher.save(directory / "ic_launcher.png")
        launcher.save(directory / "ic_launcher_round.png")
        square_icon(compact_logo, round(launcher_size * 2.25)).save(
            directory / "ic_launcher_foreground.png"
        )
        android_status_icon(compact_logo, round(launcher_size * 0.5)).save(
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
    big_logo = Image.open(BIG_LOGO).convert("RGB")
    compact_logo = Image.open(COMPACT_LOGO).convert("RGB")
    master = square_icon(compact_logo, 1024)
    master.save(ROOT / "res" / "icon.png")
    for name in ("logo.png", "logo_light.png", "logo_dark.png"):
        big_logo.save(ROOT / "flutter" / "assets" / name)
    save_android_assets(compact_logo)
    save_windows_assets(master)


if __name__ == "__main__":
    main()