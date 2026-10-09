from display_project.experiments import append_jsonl_atomic


def test_atomic_ledger_rejects_nonserializable(tmp_path):
    target = tmp_path / "ledger.jsonl"
    try:
        append_jsonl_atomic(target, [{"bad": object()}])
    except TypeError:
        pass
    else:
        raise AssertionError("non-serializable ledger row accepted")
    assert not target.exists()
