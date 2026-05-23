import json
import re
from typing import Any

STATUS_RECOMMENDATIONS = [
    "need_info",
    "draft_ready",
    "review_required",
    "high_risk_review",
]

CONFIDENCE_LEVELS = ["low", "medium", "high"]

REQUIRED_KEYS = [
    "status_recommendation",
    "confidence",
    "missing_inputs",
    "risk_flags",
    "applicable_laws",
    "document_requests",
    "client_message_draft",
    "internal_working_note",
    "final_output_markdown",
]

_LIST_FIELDS = ["missing_inputs", "risk_flags", "applicable_laws", "document_requests"]
_TEXT_FIELDS = ["client_message_draft", "internal_working_note", "final_output_markdown"]
_REVIEW_NOTE = "AI-prepared draft. Review by CA firm required before client communication or filing."


def empty_structured_output(raw_text: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "status_recommendation": "review_required",
        "confidence": "low",
        "missing_inputs": [],
        "risk_flags": [reason or "Structured output could not be generated"],
        "applicable_laws": [],
        "document_requests": [],
        "client_message_draft": "",
        "internal_working_note": raw_text or "",
        "final_output_markdown": raw_text or "AI output requires manual review.",
    }


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [value]


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _contains_risk_signal(flags: list[Any]) -> bool:
    joined = " ".join(_to_text(x).lower() for x in flags)
    risk_phrases = ["high risk", "uncertain", "legal opinion", "material", "significant tax"]
    return any(phrase in joined for phrase in risk_phrases)


def normalize_structured_output(data: dict[str, Any]) -> dict[str, Any]:
    output = dict(data or {})

    for key in REQUIRED_KEYS:
        output.setdefault(key, empty_structured_output()[key])

    for key in _LIST_FIELDS:
        output[key] = _to_list(output.get(key))

    for key in _TEXT_FIELDS:
        output[key] = _to_text(output.get(key))

    status = _to_text(output.get("status_recommendation")).strip().lower()
    if status not in STATUS_RECOMMENDATIONS:
        output["status_recommendation"] = "review_required"
        output["risk_flags"].append(f"Invalid status_recommendation received: {status or 'empty'}")
    else:
        output["status_recommendation"] = status

    confidence = _to_text(output.get("confidence")).strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        output["confidence"] = "low"
        output["risk_flags"].append("Invalid confidence received; downgraded to low.")
    else:
        output["confidence"] = confidence

    if output["missing_inputs"]:
        output["status_recommendation"] = "need_info"

    if _contains_risk_signal(output["risk_flags"]):
        if output["status_recommendation"] == "draft_ready":
            output["status_recommendation"] = "review_required"
        if output["status_recommendation"] == "need_info":
            # Keep need_info when inputs are missing, but preserve explicit high-risk flag.
            pass
        elif output["status_recommendation"] == "review_required":
            output["status_recommendation"] = "high_risk_review"

    if _REVIEW_NOTE not in output["final_output_markdown"]:
        if output["final_output_markdown"].strip():
            output["final_output_markdown"] = f"{output['final_output_markdown'].rstrip()}\n\n{_REVIEW_NOTE}"
        else:
            output["final_output_markdown"] = _REVIEW_NOTE

    return output


def extract_json_object(text: str) -> dict[str, Any]:
    src = (text or "").strip()
    if not src:
        raise ValueError("No text provided")

    # 1) Raw JSON object
    try:
        parsed = json.loads(src)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # 2) ```json fenced block (or generic fenced block)
    fence_re = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
    for match in fence_re.finditer(src):
        candidate = match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue

    # 3) First balanced JSON-like object
    start = src.find("{")
    if start == -1:
        raise ValueError("No JSON object found")

    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(src[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = src[start:idx + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    break

    raise ValueError("Could not parse JSON object from text")


def _format_list(items: list[Any]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {_to_text(item)}" for item in items)


def structured_output_to_markdown(data: dict[str, Any]) -> str:
    d = normalize_structured_output(data)

    return (
        "### Structured AI Output\n\n"
        f"**Status recommendation:** `{d['status_recommendation']}`\n\n"
        f"**Confidence:** `{d['confidence']}`\n\n"
        "#### Missing Inputs\n"
        f"{_format_list(d['missing_inputs'])}\n\n"
        "#### Risk Flags\n"
        f"{_format_list(d['risk_flags'])}\n\n"
        "#### Applicable Laws / Sections\n"
        f"{_format_list(d['applicable_laws'])}\n\n"
        "#### Document Requests\n"
        f"{_format_list(d['document_requests'])}\n\n"
        "#### Client Message Draft\n"
        f"{d['client_message_draft'] or '_None_'}\n\n"
        "#### Internal Working Note\n"
        f"{d['internal_working_note'] or '_None_'}\n\n"
        "#### Final Output\n"
        f"{d['final_output_markdown']}"
    )
