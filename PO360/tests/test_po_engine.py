from datetime import datetime

from app.analyzer.po.engine import POEngine, _email_distribution_map
from app.database.db import Database
from app.mail.models import MailMessage
from app.processing.runner import ApplicationRunner


def msg(mid, thread, subject, body, sender="vendor@abc.com"):
    return MailMessage("demo", mid, thread, sender, "demo@example.com", subject,
                        datetime.fromisoformat("2026-08-10T14:10:00+05:30"), body, "", mid, [])


def ai_result(**overrides):
    base = {
        "is_po_related": True, "po_remarks": "Contains a PO number", "po_confidence": 9,
        "email_conclusion": "Shared a new PO", "pos": [], "unresolved_distribution": None,
        "has_unresolved_distribution": False,
    }
    base.update(overrides)
    return base


def test_not_po_related_only_updates_email_details(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    engine = POEngine(db)
    m = msg("1", "t1", "Meeting", "Let's meet tomorrow")
    email_id = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "1", "thread_id": "t1"})
    out = engine.apply_ai_result(ai_result(is_po_related=False, pos=[]), m, email_id, "demo@example.com")
    assert out["status"] == "PROCESSED"
    assert out["is_po_related"] is False


def test_new_po_with_distribution_and_log(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    engine = POEngine(db)
    m = msg("1", "t1", "PO-1", "Please find attached PO-1")
    email_id = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "1", "thread_id": "t1"})
    result = ai_result(pos=[{
        "po_number": "PO-1", "is_existing_po": False, "company_name": "ABC", "sent_by": "vendor@abc.com",
        "po_datetime": "2026-08-10T14:10:00", "total_goods": "Fan (10)",
        "distributions": [{
            "address": "Pune", "address_date": "2026-08-10T14:10:00", "goods": "Fan (10)",
            "state_region": "MH", "branch_name": "Pune", "branch_code": "A1",
            "branch_manager_name": "BH1", "branch_manager_contact": "111", "on_hold": False,
        }],
    }])
    out = engine.apply_ai_result(result, m, email_id, "demo@example.com")
    assert out["po_numbers"] == ["PO-1"]
    assert db.get_po_details("PO-1")["company_name"] == "ABC"
    assert len(db.get_po_distributions("PO-1")) == 1


def test_hold_notice_marks_existing_distribution(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    engine = POEngine(db)

    m1 = msg("1", "t2", "PO-2", "PO-2 with address", )
    e1 = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "1", "thread_id": "t2"})
    engine.apply_ai_result(ai_result(pos=[{
        "po_number": "PO-2", "is_existing_po": False, "company_name": "ABC", "sent_by": "vendor@abc.com",
        "po_datetime": "", "total_goods": "Light (5)",
        "distributions": [{
            "address": "Nagpur", "address_date": "", "goods": "Light (5)", "state_region": "MH",
            "branch_name": "Nagpur", "branch_code": "", "branch_manager_name": "", "branch_manager_contact": "",
            "on_hold": False,
        }],
    }]), m1, e1, "demo@example.com")

    m2 = msg("2", "t2", "Re: PO-2", "Please hold Nagpur delivery")
    e2 = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "2", "thread_id": "t2"})
    engine.apply_ai_result(ai_result(email_conclusion="Put Nagpur on hold", pos=[{
        "po_number": "PO-2", "is_existing_po": True, "company_name": "", "sent_by": "", "po_datetime": "",
        "total_goods": "",
        "distributions": [{
            "address": "Nagpur", "address_date": "", "goods": "", "state_region": "", "branch_name": "",
            "branch_code": "", "branch_manager_name": "", "branch_manager_contact": "", "on_hold": True,
        }],
    }]), m2, e2, "demo@example.com")

    row = db.get_po_distributions("PO-2")[0]
    assert row["status"] == "ON_HOLD"
    # Original goods/branch info from the first email must not be erased.
    assert row["goods"] == "Light (5)"


