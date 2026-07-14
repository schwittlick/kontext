"""downstream sizing derived from survey numbers.

every assumption lives here as a named constant and is echoed into the
report json, so phase 2 can check what the projections were based on.
default hardware: nvidia titan x (12 gb vram), 64 gb ram.
"""

from __future__ import annotations

CHARS_PER_WORD = 6.0
CHUNK_WORDS = 300           # target passage size for chunking
CHUNK_OVERLAP = 0.15        # 15% overlap between neighbouring chunks
EMBED_DIM = 1024            # bge-m3 dense vector size
INT8_BYTES_PER_VECTOR = EMBED_DIM          # scalar-quantized, resident in ram
FP32_BYTES_PER_VECTOR = EMBED_DIM * 4      # originals on disk for rescoring
HNSW_BYTES_PER_VECTOR = 150                # graph links + qdrant payload overhead
WORDS_PER_OCR_PAGE = 300    # typical yield of an ocr'd book page
EMBED_CHUNKS_PER_SEC = 150.0  # bge-m3, fp32, batched, on a titan x -- conservative
OCR_PAGES_PER_SEC = 1.0       # surya/tesseract-class ocr on the same machine
RAM_GB = 64

GB = 1024 ** 3


def compute_estimates(
    words_extractable: int,
    scanned_pages: int,
    embed_rate: float | None = None,
    ocr_rate: float | None = None,
) -> dict:
    embed_rate = embed_rate or EMBED_CHUNKS_PER_SEC
    ocr_rate = ocr_rate or OCR_PAGES_PER_SEC

    words_ocr = int(scanned_pages * WORDS_PER_OCR_PAGE)
    total_words = words_extractable + words_ocr
    chunks = int(total_words / CHUNK_WORDS * (1 + CHUNK_OVERLAP))

    ram_gb = chunks * (INT8_BYTES_PER_VECTOR + HNSW_BYTES_PER_VECTOR) / GB
    disk_gb = chunks * FP32_BYTES_PER_VECTOR / GB
    embed_hours = chunks / embed_rate / 3600
    ocr_hours = scanned_pages / ocr_rate / 3600

    if ram_gb < RAM_GB * 0.5:
        ram_verdict = f"fits in ram comfortably ({ram_gb:.1f} of {RAM_GB} gb)"
    elif ram_gb < RAM_GB * 0.8:
        ram_verdict = f"fits in ram, tight ({ram_gb:.1f} of {RAM_GB} gb) -- consider binary quantization"
    else:
        ram_verdict = f"exceeds comfortable ram ({ram_gb:.1f} of {RAM_GB} gb) -- use on-disk index + binary quantization"

    return {
        "words_extractable": words_extractable,
        "words_from_ocr": words_ocr,
        "words_total": total_words,
        "chunks": chunks,
        "vector_ram_int8_gb": round(ram_gb, 2),
        "vector_disk_fp32_gb": round(disk_gb, 2),
        "ram_verdict": ram_verdict,
        "embed_hours": round(embed_hours, 1),
        "ocr_hours": round(ocr_hours, 1),
        "assumptions": {
            "chars_per_word": CHARS_PER_WORD,
            "chunk_words": CHUNK_WORDS,
            "chunk_overlap": CHUNK_OVERLAP,
            "embed_dim": EMBED_DIM,
            "int8_bytes_per_vector": INT8_BYTES_PER_VECTOR,
            "fp32_bytes_per_vector": FP32_BYTES_PER_VECTOR,
            "hnsw_bytes_per_vector": HNSW_BYTES_PER_VECTOR,
            "words_per_ocr_page": WORDS_PER_OCR_PAGE,
            "embed_chunks_per_sec": embed_rate,
            "ocr_pages_per_sec": ocr_rate,
            "ram_gb": RAM_GB,
        },
    }


def humanize_hours(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"
