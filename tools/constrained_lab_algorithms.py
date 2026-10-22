#!/usr/bin/env python3
"""Deterministic offline bitset/tile benchmark fixtures (stdlib-only).

These are representative algorithms, NOT execution of Ironmate/NagoyAction code.
"""
import hashlib
import json
import random
import sys
import time

def bitset_intersection(a, b, width):
    if width < 0 or any(type(x) is not int or x < 0 or x >= width for x in a + b):
        raise ValueError("invalid bit index")
    mask_a = mask_b = 0
    for index in a:
        mask_a |= 1 << index
    for index in b:
        mask_b |= 1 << index
    mask = mask_a & mask_b
    return [i for i in range(width) if mask & (1 << i)]

def baseline_intersection(a, b):
    return sorted(set(a).intersection(b))

def tile_deduplicate(pixels, tile_width):
    if type(tile_width) is not int or tile_width < 1 or not pixels:
        raise ValueError("invalid tile width or image")
    if any(len(row) != len(pixels[0]) for row in pixels):
        raise ValueError("ragged image")
    if not pixels[0] or len(pixels) % tile_width or len(pixels[0]) % tile_width:
        raise ValueError("image dimensions must be divisible by tile width")
    palette = {}
    indices = []
    for y in range(0, len(pixels), tile_width):
        for x in range(0, len(pixels[0]), tile_width):
            tile = tuple(tuple(row[x:x+tile_width]) for row in pixels[y:y+tile_width])
            if tile not in palette:
                palette[tile] = len(palette)
            indices.append(palette[tile])
    return len(palette), indices

def benchmark(seed=7, width=1024, trials=5):
    if type(seed) is not int or type(width) is not int or width < 1 or type(trials) is not int or trials < 1:
        raise ValueError("invalid benchmark parameters")
    rng = random.Random(seed)
    a = [rng.randrange(width) for _ in range(width)]
    b = [rng.randrange(width) for _ in range(width)]
    measurements = []
    for label, operation in (("set", lambda: baseline_intersection(a, b)),
                             ("bitset", lambda: bitset_intersection(a, b, width))):
        start = time.perf_counter()
        outputs = [operation() for _ in range(trials)]
        measurements.append({"method": label, "wall_seconds": round(time.perf_counter()-start, 6),
                             "digest": hashlib.sha256(json.dumps(outputs, sort_keys=True).encode()).hexdigest()})
    return {"schema": "constrained-lab-algorithms/1", "seed": seed, "width": width,
            "trials": trials, "equivalent": measurements[0]["digest"] == measurements[1]["digest"],
            "measurements": measurements}

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--trials", type=int, default=5)
    args = p.parse_args()
    try:
        result = benchmark(args.seed, args.width, args.trials)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["equivalent"] else 1

if __name__ == "__main__":
    sys.exit(main())
