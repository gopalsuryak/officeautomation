import json

from output_schema import (
    extract_json_object,
    normalize_structured_output,
    structured_output_to_markdown,
)


def run():
    raw = '{"status_recommendation":"draft_ready","confidence":"high","missing_inputs":[],"risk_flags":[],"applicable_laws":[],"document_requests":[],"client_message_draft":"ok","internal_working_note":"n","final_output_markdown":"draft"}'
    parsed_raw = extract_json_object(raw)
    assert parsed_raw["status_recommendation"] == "draft_ready"

    fenced = "hello\n```json\n" + raw + "\n```\nbye"
    parsed_fenced = extract_json_object(fenced)
    assert parsed_fenced["confidence"] == "high"

    normalized = normalize_structured_output({"status_recommendation": "invalid", "confidence": "bad"})
    assert normalized["status_recommendation"] in {"review_required", "need_info", "high_risk_review"}
    assert normalized["confidence"] == "low"
    assert "final_output_markdown" in normalized

    md = structured_output_to_markdown(normalized)
    assert "Structured AI Output" in md
    assert "Raw JSON" not in md

    print("Wave 5 output schema self-test passed.")
    print(json.dumps({"status": "ok"}))


if __name__ == "__main__":
    run()
