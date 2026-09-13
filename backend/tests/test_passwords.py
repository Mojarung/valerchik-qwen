"""Пароли сотрудников: PBKDF2-SHA256 на 600 000 итераций (рекомендация OWASP
2023), число итераций записано в самом хеше, прежние хеши на 260 000 итераций
перехешируются при первом успешном входе.
"""

import hashlib

from sqlalchemy import select

from app.config import Settings
from app.models import User
from app.security import hash_password, needs_rehash, verify_password


def _legacy(password: str, salt: str = "0" * 32) -> str:
    """Хеш в прежнем формате `pbkdf2$соль$дайджест` на 260 000 итераций."""
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()
    return f"pbkdf2${salt}${digest}"


def test_production_work_factor_is_owasp_2023():
    assert Settings.model_fields["password_hash_iterations"].default == 600_000


def test_hash_records_algorithm_and_iterations():
    stored = hash_password("s3cret-пароль")

    algo, iterations, salt, digest = stored.split("$")
    assert algo == "pbkdf2_sha256"
    assert int(iterations) > 0
    assert salt and digest
    assert verify_password("s3cret-пароль", stored)
    assert not verify_password("не тот", stored)


def test_legacy_hash_still_verifies_and_needs_rehash():
    stored = _legacy("старый-пароль")

    assert verify_password("старый-пароль", stored)
    assert not verify_password("не тот", stored)
    assert needs_rehash(stored)


def test_current_hash_does_not_need_rehash():
    assert not needs_rehash(hash_password("x"))


def test_malformed_hash_never_verifies():
    assert not verify_password("x", "что-то-не-то")
    assert not verify_password("x", "pbkdf2_sha256$много$соль$дайджест")
    assert not verify_password("x", "")


def test_login_upgrades_legacy_hash(client):
    rt = client.app.state.rt

    async def set_hash(value: str) -> None:
        async with rt.db.session_factory() as db:
            user = (await db.execute(select(User).where(User.username == "op_maria"))).scalar_one()
            user.password_hash = value
            await db.commit()

    async def stored_hash() -> str:
        async with rt.db.session_factory() as db:
            user = (await db.execute(select(User).where(User.username == "op_maria"))).scalar_one()
            return user.password_hash

    client.portal.call(set_hash, _legacy("operator12345"))

    resp = client.post("/api/auth/login", json={"username": "op_maria", "password": "operator12345"})

    assert resp.status_code == 200
    upgraded = client.portal.call(stored_hash)
    assert upgraded.startswith("pbkdf2_sha256$")
    assert not needs_rehash(upgraded)
    again = client.post("/api/auth/login", json={"username": "op_maria", "password": "operator12345"})
    assert again.status_code == 200


def test_unknown_login_still_pays_for_pbkdf2(client, monkeypatch):
    """Отказ по несуществующему логину приходил мгновенно, а по настоящему — после
    PBKDF2: время ответа выдавало, какие сотрудники есть."""
    from app.services import credentials

    checked: list[str] = []
    real = credentials.verify_password

    def spy(password: str, stored: str) -> bool:
        checked.append(stored)
        return real(password, stored)

    monkeypatch.setattr(credentials, "verify_password", spy)

    resp = client.post("/api/auth/login", json={"username": "нет-такого", "password": "x"})

    assert resp.status_code == 401
    assert len(checked) == 1
    assert checked[0].startswith("pbkdf2_sha256$")
