# Устранение неполадок

## MCP в Cursor красный, в логе «java» и нечитаемые символы

**Причина:** процесс Cursor **не видит** `java` в `PATH`.

**Решение:** в `.cursor/mcp.json` в поле `command` укажите **полный путь** к `java.exe` (Windows), например:

`C:/Program Files/Eclipse Adoptium/jdk-17.0.18.8-hotspot/bin/java.exe`

Перезапустите Cursor или выполните **Reload Window**.

## Ошибка PowerShell при ручном запуске JAR

Сообщение вроде `Could not find or load main class .encoding=UTF-8`.

**Причина:** в PowerShell аргумент `-Dfile.encoding=UTF-8` разбирается неверно.

**Решение:** передавайте его в кавычках:

`java '-Dfile.encoding=UTF-8' -jar ...`

В `mcp.json` каждый элемент массива `args` — отдельный аргумент; там кавычки не нужны.

## Справка пустая или ошибки чтения

**Проверьте** `--platform-path`: это должен быть **корень версии** платформы (где есть `docs`), а не `Program Files\1cv8` без номера версии.

## Инструменты в Cursor не совпадают с таблицей в README

Имена tools задаёт **конкретная версия JAR**. Смотрите список в **Settings → MCP** у сервера `1c-platform` и при необходимости обновите правило `.cursor/rules/use-1c-platform-mcp.mdc` под фактические имена.

## Версия Java

`java -version` должна показывать **17+**. Для сервера и для Cursor используйте один и тот же установленный JDK, путь к которому прописан в `command`, если `PATH` ненадёжен.
