import json
import re
import httpx

from . import config

SYSTEM_PROMPT = """You are an invoice-review agent for an accounts payable team.

For EACH invoice package given, choose exactly one action:
- settle_invoice: valid, reconciled, and within autonomous authority.
- request_approval: commercially valid, but outside delegated authority.
- hold_invoice: payment pauses until a stated verification completes.
- reject_duplicate: the same commercial invoice was already paid.
- open_exception: material records conflict and need an exception workflow.

The documents mix genuine decisive facts with old/superseded examples, negated
statements ("not approved", "no longer applies"), cover-sheet boilerplate, and
irrelevant words that merely sound like action verbs. Ignore anything that is
an example, archived, negated, or from a cover sheet. Base the action ONLY on
the current, decisive statement(s) in the package body.

References inside the documents look like bracketed tags, e.g. [R3]. For each
package, cite ONLY the decisive bracketed references that actually determine
the action (do not cite cover-sheet references, examples, or decoys).

Return STRICT JSON ONLY (no markdown fences, no prose) as an array. Each item:
{
  "packageId": "<echo the package's id>",
  "action": "<one of the five actions above>",
  "facts": {"vendorName": "...", "invoiceNumber": "...", "amountMinor": <integer, minor units>, "currency": "<ISO 4217>"},
  "evidenceRefs": ["<decisive bracketed refs, e.g. [R3]>", "..."],
  "rationale": "<60-1500 chars, name the chosen action explicitly and cite at least two evidence refs>"
}
Return one item per package, in the same order as given. Output nothing else.
"""


def _extract_json_array(text: str):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array found in model output")
    return json.loads(text[start:end + 1])


def decide_packages(packages: list[dict]) -> dict:
    """Batch every package needing a fresh decision into ONE model call.
    Returns {packageId: decision_dict}."""
    if not packages:
        return {}
    if not config.AIPIPE_TOKEN:
        raise RuntimeError("AIPIPE_TOKEN is not configured")

    user_payload = json.dumps({"packages": packages}, ensure_ascii=False)
    body = {
        "model": config.AIPIPE_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {config.AIPIPE_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{config.AIPIPE_BASE_URL.rstrip('/')}/chat/completions"

    last_err = None
    for attempt in range(2):  # one retry with a repair nudge
        if attempt == 1:
            body["messages"].append({
                "role": "user",
                "content": "Your previous output was not valid strict JSON matching the "
                            "required array schema. Return ONLY the corrected JSON array now.",
            })
        with httpx.Client(timeout=40.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        try:
            items = _extract_json_array(content)
            by_id = {}
            for item in items:
                pid = item["packageId"]
                by_id[pid] = item
            missing = [p["packageId"] for p in packages if p["packageId"] not in by_id]
            if missing:
                raise ValueError(f"missing decisions for packages: {missing}")
            return by_id
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise RuntimeError(f"AI decision step failed after retry: {last_err}")
