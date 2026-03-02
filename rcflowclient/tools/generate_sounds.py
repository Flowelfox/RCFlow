"""Generate simple notification WAV sound files for RCFlow client."""

import math
import struct
import wave

SAMPLE_RATE = 44100
OUTPUT_DIR = "assets/sounds"


def generate_tone(
    frequencies: list[tuple[float, float, float]],
    filename: str,
    sample_rate: int = SAMPLE_RATE,
) -> None:
    """Generate a WAV file from a list of (freq_hz, duration_s, amplitude) segments.

    Each segment is a sine wave at the given frequency, duration, and amplitude (0-1).
    Segments play sequentially. An ADSR-like fade is applied per segment.
    """
    samples: list[int] = []

    for freq, duration, amplitude in frequencies:
        n_samples = int(sample_rate * duration)
        fade_samples = min(int(sample_rate * 0.01), n_samples // 4)

        for i in range(n_samples):
            t = i / sample_rate
            value = amplitude * math.sin(2.0 * math.pi * freq * t)

            # Fade in
            if i < fade_samples:
                value *= i / fade_samples
            # Fade out
            elif i > n_samples - fade_samples:
                value *= (n_samples - i) / fade_samples

            samples.append(int(value * 32767))

    filepath = f"{OUTPUT_DIR}/{filename}"
    with wave.open(filepath, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    size_kb = len(samples) * 2 / 1024
    print(f"  {filename}: {len(samples)} samples, {size_kb:.1f} KB")


def main() -> None:
    print("Generating notification sounds...")

    # Gentle chime: two soft ascending tones
    generate_tone(
        [
            (523.25, 0.12, 0.4),  # C5
            (0, 0.02, 0.0),  # tiny gap
            (659.25, 0.15, 0.35),  # E5
            (0, 0.02, 0.0),
            (783.99, 0.20, 0.3),  # G5 (longer ring)
        ],
        "gentle_chime.wav",
    )

    # Soft ping: quick high-pitched ping
    generate_tone(
        [
            (1046.50, 0.08, 0.5),  # C6
            (1318.51, 0.12, 0.35),  # E6
        ],
        "soft_ping.wav",
    )

    # Subtle pop: short low-to-high sweep
    generate_tone(
        [
            (440.0, 0.04, 0.5),  # A4
            (880.0, 0.06, 0.4),  # A5
            (1760.0, 0.03, 0.2),  # A6
        ],
        "subtle_pop.wav",
    )

    # Bell: classic bell-like tone with harmonics simulated via frequency steps
    generate_tone(
        [
            (880.0, 0.06, 0.5),  # A5 attack
            (880.0, 0.12, 0.4),  # sustain
            (880.0, 0.18, 0.25),  # decay
            (440.0, 0.12, 0.15),  # low ring
        ],
        "bell.wav",
    )

    # Digital tone: electronic two-tone beep
    generate_tone(
        [
            (800.0, 0.10, 0.45),
            (0, 0.05, 0.0),
            (1000.0, 0.10, 0.45),
            (0, 0.05, 0.0),
            (800.0, 0.08, 0.3),
        ],
        "digital_tone.wav",
    )

    print("Done!")


if __name__ == "__main__":
    main()
