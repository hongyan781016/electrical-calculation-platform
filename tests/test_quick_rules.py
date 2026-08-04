from src.electrical_calc.database import Database


def test_quick_rules_are_seeded_idempotently(tmp_path):
    path = tmp_path / "rules.db"
    first = Database(path)
    codes = {item["code"] for item in first.list_rules()}
    assert {
        "ELEC.LOAD.POWER_FACTOR",
        "ELEC.CABLE.BV.AMPACITY",
        "ELEC.CABLE.YJV.AMPACITY",
        "ELEC.BREAKER.RATING",
        "ELEC.RCD.PARAMETERS",
    } <= codes
    count = len(codes)
    Database(path)
    assert len(Database(path).list_rules()) == count

def test_catalog_note_migration_preserves_user_content(tmp_path):
    path = tmp_path / "rules-custom.db"
    first = Database(path)
    rule = first.rules_by_code()["ELEC.CABLE.BV.AMPACITY"]
    first.update_rule(rule["id"], {**rule, "note": "用户自定义备注"})

    reopened = Database(path)
    assert reopened.rules_by_code()["ELEC.CABLE.BV.AMPACITY"]["note"] == "用户自定义备注"
