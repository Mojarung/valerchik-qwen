"""Пошаговые сценарии по порталу — каталог для расширения браузера и виджета,
плюс редактирование сценариев из админки."""

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas import GuideDraftRequest, GuideIn
from app.schemas.guides import (
    GuideAdminItem,
    GuideAdminList,
    GuideCatalog,
    GuideDeleted,
    GuideDraft,
    GuideEnabled,
    GuideMatch,
    GuidePublic,
    GuideSaved,
)
from app.security import AdminUser, StaffUser
from app.services import audience as audience_of
from app.services import guide_draft, guides, guides_store

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guides", tags=["guides"])
admin_router = APIRouter(prefix="/api/admin/guides", tags=["guides"])


@router.get("", response_model=GuideCatalog, response_model_exclude_unset=True)
async def list_guides(request: Request, audience: str = "") -> GuideCatalog:
    """Без авторизации: сценарии описывают публичный интерфейс портала и
    ничего не знают о пользователе."""
    role = audience_of.normalize(audience)
    return GuideCatalog(
        origin=guides.catalog().get("origin", ""),
        guides=[GuidePublic.model_validate(guides.public(g)) for g in guides.all_guides(role)],
    )


@router.get("/match", response_model=GuideMatch, response_model_exclude_unset=True)
async def match_guide(request: Request, q: str = "", audience: str = "") -> GuideMatch:
    """Какой сценарий приложился бы к такому вопросу — для проверки каталога."""
    rt = request.app.state.rt
    found = guides.match(q, audience_of.normalize(audience), float(rt.dyn.get("guides.min_overlap")))
    return GuideMatch(guide=GuidePublic.model_validate(guides.public(found)) if found else None)


@router.get("/{guide_id}", response_model=GuidePublic, response_model_exclude_unset=True)
async def get_guide(guide_id: str) -> GuidePublic:
    found = guides.get(guide_id)
    if found is None:
        raise HTTPException(404, "Сценарий не найден")
    return GuidePublic.model_validate(guides.public(found))


# --- редактирование из админки ---
#
# Заводские сценарии лежат файлом в репозитории, правки админов — в БД. Список
# отдаётся одним набором с пометкой, откуда сценарий взялся: администратору
# важно видеть, что он меняет заводской, и что удаление вернёт исходный.


def _item(guide: dict[str, Any], record: dict[str, Any] | None, overridden: bool) -> GuideAdminItem:
    return GuideAdminItem.model_validate(
        {
            "id": guide["id"],
            "title": guide.get("title", ""),
            "audience": guide.get("audience", audience_of.ANY),
            "questions": guide.get("questions") or [],
            "steps": guide.get("steps") or [],
            "start": guide.get("start", "/"),
            "enabled": bool(record["enabled"]) if record else True,
            "source": record["source"] if record else "file",
            "author": record["author"] if record else "",
            "updated_at": record["updated_at"] if record else "",
            # заводской сценарий, переписанный в админке: удаление вернёт исходный
            "overridden": overridden,
        }
    )


@admin_router.get("", response_model=GuideAdminList, response_model_exclude_unset=True)
async def admin_list(request: Request, user: StaffUser) -> GuideAdminList:
    rt = request.app.state.rt
    records = {r["id"]: r for r in await guides_store.load_all(rt.db.session_factory)}
    factory_ids = {g["id"] for g in guides.factory()["guides"]}
    items = [
        _item(g, records.get(g["id"]), g["id"] in factory_ids and g["id"] in records)
        for g in guides.catalog()["guides"]
    ]
    # выключенные записи в каталог не попадают, но в списке админки нужны
    shown = {item.id for item in items}
    for guide_id, record in records.items():
        if guide_id in shown:
            continue
        items.append(_item(record, record, guide_id in factory_ids))
    items.sort(key=lambda item: item.id)
    return GuideAdminList(origin=guides.catalog().get("origin", ""), items=items)


@admin_router.put("/{guide_id}", response_model=GuideSaved)
async def admin_save(request: Request, user: AdminUser, guide_id: str, body: GuideIn) -> GuideSaved:
    """Создать или переписать сценарий. Идентификатор берём из адреса."""
    if body.id != guide_id:
        raise HTTPException(400, "Идентификатор в адресе и в теле не совпадают")
    rt = request.app.state.rt
    payload = {
        "title": body.title,
        "audience": body.audience,
        "questions": body.questions,
        "steps": [step.compact() for step in body.steps],
        "enabled": body.enabled,
        "source": body.source,
        "author": user.username,
    }
    await guides_store.save(rt.db.session_factory, guide_id, payload, actor=user.username)
    await _reload(rt)
    return GuideSaved(status="ok", id=guide_id)


@admin_router.post("/draft", response_model=GuideDraft, response_model_exclude_unset=True)
async def admin_draft(request: Request, user: AdminUser, body: GuideDraftRequest) -> GuideDraft:
    """Тексты шагов и формулировки вопроса — нейросетью.

    Селекторы приходят из записи на портале и остаются нетронутыми: модель
    пишет слова, а не разметку. Ничего не сохраняет — черновик возвращается
    в редактор, сохраняет его администратор.
    """
    rt = request.app.state.rt
    steps = [step.compact() for step in body.steps]
    return GuideDraft.model_validate(await guide_draft.draft(rt, body.goal, body.audience, steps))


@admin_router.delete("/{guide_id}", response_model=GuideDeleted)
async def admin_delete(request: Request, user: AdminUser, guide_id: str) -> GuideDeleted:
    """Убрать запись админки. Заводской сценарий с тем же идентификатором вернётся."""
    rt = request.app.state.rt
    if not await guides_store.delete(rt.db.session_factory, guide_id, actor=user.username):
        raise HTTPException(404, "Сценарий не заводился в админке")
    await _reload(rt)
    return GuideDeleted(status="ok", restored=guides.get(guide_id) is not None)


@admin_router.post("/{guide_id}/enabled", response_model=GuideEnabled)
async def admin_set_enabled(
    request: Request, user: AdminUser, guide_id: str, enabled: bool = True
) -> GuideEnabled:
    """Выключить сценарий (в том числе заводской) или включить обратно."""
    rt = request.app.state.rt
    if not await guides_store.set_enabled(rt.db.session_factory, guide_id, enabled, actor=user.username):
        raise HTTPException(404, "Сценарий не найден")
    await _reload(rt)
    return GuideEnabled(status="ok", enabled=enabled)


async def _reload(rt: AppRuntime) -> None:
    """Перечитать наложение и сказать остальным инстансам сделать то же."""
    await guides_store.refresh(rt.db.session_factory)
    try:
        await rt.cache.publish(guides_store.GUIDES_CHANNEL, "changed")
    except Exception:
        log.exception("Не удалось разослать обновление сценариев")
