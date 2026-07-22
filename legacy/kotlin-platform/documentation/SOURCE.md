# Исходники MCP‑сервера (корень репозитория)

Исходный код сервера (Kotlin, Spring, Gradle) лежит **в корне** этого репозитория (`src/`, `build.gradle.kts`, `gradlew*`). Его можно открывать в IDE, менять и **пересобирать** JAR.

Корневой пакет Kotlin: **`io.github.adamrubinstein.mcpbsl`** (точка входа — `McpServerApplication`).

## Сборка

Рабочий каталог — **корень клона** (там же, где `README.md` и `dist/`).

### Windows

```powershell
.\gradlew.bat build
```

### Linux / macOS

```bash
chmod +x gradlew
./gradlew build
```

После `bootJar` файл — **`build/libs/1C_mcp_bsl.jar`**. Скопируйте в **`dist/1C_mcp_bsl.jar`** для документации и `mcp.json`. Пример PowerShell из корня репозитория:

```powershell
Copy-Item -Force ".\build\libs\1C_mcp_bsl.jar" ".\dist\1C_mcp_bsl.jar"
```

## Зависимости

Нужны **JDK 17+** и доступ в интернет при первой сборке (Gradle подтянет зависимости). Каталоги **`build/`** и **`.gradle/`** не коммитятся (см. корневой `.gitignore`).
