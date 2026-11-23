from jacq import catalog as C


def test_known_and_tiers():
    ts, tier = C.known_terms("Add")
    assert tier == "confirmed" and {"x", "y", "x+y"} <= ts


def test_close_suggests():
    assert "Increment" in C.close("increment", C.all_functions())
    assert "0, 1" in C.close("0,1", C.known_terms("Boolean To (0,1)")[0])


def test_queue_roundtrip(tmp_queue, monkeypatch, tmp_path):
    import json, shutil
    conf = tmp_path / "conf.json"
    shutil.copy(C.CONFIRMED, conf)
    monkeypatch.setattr(C, "CONFIRMED", conf)
    C.load.cache_clear()
    C.queue_add("Wait (ms)", "milliseconds to wait", "cld1")
    assert "Wait (ms)" in C.queue_load()["items"]
    C.record([json.dumps({"function": "Wait (ms)", "ok": True,
                          "result": {"inputs": ["milliseconds to wait"], "outputs": ["millisecond timer value"]}})])
    assert "Wait (ms)" not in C.queue_load()["items"]
    assert C.known_terms("Wait (ms)")[1] == "confirmed"
    C.load.cache_clear()


def test_queue_remove_and_reject_after_two_failed_probes(tmp_queue, monkeypatch, tmp_path):
    """The queue must have exits -- manual removal, and automatic move to rejected after two failed on-site probes (lint no longer queues it)."""
    import json, shutil
    conf = tmp_path / "conf.json"
    shutil.copy(C.CONFIRMED, conf)
    monkeypatch.setattr(C, "CONFIRMED", conf)
    C.queue_add("Frobnicate Widget", "in", "j1")
    C.queue_add("Typo Fn", None, "j1")
    assert C.queue_remove("Typo Fn") and not C.queue_remove("Typo Fn")
    fail = json.dumps({"function": "Frobnicate Widget", "ok": False, "error": "no such function"})
    C.record([fail])
    assert C.queue_load()["items"]["Frobnicate Widget"]["failed"] == 1 and not C.rejected()
    C.record([fail])
    assert "Frobnicate Widget" not in C.queue_load()["items"] and C.rejected()["Frobnicate Widget"]["failed"] == 2
    assert C.main(["queue", "--names"]) == 0
    C.load.cache_clear()


def test_node_kinds_are_not_functions():
    """Node kinds from dumps (subvi / property_node / in_place) got mixed into the learned mapping table and must not be used as function names."""
    assert not {"subvi", "property_node", "in_place"} & set(C.all_functions())
    assert C.known_terms("subvi") == (set(), None)
