"""Идемпотентность уведомлений YooKassa.

YooKassa повторяет уведомление до тех пор, пока не получит ответ 2xx.
Так как создание чека в ЛК НПД занимает десятки секунд, первый ответ
может не уложиться в таймаут YooKassa — и придёт повторное уведомление
о том же платеже. Без идемпотентности каждый такой повтор:

  * заново создаёт чек в ЛК НПД (лишние чеки в налоговой);
  * заново публикует NATS-сообщение, из-за чего бот повторно
    начисляет деньги на баланс пользователя.

Модуль хранит id уже обработанных платежей, чтобы повторные
уведомления не приводили к повторному зачислению средств.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# Статусы обработки платежа
ACQUIRED = "acquired"
DUPLICATE = "duplicate"
IN_PROGRESS = "in_progress"

_STATE_PROCESSING = "processing"
_STATE_DONE = "done"

# Платеж, обработка которого «зависла» дольше этого времени (например,
# сервис упал в середине обработки), можно обработать заново.
_STALE_PROCESSING_SECONDS = 60 * 60

# Сколько id обработанных платежей хранить в файле. Повторные
# уведомления YooKassa приходят в течение суток, поэтому самый старый
# хвост можно безопасно отбрасывать, чтобы файл не рос бесконечно.
_MAX_PERSISTED = 5000


class PaymentIdempotencyStore:
    """Хранилище обработанных payment_id.

    Состояние хранится в памяти и (по возможности) дублируется в файл,
    чтобы пережить перезапуск сервиса. Ошибки записи не должны ломать
    обработку платежей — в худшем случае останется только память.

    Методы не содержат ``await`` внутри критической секции, поэтому в
    однопоточном asyncio-приложении гонки между двумя уведомлениями об
    одном платеже невозможны.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        # payment_id -> (state, monotonic timestamp)
        self._states: dict[str, tuple[str, float]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------
    async def acquire(self, payment_id: str) -> str:
        """Пытается зарезервировать платеж под обработку.

        Returns:
            ACQUIRED — платеж новый, обрабатываем его;
            DUPLICATE — платеж уже успешно обработан, повторяем 200 без
                        создания чека и публикации в NATS;
            IN_PROGRESS — платеж обрабатывается прямо сейчас, нужно
                        ответить ошибкой, чтобы YooKassa повторила запрос.
        """
        state = self._states.get(payment_id)

        if state is not None:
            current, started_at = state
            if current == _STATE_DONE:
                return DUPLICATE
            if not self._is_stale(started_at):
                return IN_PROGRESS
            logger.warning(
                "Payment %s: предыдущая обработка зависла — "
                "пробуем заново",
                payment_id,
            )

        self._set_state(payment_id, _STATE_PROCESSING)
        return ACQUIRED

    async def mark_done(self, payment_id: str) -> None:
        """Помечает платеж как успешно обработанный."""
        self._set_state(payment_id, _STATE_DONE)
        logger.info(
            "Payment %s: обработан, повторные вебхуки игнорируются",
            payment_id,
        )

    async def release(self, payment_id: str) -> None:
        """Снимает резерв, чтобы YooKassa могла повторить уведомление."""
        state = self._states.get(payment_id)
        if state is not None and state[0] == _STATE_PROCESSING:
            self._states.pop(payment_id, None)
            self._persist()

    def is_processed(self, payment_id: str) -> bool:
        """Обработан ли платеж (без изменения состояния)."""
        state = self._states.get(payment_id)
        return state is not None and state[0] == _STATE_DONE

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------
    def _set_state(self, payment_id: str, state: str) -> None:
        self._states[payment_id] = (state, time.monotonic())
        self._persist()

    def _is_stale(self, started_at: float) -> bool:
        return (time.monotonic() - started_at) > _STALE_PROCESSING_SECONDS

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Не удалось прочитать %s: %s", self._path, exc)
            return

        for payment_id in raw:
            self._states[payment_id] = (_STATE_DONE, 0.0)
        logger.info(
            "Загружено %d обработанных платежей из %s",
            len(self._states), self._path,
        )

    def _persist(self) -> None:
        if self._path is None:
            return

        done_ids = [
            payment_id
            for payment_id, (state, _) in self._states.items()
            if state == _STATE_DONE
        ]
        if len(done_ids) > _MAX_PERSISTED:
            done_ids = done_ids[-_MAX_PERSISTED:]

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_name(self._path.name + ".tmp")
            tmp_path.write_text(
                json.dumps(done_ids, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(self._path)
        except Exception as exc:
            logger.warning(
                "Не удалось сохранить список обработанных платежей: %s", exc,
            )
