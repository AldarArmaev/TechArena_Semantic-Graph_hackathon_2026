# Статический solver Train Ticket

Решение читает `scenarios/`, `skeleton/`, Java-исходники в `targets/trainticket/` и SQL-снимок в `context/`. Оно не запускает Train Ticket и не использует runtime traces. Выход — четыре типа направленных рёбер (`executes`, `calls`, `remote`, `data`) для S0–S7.

Установка (Python 3.10+):

```powershell
python -m pip install -r solution/requirements.txt
python solution/solve.py --diagnostics solution/output/diagnostics.json
python platform/evaluate_golden.py gold/S0.json solution/output/answer.json
python -m unittest discover -s solution/tests -q
```

`solution/output/answer.json` содержит полный ответ и игнорируется Git. `--scenarios scenarios/S1.json` ограничивает прогон; `--output` меняет путь ответа; `--explain` выводит ключевые события; `--max-paths` задаёт предел альтернативных символических путей. Для диагностики каждого шага используйте JSON из `--diagnostics`: там сохранены выбранный запрос, входные значения, ветви, SQL, межсервисные вызовы, исключения и причина остановки.

`--audit solution/output/audit.json` создаёт компактный отчёт по каждому шагу: endpoint, actor/family, DTO, изменения состояния, HTTP/MQ/SQL, исключения, причины неопределённых ветвей и рёбра, поддержанные только частью допустимых путей. Это удобнее для поиска FP/FN, чем полный `--diagnostics`.

Для варианта отправки платформы **«Zip archive (source + submission.json)»** после полного прогона выполните `python solution/package_submission.py` и загрузите созданный `solution/output/techarena-submission.zip`. В корне архива лежит `submission.json`, а также код solver и зависимости. Архив с одним `answer.json` не соответствует показанному платформой формату.

Архитектура: `catalog.py` индексирует skeleton и Java AST; `sql.py` читает снимок БД; `planner.py` преобразует шаги сценария в запросы и связывает ответы/изменения состояния между шагами; `engine.py` исполняет Java AST символически; `adapters.py` моделирует Spring, HTTP, RabbitMQ, JPA и стандартную библиотеку; `events.py` проецирует события на разрешённые рёбра skeleton; `runner.py` управляет альтернативными путями. Подробности и актуальные ограничения — в [PROGRESS.md](../PROGRESS.md).

Неизвестные условия не превращаются автоматически в истину: движок исследует альтернативы с ограниченным бюджетом и берёт пересечение их рёбер. Путь, противоречащий явному наблюдению сценария (например, выбор существующего поезда при пустом результате поиска), помечается как несовместимый.
