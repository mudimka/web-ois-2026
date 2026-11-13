"""Гонка за аудиторию: три запроса одновременно создают мероприятия в одной аудитории
на одно и то же время (BR-1: аудитория не может быть занята дважды).

Запуск при работающем API:

    docker compose exec api python scripts/race_rooms.py

Адрес API можно поменять переменной окружения API_URL. Число параллельных запросов
и аудиторию можно задать аргументами: python scripts/race_rooms.py 5 3
(5 запросов, аудитория с id 3).
"""
import os
import random
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")
REQUESTS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
ROOM_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 3  # 3 — «Коворкинг», вместимость 25

TOMSK = timezone(timedelta(hours=7))


def random_slot() -> tuple[datetime, datetime]:
    """Свободное окно на два часа в 2027 году. Случайное, чтобы повторные запуски
    не упирались в мероприятия, созданные прошлым запуском."""
    day = datetime(2027, 1, 1, tzinfo=TOMSK) + timedelta(days=random.randint(0, 300))
    start = day.replace(hour=random.randint(8, 20))
    return start, start + timedelta(hours=2)


def main() -> None:
    starts_at, ends_at = random_slot()
    print(f"Аудитория {ROOM_ID}, {starts_at:%d.%m.%Y %H:%M}–{ends_at:%H:%M}, запросов: {REQUESTS}")

    results: list[tuple[int, int, str]] = []  # (номер запроса, код ответа, текст)
    created_ids: list[int] = []
    # Барьер: все потоки ждут друг друга и отпускают запросы в один момент
    barrier = threading.Barrier(REQUESTS)

    def worker(number: int) -> None:
        payload = {
            "title": f"Гонка, запрос {number}",
            "club_id": 1,
            "room_id": ROOM_ID,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "capacity": 20,
        }
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.get("/api/health")  # соединение открыто заранее, на старте его не ждём
            barrier.wait()
            response = client.post("/api/events", json=payload)
        body = response.json()
        if response.status_code == 201:
            created_ids.append(body["id"])
        results.append((number, response.status_code, str(body.get("detail", ""))))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, REQUESTS + 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for number, code, text in sorted(results):
        print(f"  запрос {number}: {code} {text}")
    print("Итог:", dict(Counter(code for _, code, _ in results)))

    # Убираем за собой: удаляем созданное мероприятие, чтобы база не росла после каждого запуска.
    # (Новые мероприятия «на модерации», в афише их нет, поэтому удаляем по id из ответа.)
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        for event_id in created_ids:
            client.delete(f"/api/events/{event_id}")

if __name__ == "__main__":
    main()
