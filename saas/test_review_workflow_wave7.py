from review_workflow import get_available_review_actions, ACTION_TO_STATUS


def run_tests():
    actions_ai_draft = get_available_review_actions("ai_draft_ready")
    names = [a["action"] for a in actions_ai_draft]
    assert "send_for_review" in names
    assert "request_changes" in names
    assert "cancel" in names

    actions_closed = get_available_review_actions("closed")
    assert actions_closed == []

    assert ACTION_TO_STATUS["approve"] == "approved"
    assert ACTION_TO_STATUS["mark_filed"] == "filed"

    print("Wave 7 review_workflow basic tests passed.")


if __name__ == "__main__":
    run_tests()
