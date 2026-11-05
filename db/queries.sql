-- Запросы к практике 3. Ожидаемые результаты даны для демо-данных из seed.sql.


-- 1. Клубы и число их опубликованных мероприятий.
-- LEFT JOIN оставляет клубы без мероприятий; условие status = 'published' стоит именно в ON,
-- а не в WHERE: иначе строки с NULL отфильтруются и клуб без афиши пропадёт из результата.
-- count(e.id) не считает NULL, поэтому для такого клуба получается 0.
SELECT c.name,
       count(e.id) AS published
FROM clubs c
         LEFT JOIN events e ON e.club_id = c.id AND e.status = 'published'
GROUP BY c.id, c.name
ORDER BY published DESC, c.name;


-- 2. Организаторы и клубы, которые они ведут, — одной строкой через запятую, по алфавиту.
-- string_agg склеивает названия клубов внутри группы (по организатору),
-- ORDER BY внутри агрегата задаёт порядок склейки.
SELECT u.full_name,
       string_agg(c.name, ', ' ORDER BY c.name) AS clubs
FROM users u
         JOIN club_organizers co ON co.user_id = u.id
         JOIN clubs c ON c.id = co.club_id
GROUP BY u.id, u.full_name
ORDER BY u.full_name;


-- 3. Активные студенты: две и больше активных записей, и число этих записей.
-- Отменённые записи отсекаются до группировки (WHERE/ON), а фильтр по числу —
-- после неё, через HAVING: условие на агрегат в WHERE написать нельзя.
SELECT u.full_name,
       count(*)
FROM users u
         JOIN registrations r ON r.user_id = u.id AND r.status = 'active'
GROUP BY u.id, u.full_name
HAVING count(*) >= 2
ORDER BY u.full_name;


-- 4. Загрузка аудиторий в ноябре 2026: сколько часов аудитория занята мероприятиями
-- на модерации и опубликованными (отменённые и отклонённые не считаются).
-- Границы месяца заданы со смещением +07, поэтому результат не зависит от часового пояса сессии.
-- Мероприятие, которое выходит за границу месяца, учитывается только той частью, что попала
-- в ноябрь: GREATEST/LEAST обрезают интервал по границам.
-- Условия на мероприятие стоят в ON, чтобы аудитории без мероприятий остались в результате;
-- coalesce превращает NULL в 0. Разность timestamptz — интервал, extract(epoch ...) — секунды.
SELECT r.name,
       round(
               coalesce(
                       sum(extract(epoch FROM (
                           least(e.ends_at, timestamptz '2026-12-01 00:00+07')
                               - greatest(e.starts_at, timestamptz '2026-11-01 00:00+07')
                           ))),
                       0
               ) / 3600.0,
               2
       ) AS hours
FROM rooms r
         LEFT JOIN events e
                   ON e.room_id = r.id
                       AND e.status IN ('pending', 'published')
                       AND e.starts_at < timestamptz '2026-12-01 00:00+07'
                       AND e.ends_at > timestamptz '2026-11-01 00:00+07'
GROUP BY r.id, r.name
ORDER BY hours DESC, r.name;


-- 5. Мероприятия с тегом «IT» по времени начала.
-- Идём через связующую таблицу event_tags: мероприятие -> его теги -> нужный тег.
SELECT e.id,
       e.title,
       e.starts_at
FROM events e
         JOIN event_tags et ON et.event_id = e.id
         JOIN tags t ON t.id = et.tag_id
WHERE t.name = 'IT'
ORDER BY e.starts_at;
