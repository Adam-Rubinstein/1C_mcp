# Документация MCP (частное использование)

| Документ | Содержание |
|----------|------------|
| [INSTALL.md](INSTALL.md) | Требования, откуда взять JAR, проверка из терминала, подключение в Cursor. |
| [cursor-user-rule-snippet.txt](cursor-user-rule-snippet.txt) | Одна вставка в **Cursor → Rules**, чтобы «установи mcp» работало в любом проекте. |
| `scripts/test/smoke-install-mcp.sh` | Smoke установки без реальной 1С (вызывается из CI и вручную из корня клона). |
| JUnit `io.github.adamrubinstein.mcpbsl.install.*` | Контракт разбора URL GitHub и структуры `mcp.json` (`./gradlew test --tests '…install.*'`). |
| [CLI.md](CLI.md) | Опции командной строки JAR, лог, кратко про инструменты и сборку. |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Типичные ошибки (PATH, PowerShell, путь к платформе). |
| [SOURCE.md](SOURCE.md) | Исходники сервера в корне репозитория (`src/`), сборка Gradle, обновление `dist/`. |

Короткий обзор и чеклист остаются в [корневом README.md](../README.md).
