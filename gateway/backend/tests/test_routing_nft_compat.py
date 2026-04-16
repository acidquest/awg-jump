from app.services import routing


def test_ensure_compat_jump_skips_missing_builtin_chain(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], input_data: str | None = None):
        if args == ["nft", "list", "chain", "ip", "filter", "INPUT"]:
            return 1, "No such file or directory"
        raise AssertionError(f"Unexpected command: {args}")

    def fake_run_logged(args: list[str], input_data: str | None = None) -> None:
        calls.append(args)

    monkeypatch.setattr(routing, "_run", fake_run)
    monkeypatch.setattr(routing, "_run_logged", fake_run_logged)

    routing._ensure_compat_jump("filter", "INPUT", routing.FILTER_INPUT_CHAIN)

    assert calls == []
