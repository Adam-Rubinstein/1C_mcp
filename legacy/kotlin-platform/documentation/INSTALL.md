# Установка MCP-сервера справки 1С для Cursor

Документ для **личного** использования: пошаговая установка.

## 0. Самое быстрое (авто)

**В Cursor** можно написать ассистенту: **«установи mcp»** и при необходимости **URL репозитория** (по умолчанию **`https://github.com/Adam-Rubinstein/1C_mcp_bsl`**). Агент подставит корень вашего workspace и запустит скрипт (см. **`.cursor/rules/use-1c-platform-mcp.mdc`**).

Из **терминала**, в **корне клона** этого репозитория:

- **Windows:** `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-mcp.ps1`
- **Linux / macOS:** `bash scripts/install-mcp.sh` (нужны `curl`, `python3`; 1С — типичный путь `/opt/1cv8/...`)

В **другом проекте** (где нет клона) на **Windows** (нужны `git` и сеть), подставив абсолютный путь к корню проекта вместо `WS`:

```powershell
$f = Join-Path $env:TEMP "install-mcp-1c.ps1"; irm "https://raw.githubusercontent.com/Adam-Rubinstein/1C_mcp_bsl/main/scripts/install-mcp.ps1" -OutFile $f; powershell -NoProfile -ExecutionPolicy Bypass -File $f -GitHubUrl "https://github.com/Adam-Rubinstein/1C_mcp_bsl" -WorkspaceRoot "WS"
```

Скрипт создаёт **`.cursor/mcp.json`**, копирует **`use-1c-platform-mcp.mdc`** и **`mcp.json.example`**. Дальше: **Reload Window**. Если автообнаружение 1С не сработало — разделы ниже и ручное заполнение по **`mcp.json.example`**.

**Переменные для `install-mcp.sh`:** `INSTALL_MCP_GITHUB_URL`, `INSTALL_MCP_WORKSPACE_ROOT`, `INSTALL_MCP_SOURCE_ROOT`, **`INSTALL_MCP_JAR_PATH`**, **`INSTALL_MCP_PLATFORM_PATH`** (последние два — для CI и нестандартных путей), **`INSTALL_MCP_PYTHON`** — явный интерпретатор, если `python3` в PATH оказался заглушкой (Windows Store). Полный список параметров PowerShell и переменных — в **[корневом README](../README.md)** (раздел **«Скрипты install-mcp»**).

**Проверки в репозитории:** JUnit `io.github.adamrubinstein.mcpbsl.install.*`, smoke **`bash scripts/test/smoke-install-mcp.sh`** (из корня клона).

## 1. Требования

| Компонент | Минимум |
|-----------|---------|
| ОС | Windows, Linux или macOS |
| Java | JDK **17+** (64-bit) |
| 1С:Предприятие | Платформа **8.3** (рекомендуется не ниже 8.3.20), установленная на машине |
| Cursor | Версия с поддержкой MCP (stdio) |
| Файл сервера | Один **fat JAR** (`1C_mcp_bsl.jar`) |

## 2. Откуда взять JAR

1. **Из репозитория (клон или ZIP с GitHub)** — файл **`dist/1C_mcp_bsl.jar`** уже лежит в дереве проекта; скопируйте его в удобный каталог (см. ниже).
2. **[С GitHub Releases](https://github.com/Adam-Rubinstein/1C_mcp_bsl/releases)** — скачайте вложение **`1C_mcp_bsl.jar`** у нужного тега (например **[v1.0.0](https://github.com/Adam-Rubinstein/1C_mcp_bsl/releases/tag/v1.0.0)**), если не хотите клонировать репозиторий.
3. **Сборка у себя** — из **корня** репозитория: `.\gradlew.bat bootJar` (Windows) или `./gradlew bootJar` (Linux/macOS); результат — **`build/libs/1C_mcp_bsl.jar`** (см. [SOURCE.md](SOURCE.md)).
4. **Уже лежит на ПК** — укажите полный путь в `mcp.json`.

**Про Releases:** новые версии в разделе **Releases** появляются, когда в репозитории включены **GitHub Actions** и запушен тег вида **`v*.*.*`** (workflow **`release-jar.yml`** собирает JAR и прикрепляет к релизу). Если Actions выключены, JAR по-прежнему можно взять из **`dist/`** или собрать локально.

Положите файл в каталог **без кириллицы** в пути, например:

`C:\Tools\1C_mcp_bsl\1C_mcp_bsl.jar`

Путь (и имя файла) в `mcp.json` должны **точно** указывать на ваш JAR на диске; в этом репозитории стабильное имя артефакта — **`1C_mcp_bsl.jar`**.

## 3. Проверка JAR из терминала

### Windows (PowerShell)

```powershell
java '-Dfile.encoding=UTF-8' -jar "C:\Tools\1C_mcp_bsl\1C_mcp_bsl.jar" --platform-path "C:\Program Files\1cv8\8.3.27.1719" --help
```

В PowerShell строку **`-Dfile.encoding=UTF-8`** обязательно брать **в кавычках**.

### Linux / macOS (пример)

```bash
java -Dfile.encoding=UTF-8 -jar /opt/mcp/1C_mcp_bsl.jar --platform-path "/opt/1cv8/x86_64/8.3.25.1257" --help
```

`--platform-path` — **корень** установки версии платформы (рядом должны быть каталоги вроде `bin`, `docs`).

Ожидается вывод справки по опциям без ошибки JVM.

## 4. Подключение в Cursor

1. Скопируйте [`.cursor/mcp.json.example`](../.cursor/mcp.json.example) в **`.cursor/mcp.json`** в корне того проекта, где работаете (или в глобальную конфигурацию MCP — по правилам Cursor).
2. Подставьте:
   - полный путь к **`java.exe`** (на Windows для Cursor часто нужен **абсолютный** путь, не только `java`);
   - полный путь к **JAR**;
   - **`--platform-path`** на ваш каталог платформы.
3. Положите [`.cursor/rules/use-1c-platform-mcp.mdc`](../.cursor/rules/use-1c-platform-mcp.mdc) в `.cursor/rules/` проекта с BSL.
4. **Developer: Reload Window** → **Settings → MCP** → сервер **`1c-platform`** должен быть зелёным.

Подробнее про опции JAR: [CLI.md](CLI.md).

## 5. Что не входит в эту установку

- Установка самой платформы 1С и лицензий на неё.
- Установка Cursor.
- Прикладная конфигурация / выгрузки ERP — в этом репозитории не версионируются.
