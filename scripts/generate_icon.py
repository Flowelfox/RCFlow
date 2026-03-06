#!/usr/bin/env python3
"""Generate the RCFlow application icon programmatically using Pillow.

Design concept:
- Rounded square with a dark navy-to-slate gradient background
- Bold, geometric "RC" monogram on the left side
- Three smooth flowing wave/stream lines emanating rightward,
  representing commands flowing from voice/text to machine actions
- Small chevron arrowheads on each stream
- Clean, modern, tech aesthetic with smooth anti-aliasing

Rendered at 4x (4096x4096) and downscaled for crisp results.
"""

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


# --- Configuration ---
RENDER_SIZE = 4096
MASTER_SIZE = 1024

# Colors
BG_TOP = (15, 23, 42)        # slate-900
BG_BOTTOM = (30, 41, 69)     # slate-800ish
ACCENT_1 = (56, 189, 248)    # sky-400
ACCENT_2 = (45, 212, 191)    # teal-400
ACCENT_3 = (129, 140, 248)   # indigo-400
GLOW_BLUE = (56, 189, 248)

CORNER_RADIUS_RATIO = 0.22
PADDING_RATIO = 0.14


def create_background(size: int) -> Image.Image:
    """Vertical gradient background."""
    img = Image.new("RGBA", (size, size), (*BG_TOP, 255))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        t = t * 0.6 + (t ** 2) * 0.4  # Slightly curved
        r = int(BG_BOTTOM[0] + (BG_TOP[0] - BG_BOTTOM[0]) * t)
        g = int(BG_BOTTOM[1] + (BG_TOP[1] - BG_BOTTOM[1]) * t)
        b = int(BG_BOTTOM[2] + (BG_TOP[2] - BG_BOTTOM[2]) * t)
        draw.line([(0, y), (size - 1, y)], fill=(r, g, b, 255))
    return img


def add_center_glow(img: Image.Image, size: int) -> Image.Image:
    """Subtle radial glow for depth."""
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    cx, cy = size // 2, int(size * 0.45)
    max_r = int(size * 0.38)
    for r in range(max_r, 0, -3):
        alpha = int(15 * (r / max_r))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=(*GLOW_BLUE, alpha))
    return Image.alpha_composite(img, glow)


def rounded_rect_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def bezier_point(p0, p1, p2, p3, t):
    """Evaluate a cubic Bezier curve at parameter t."""
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def bezier_tangent(p0, p1, p2, p3, t):
    """Tangent of cubic Bezier at parameter t."""
    u = 1 - t
    dx = (3 * u**2 * (p1[0] - p0[0]) + 6 * u * t * (p2[0] - p1[0])
          + 3 * t**2 * (p3[0] - p2[0]))
    dy = (3 * u**2 * (p1[1] - p0[1]) + 6 * u * t * (p2[1] - p1[1])
          + 3 * t**2 * (p3[1] - p2[1]))
    return (dx, dy)


def draw_smooth_wave(
    img: Image.Image,
    color: tuple[int, int, int],
    thickness: float,
    control_points: list[tuple[float, float]],
    alpha: int = 255,
):
    """Draw a smooth thick curve as a filled polygon using Bezier evaluation.

    control_points: list of (x, y) for a cubic Bezier (4 points).
    """
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    num_samples = 400
    half_w = thickness / 2

    # Sample points and normals along the curve
    pts_top = []
    pts_bot = []
    p0, p1, p2, p3 = control_points

    for i in range(num_samples + 1):
        t = i / num_samples
        pt = bezier_point(p0, p1, p2, p3, t)
        tang = bezier_tangent(p0, p1, p2, p3, t)
        length = math.sqrt(tang[0]**2 + tang[1]**2)
        if length < 1e-6:
            continue
        nx, ny = -tang[1] / length, tang[0] / length  # Normal

        # Fade in/out the alpha
        fade = 1.0
        if t < 0.08:
            fade = t / 0.08
        elif t > 0.88:
            fade = (1.0 - t) / 0.12

        w = half_w * fade
        pts_top.append((pt[0] + nx * w, pt[1] + ny * w))
        pts_bot.append((pt[0] - nx * w, pt[1] - ny * w))

    # Create polygon from top edge + reversed bottom edge
    polygon = pts_top + list(reversed(pts_bot))
    if len(polygon) >= 3:
        draw.polygon(polygon, fill=(*color, alpha))

    return Image.alpha_composite(img, layer)


