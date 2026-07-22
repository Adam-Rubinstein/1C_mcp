# 1C MCP Toolkit

Набор **MCP-серверов** для разработки на платформе **1С:Предприятие**.  
Позволяет агенту (Cursor и аналоги) и разработчику работать с конфигурацией и справкой API без ручного щёлканья в Конфигураторе на каждый шаг.

Репозиторий: [Adam-Rubinstein/1C_mcp](https://github.com/Adam-Rubinstein/1C_mcp)

## Что это такое

MCP (Model Context Protocol) — способ подключить к ИИ-редактору внешние «инструменты».  
В этом репозитории инструменты заточены под 1С:

- найти метод или тип в **справке платформы** (синтакс-помощник из `shcntx_ru.hbk`);
- **точечно выгрузить** объекты конфигурации из информационной базы в файлы (`src/cf`, `src/cfe`);
- **точечно загрузить** изменённые XML/BSL обратно в базу;
- искать по уже выгруженным файлам, гонять простой code-review, дергать COM и т.д.

Секреты (пути к базам, пароли, токены) **не хранятся в git** — только в локальном `.env` и настройках Cursor.

## Какие серверы входят

| Пакет | Зачем |
|-------|--------|
| **1c-platform** | Справка по API платформы: `search`, `info`, `getMember`, `getMembers`, `getConstructors` (чистый Python, читает HBK) |
| **1c-dump** | Частичная / инкрементальная выгрузка конфигурации из ИБ в файлы |
| **1c-load** | Частичная загрузка файлов в ИБ (`confirm=true` обязательно) |
| **1c-com** | Запросы и метаданные через COM (`V83.COMConnector`) |
| **1c-files** | Поиск и чтение по каталогам выгрузки (`REPO_CF` / `REPO_CFE`) |
| **1c-review** | Чеклист по BSL (паттерны из YAML) |
| **1c-journal** | Журнал регистрации через COM (best effort) |
| **1c-debug** | Клиент к HTTP-отладчику (`dbgs`); без сервера отладки честно пишет, что недоступен |
| **1c-bsl** | Статус/подсказка по BSL Language Server (сам LS ставится отдельно) |

Общий код: `packages/shared/onec_mcp_shared/`.

## Две информационные базы (рекомендуемый цикл)

Типичная схема на рабочей станции:

| Роль | Переменная | Назначение |
|------|------------|------------|
| **DEV** | `ONEC_IB_DEV` | Песочница (часто копия без хранилища). Сюда dump/load для проверки |
| **WORK** | `ONEC_IB_WORK` | Рабочая база (часто с хранилищем). Load только когда вы готовы |

По умолчанию dump и load идут в **dev**. В **work**: `load_objects(..., target="work", confirm=true)`.

### Монополия Конфигуратора

Точечный dump/load запускает **пакетный Конфигуратор** (`1cv8 DESIGNER`). На **файловой** ИБ второй Конфигуратор к той же базе не подключится.

- Правки только в файлах `src/cf` — монополия **не нужна**.
- Load в **dev**, пока вы сидите в Конфигураторе на **work** — обычно **ок**.
- Load в **ту же** базу, где открыт Конфигуратор — нужно закрыть его (или `manage_session=true`: сервер сам закроет 1С, зальёт, откроет снова с `/N` `/P`).

Если объекты в **хранилище** не захвачены, load вернёт `objectsToCapture` — не «тихий» успех.

## Быстрый старт

```bat
cd C:\Tools\1C_mcp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env
python scripts\smoke_test.py
python scripts\smoke_test.py --live-ib
```

В Cursor — пример [`mcp.json.example`](mcp.json.example): все серверы через

`python scripts/run_server.py <platform|dump|load|…>`

и переменные окружения из `.env`.

### Важные переменные

См. [`.env.example`](.env.example): `ONEC_BIN`, `ONEC_PLATFORM_PATH`, `ONEC_IB_DEV`, `ONEC_IB_WORK`, `ONEC_USER`, `ONEC_PASSWORD`, `ONEC_EXTENSION`, `REPO_CF`, `REPO_CFE`.

## Примеры

**Выгрузка одного документа из dev:**

`dump_objects(objects=["Document.МойДокумент"], target="dev", merge_into_repo=true)`

**Загрузка в песочницу:**

`load_objects(objects=["Document.МойДокумент"], confirm=true, target="dev")`

**Загрузка в work с авто-перезапуском 1С:**

`load_objects(..., confirm=true, target="work", manage_session=true)`

**Справка платформы:**

`search("Запрос")` → `info(name="Запрос", type="type")`

## Безопасность

- Не публикуйте `.env` и `mcp.json` с паролями/токенами.
- HTTP/SSE с `MCP_TOKEN` — только в доверенной сети; для рабочей станции удобнее **stdio**.
- MCP с доступом к ИБ = доступ к конфигурации и (через COM) к данным. Не открывайте это в интернет без авторизации.

## Документация

- [docs/GUIDE.md](docs/GUIDE.md) — подробности оператора
- [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) — чеклист настройки агентом

## Лицензия

См. файл `LICENSE` в корне репозитория.
