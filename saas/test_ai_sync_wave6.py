from ai_sync import extract_latest_structured_json_from_comments


def run_tests():
    comments_list = [
        {"body": "Old\n```json\n{\"status_recommendation\":\"need_info\"}\n```"},
        {"body": "New\n```json\n{\"status_recommendation\":\"draft_ready\",\"confidence\":\"high\"}\n```"},
    ]
    out1 = extract_latest_structured_json_from_comments(comments_list)
    assert out1["status_recommendation"] == "draft_ready"

    comments_obj = {
        "comments": [
            {"content": "```json\n{\"status_recommendation\":\"review_required\"}\n```"}
        ]
    }
    out2 = extract_latest_structured_json_from_comments(comments_obj)
    assert out2["status_recommendation"] == "review_required"

    bad = [{"body": "No json here"}]
    try:
        extract_latest_structured_json_from_comments(bad)
        raise AssertionError("Expected ValueError for invalid comments")
    except ValueError:
        pass

    print("Wave 6 ai_sync extraction tests passed.")


if __name__ == "__main__":
    run_tests()
