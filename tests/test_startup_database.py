import ssa_server.app as app_module
from ssa_server.app import ensure_baby_name_database


def write_source(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "yob1880.txt").write_text("Mary,F,10\nJohn,M,11\n", encoding="utf-8")


def test_missing_database_is_built_at_startup(tmp_path, monkeypatch):
    data_dir = tmp_path / "names"
    db_path = tmp_path / "out" / "names.db"
    write_source(data_dir)
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module.settings, "BABY_NAMES_DB_PATH", db_path)

    ensure_baby_name_database()

    assert db_path.exists()


def test_existing_database_is_left_untouched_at_startup(tmp_path, monkeypatch):
    db_path = tmp_path / "names.db"
    db_path.write_bytes(b"existing database")
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module.settings, "BABY_NAMES_DB_PATH", db_path)

    ensure_baby_name_database()

    assert db_path.read_bytes() == b"existing database"