def draw_chevron(
    img: Image.Image,
    tip: tuple[float, float],
    angle: float,
    arm_len: float,
    thickness: float,
    color: tuple[int, int, int],
    alpha: int = 230,
):
    """Draw a small > chevron arrowhead."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    spread = math.pi / 4.5  # ~40 degree spread
    # Upper arm
    a1 = angle + math.pi - spread
    a2 = angle + math.pi + spread
    p1 = (tip[0] + arm_len * math.cos(a1), tip[1] + arm_len * math.sin(a1))
    p2 = (tip[0] + arm_len * math.cos(a2), tip[1] + arm_len * math.sin(a2))

    draw.line([p1, tip], fill=(*color, alpha), width=max(int(thickness), 1))
    draw.line([tip, p2], fill=(*color, alpha), width=max(int(thickness), 1))

    return Image.alpha_composite(img, layer)


def draw_thick_arc(
    draw: ImageDraw.ImageDraw,
    bbox: list[float],
    start_deg: float,
    end_deg: float,
    color: tuple,
    width: int,
):
    """Draw an arc with specified width."""
    draw.arc(bbox, start=start_deg, end=end_deg, fill=color, width=width)


def draw_letter_r(img: Image.Image, size: int, x: float, y: float,
                  h: float, w: float, stroke: int) -> Image.Image:
    """Draw a bold geometric R."""
    draw = ImageDraw.Draw(img)
    c = (*ACCENT_1, 255)
    half_s = stroke // 2

    # Vertical spine
    draw.rectangle([x - half_s, y, x + half_s, y + h], fill=c)

    # Top horizontal bar
    draw.rectangle([x, y - half_s, x + w * 0.65, y + half_s], fill=c)

    # Bowl (curved right side) - use arc
    bowl_h = h * 0.48
    bowl_w = w * 0.78
    bowl_bbox = [
        x + w * 0.2, y - bowl_h * 0.02,
        x + bowl_w, y + bowl_h * 1.02,
    ]
    draw.arc(bowl_bbox, start=-90, end=90, fill=c, width=stroke)

    # Middle horizontal bar (connects bowl bottom)
    mid_y = y + bowl_h
    draw.rectangle([x, mid_y - half_s, x + w * 0.5, mid_y + half_s], fill=c)

    # Diagonal leg
    leg_start_x = x + w * 0.28
    leg_start_y = mid_y
    leg_end_x = x + w * 0.88
    leg_end_y = y + h

    # Draw leg as polygon for clean anti-aliasing
    dx = leg_end_x - leg_start_x
    dy = leg_end_y - leg_start_y
    length = math.sqrt(dx**2 + dy**2)
    nx, ny = -dy / length * half_s, dx / length * half_s
    draw.polygon([
        (leg_start_x + nx, leg_start_y + ny),
        (leg_start_x - nx, leg_start_y - ny),
        (leg_end_x - nx, leg_end_y - ny),
        (leg_end_x + nx, leg_end_y + ny),
    ], fill=c)

    return img


def draw_letter_c(img: Image.Image, size: int, x: float, y: float,
                  h: float, w: float, stroke: int) -> Image.Image:
    """Draw a bold geometric C."""
    draw = ImageDraw.Draw(img)
    c = (*ACCENT_2, 255)

    # C as a thick arc (~270 degrees, open on right)
    c_bbox = [x, y, x + w, y + h]
    draw.arc(c_bbox, start=30, end=330, fill=c, width=stroke)

    return img


def generate_master_icon() -> Image.Image:
    """Generate the master icon at RENDER_SIZE and downscale."""
    size = RENDER_SIZE
    pad = size * PADDING_RATIO
    area = size - 2 * pad

    # 1. Background
    img = create_background(size)
    img = add_center_glow(img, size)

    # 2. Letters
    stroke = int(size * 0.038)
    letter_h = area * 0.50
    letter_w = area * 0.30

    # Position: R on left, C next to it, both vertically centered
    letters_total_w = letter_w + area * 0.04 + letter_w * 0.95
    base_x = pad + (area * 0.50 - letters_total_w) / 2
    base_y = (size - letter_h) / 2

    img = draw_letter_r(img, size, base_x, base_y, letter_h, letter_w, stroke)

    c_x = base_x + letter_w + area * 0.02
    c_w = letter_w * 0.95
    img = draw_letter_c(img, size, c_x, base_y, letter_h, c_w, stroke)

    # 3. Flowing stream lines (3 bezier curves flowing right)
    flow_start_x = c_x + c_w * 0.65
    flow_end_x = size - pad * 0.6

    # Each flow line: start from near C, wave rightward
    flow_configs = [
        {
            "color": ACCENT_1,
            "y_center": size * 0.365,
            "amplitude": size * 0.055,
            "thickness": stroke * 0.72,
            "alpha": 220,
        },
        {
            "color": ACCENT_2,
            "y_center": size * 0.500,
            "amplitude": size * 0.048,
            "thickness": stroke * 0.85,
            "alpha": 240,
        },
        {
            "color": ACCENT_3,
            "y_center": size * 0.635,
            "amplitude": size * 0.055,
            "thickness": stroke * 0.72,
            "alpha": 220,
        },
    ]

    for cfg in flow_configs:
        mid_x = (flow_start_x + flow_end_x) / 2
        amp = cfg["amplitude"]
        yc = cfg["y_center"]

        # S-curve bezier control points
        p0 = (flow_start_x, yc)
        p1 = (flow_start_x + (flow_end_x - flow_start_x) * 0.3, yc - amp)
        p2 = (flow_start_x + (flow_end_x - flow_start_x) * 0.7, yc + amp)
        p3 = (flow_end_x, yc)

        img = draw_smooth_wave(
            img, cfg["color"], cfg["thickness"],
            [p0, p1, p2, p3], cfg["alpha"],
        )

        # Arrowhead at end
        tang = bezier_tangent(p0, p1, p2, p3, 0.95)
        angle = math.atan2(tang[1], tang[0])
        tip_pt = bezier_point(p0, p1, p2, p3, 0.92)
        img = draw_chevron(
            img, tip_pt, angle,
            arm_len=cfg["thickness"] * 2.5,
            thickness=cfg["thickness"] * 0.65,
            color=cfg["color"],
            alpha=cfg["alpha"],
        )

    # 4. Rounded mask
    corner_radius = int(size * CORNER_RADIUS_RATIO)
    mask = rounded_rect_mask(size, corner_radius)
    img.putalpha(mask)

    # 5. Downscale
    master = img.resize((MASTER_SIZE, MASTER_SIZE), Image.Resampling.LANCZOS)
    return master


def generate_maskable_icon(master: Image.Image, target_size: int) -> Image.Image:
    """Maskable icon with safe zone padding (80% content area)."""
    safe_size = int(target_size * 0.80)
    content = master.resize((safe_size, safe_size), Image.Resampling.LANCZOS)

    result = Image.new("RGBA", (target_size, target_size), (*BG_TOP, 255))
    offset = (target_size - safe_size) // 2
    result.paste(content, (offset, offset), content)
    return result


def save_png(img: Image.Image, path: str, target_size: int):
    """Resize and save as PNG."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    resized = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    resized.convert("RGBA").save(path, "PNG")
    print(f"  {path} ({target_size}x{target_size})")


