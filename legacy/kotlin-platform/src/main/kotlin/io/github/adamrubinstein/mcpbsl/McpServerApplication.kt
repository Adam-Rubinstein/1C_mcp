package io.github.adamrubinstein.mcpbsl

import kotlinx.cli.ArgParser
import kotlinx.cli.ArgType
import kotlinx.cli.default
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication

@SpringBootApplication
class McpServerApplication

fun main(args: Array<String>) {
    val parser = ArgParser("1C_mcp_bsl")

    val platformPath by parser.option(
        ArgType.String,
        shortName = "p",
        fullName = "platform-path",
        description = "Путь к каталогу платформы 1С",
    )
    val verbose by parser.option(
        ArgType.Boolean,
        shortName = "v",
        fullName = "verbose",
        description = "Включить отладочное логирование",
    )
    val mode by parser
        .option(
            ArgType.Choice(listOf("sse", "stdio"), { it }),
            shortName = "m",
            fullName = "mode",
            description = "Режим работы: sse (HTTP Server-Sent Events) или stdio (стандартный ввод/вывод)",
        ).default("stdio")
    val ssePort by parser.option(
        ArgType.Int,
        fullName = "port",
        description = "Порт для SSE сервера (по умолчанию 8080)",
    )

    parser.parse(args)

    // Настройка пути к платформе
    if (!platformPath.isNullOrBlank()) {
        System.setProperty("platform.context.path", platformPath as String)
    }

    // Настройка логирования
    if (verbose ?: false) {
        System.setProperty("logging.level.root", "DEBUG")
    }

    // Настройка режима работы
    val activeProfiles = mutableListOf<String>()

    when (mode) {
        "sse" -> {
            activeProfiles.add("sse")
            if (ssePort != null) {
                System.setProperty("server.port", ssePort.toString())
            }
        }

        "stdio" -> {
            activeProfiles.add("stdio")
        }
    }

    runApplication<McpServerApplication>(*args) {
        setDefaultProperties(mapOf("spring.profiles.active" to activeProfiles.joinToString(",")))
    }
}