def test_address_before_po_is_linked_once_po_number_known(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    engine = POEngine(db)

    m1 = msg("1", "t3", "Delivery address", "PO will follow, deliver to Mumbai Site")
    e1 = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "1", "thread_id": "t3"})
    engine.apply_ai_result(ai_result(pos=[], unresolved_distribution={
        "address": "Mumbai Site", "address_date": "", "goods": "", "state_region": "", "branch_name": "",
        "branch_code": "", "branch_manager_name": "", "branch_manager_contact": "", "on_hold": False,
    }, has_unresolved_distribution=True), m1, e1, "demo@example.com")

    assert len(db.get_pending_po_context("demo@example.com", "t3")) == 1

    m2 = msg("2", "t3", "PO-3", "Attached is PO-3")
    e2 = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "2", "thread_id": "t3"})
    engine.apply_ai_result(ai_result(pos=[{
        "po_number": "PO-3", "is_existing_po": False, "company_name": "ABC", "sent_by": "vendor@abc.com",
        "po_datetime": "", "total_goods": "", "distributions": [],
    }]), m2, e2, "demo@example.com")

    assert db.get_pending_po_context("demo@example.com", "t3") == []
    distributions = db.get_po_distributions("PO-3")
    assert len(distributions) == 1
    assert distributions[0]["address"] == "Mumbai Site"


def test_multiple_attachment_results_keep_each_pos_goods():
    results = [
        ai_result(pos=[{
            "po_number": "PO-ONE", "is_existing_po": False, "company_name": "ABC", "sent_by": "",
            "po_datetime": "", "total_goods": "Fan (10)",
            "distributions": [{
                "address": "Pune", "address_date": "", "goods": "Fan (10)", "state_region": "",
                "branch_name": "", "branch_code": "", "branch_manager_name": "",
                "branch_manager_contact": "", "on_hold": False,
            }],
        }]),
        ai_result(pos=[{
            "po_number": "PO-TWO", "is_existing_po": False, "company_name": "ABC", "sent_by": "",
            "po_datetime": "", "total_goods": "Light (5)",
            "distributions": [{
                "address": "Mumbai", "address_date": "", "goods": "Light (5)", "state_region": "",
                "branch_name": "", "branch_code": "", "branch_manager_name": "",
                "branch_manager_contact": "", "on_hold": False,
            }],
        }]),
    ]

    merged = ApplicationRunner._merge_ai_results(results)

    assert {po["po_number"] for po in merged["pos"]} == {"PO-ONE", "PO-TWO"}
    goods_by_po = {po["po_number"]: po["total_goods"] for po in merged["pos"]}
    assert goods_by_po == {"PO-ONE": "Fan (10)", "PO-TWO": "Light (5)"}


def test_email_distribution_table_maps_each_location_to_its_own_po():
    body = """MC3393
Piprali
LTFH/PO/202627/NEWA068/03/IM40194/26624
L&T Finance Limited Bijarniya Market, Sikar, Rajasthan, 332027
Sita Ram
9549335626
MC3452
Bhadrajun
LTFH/PO/202627/NEWA068/03/IM40194/26470
2nd Floor, Jalore Road, Bhadrajun, Rajasthan, 307029
Bheru
9636259264"""

    distributions = _email_distribution_map(body)

    assert distributions["LTFH/PO/202627/NEWA068/03/IM40194/26624"] == {
        "address": "L&T Finance Limited Bijarniya Market, Sikar, Rajasthan, 332027",
        "state_region": "Rajasthan",
        "branch_name": "Piprali",
        "branch_code": "MC3393",
        "branch_manager_name": "Sita Ram",
        "branch_manager_contact": "9549335626",
    }
    assert distributions["LTFH/PO/202627/NEWA068/03/IM40194/26470"]["branch_name"] == "Bhadrajun"


def test_state_parser_does_not_treat_bihar_colony_as_the_state():
    body = """MC3454
Jamwa Ramgarh
LTFH/PO/202627/NEWA068/03/IM40194/26472
House No 36, Jeen Bihar Colony, Jaipur, Rajasthan, 303109
Raju Bairwa
9694379711"""

    distributions = _email_distribution_map(body)

    assert distributions["LTFH/PO/202627/NEWA068/03/IM40194/26472"]["state_region"] == "Rajasthan"
