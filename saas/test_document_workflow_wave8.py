from document_workflow import (
    DOCUMENT_STATUSES,
    DOCUMENT_STATUS_LABELS,
    add_document_request,
    update_document_request_status,
)


def run_tests():
    assert "requested" in DOCUMENT_STATUSES
    assert DOCUMENT_STATUS_LABELS["not_required"] == "Not Required"

    try:
        add_document_request(tenant_id=1, task_id=1, document_name="")
        raise AssertionError("Expected ValueError for empty document name")
    except ValueError:
        pass

    try:
        update_document_request_status(tenant_id=1, request_id=1, new_status="bad")
        raise AssertionError("Expected ValueError for invalid status")
    except ValueError:
        pass

    print("Wave 8 document_workflow validation tests passed.")


if __name__ == "__main__":
    run_tests()
