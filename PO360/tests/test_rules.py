from app.analyzer.po.rules import extract_po_number, is_candidate, normalize_event_type


def test_extract_po_number_common_formats():
    assert extract_po_number("PO No: ABC/12345") == "ABC/12345"
    assert extract_po_number("Purchase Order PO-998877 attached") == "998877"


def test_candidate_detection():
    assert is_candidate("Purchase Order", "Please process", ["invoice.pdf"]) is True
    assert is_candidate("Hello", "General meeting tomorrow", ["photo.jpg"]) is False


def test_event_aliases():
    assert normalize_event_type("cancelled") == "CANCELLATION"
    assert normalize_event_type("on hold") == "HOLD"