def save_ico(img: Image.Image, path: str, sizes: list[int]):
    """Save multi-size ICO file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Pillow ICO: use sizes parameter to embed multiple resolutions
    # The source image must be large enough; Pillow auto-resizes to each
    large = img.resize((max(sizes), max(sizes)), Image.Resampling.LANCZOS).convert("RGBA")
    large.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"  {path} (ICO: {sizes})")


def main():
    project_root = Path(__file__).resolve().parent.parent
    client_root = project_root / "rcflowclient"

    print("Generating RCFlow icon...")

    master = generate_master_icon()
    print("Master generated.\n")

    # Reference copy
    save_png(master, str(client_root / "assets" / "icon" / "app_icon.png"), MASTER_SIZE)

    # Web
    print("\nWeb:")
    save_png(master, str(client_root / "web" / "favicon.png"), 16)
    save_png(master, str(client_root / "web" / "icons" / "Icon-192.png"), 192)
    save_png(master, str(client_root / "web" / "icons" / "Icon-512.png"), 512)
    maskable_192 = generate_maskable_icon(master, 192)
    maskable_512 = generate_maskable_icon(master, 512)
    save_png(maskable_192, str(client_root / "web" / "icons" / "Icon-maskable-192.png"), 192)
    save_png(maskable_512, str(client_root / "web" / "icons" / "Icon-maskable-512.png"), 512)

    # Android
    print("\nAndroid:")
    res = client_root / "android" / "app" / "src" / "main" / "res"
    for folder, sz in [("mipmap-mdpi", 48), ("mipmap-hdpi", 72),
                       ("mipmap-xhdpi", 96), ("mipmap-xxhdpi", 144),
                       ("mipmap-xxxhdpi", 192)]:
        save_png(master, str(res / folder / "ic_launcher.png"), sz)

    # Windows ICO
    print("\nWindows:")
    save_ico(master, str(client_root / "windows" / "runner" / "resources" / "app_icon.ico"),
             [16, 32, 48, 64, 128, 256])

    # macOS
    print("\nmacOS:")
    macos = client_root / "macos" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset"
    for sz in [16, 32, 64, 128, 256, 512, 1024]:
        save_png(master, str(macos / f"app_icon_{sz}.png"), sz)

    # Backend tray icon (used by system tray on Windows)
    print("\nBackend tray icon:")
    save_ico(master, str(project_root / "assets" / "tray_icon.ico"),
             [16, 32, 48, 64, 128, 256])

    print("\nDone!")


if __name__ == "__main__":
    main()
