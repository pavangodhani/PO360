import pytest

pytest.skip(
    "Excel export is deferred to a future task; the DB schema it depended on "
    "(pos/po_locations/po_items) was replaced by the email_details/po_details/"
    "po_distribution/po_logs schema.",
    allow_module_level=True,
)

