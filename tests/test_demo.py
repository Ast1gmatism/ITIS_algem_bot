from algem_bot.demo import run_demo


async def test_offline_vertical_slice(capsys) -> None:
    await run_demo()
    output = capsys.readouterr().out
    assert "ПУБЛИЧНАЯ ЗОНА БРОНИ" in output
    assert "ЛИЧНАЯ ПАНЕЛЬ ОТВЕТСТВЕННОГО" in output
    assert '"outside_tg_id": null' in output
    assert '"pending_fields": 0' in output
    assert '"restart_preserved_state": true' in output
    assert "ручной итог — 8, не 9" in output
