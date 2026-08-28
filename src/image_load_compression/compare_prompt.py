from typing import Any


SYSTEM_PROMPT = """You are an e-commerce product-image analyst. Describe visual evidence
that can help another system compare two listings. Report observations and conflicts, but
never decide whether the listings are the same or different product."""

TASK_PROMPT = """Analyze the two image sets using these rules:

- Consider only the product itself. Ignore human models, backgrounds, props, staging,
  lighting, camera angle, crop, resolution, retailer overlay badges, and watermarks.
- Use a strict, literal comparison standard. Examine brand, model or variant name,
  colorway, size, capacity, weight, pack count or quantity, and included accessories.
  A different count, size, or colorway is a strict product-attribute conflict downstream.
- Transcribe legible packaging or label text verbatim when it identifies an attribute.
- Flag any set that is lifestyle-only, partial, or too low-quality to read attributes.
- State only evidence directly visible in the images. Do not infer a standard size, capacity,
  formulation, or product type from general knowledge. If a value is not printed, call it
  unknown; never estimate it or describe it as standard, typical, approximate, or "-style."

Write concise plain English under exactly these headings:
Set A: basic observation of the first set
Set B: basic observation of the second set
Agreements: list any attributes that are visually identical across both sets.
Discrepancies: directly point out any conflicts.  
Cannot be determined from images: only give essential ones.

Respect the strict maximum of 250 words total. Section limits are: Set A 45 words; Set B
45 words; Agreements 40 words; Discrepancies 70 words; Cannot be determined from images
40 words.

Do not state whether the products are the same or different. Do not give a match score,
probability, verdict, or yes/no answer. You may express confidence only about individual
visual observations.

Always focus on the essential product attributes based on the cateogories. 
"""


def build_messages(set_a_urls: list[str], set_b_urls: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"[SET A] {len(set_a_urls)} image(s)"}
    ]
    content.extend(
        {"type": "image_url", "image_url": {"url": url}} for url in set_a_urls
    )
    content.append({"type": "text", "text": f"[SET B] {len(set_b_urls)} image(s)"})
    content.extend(
        {"type": "image_url", "image_url": {"url": url}} for url in set_b_urls
    )
    content.append({"type": "text", "text": TASK_PROMPT})
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
