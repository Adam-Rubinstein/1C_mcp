package io.github.adamrubinstein.mcpbsl.infrastructure.hbk

import org.junit.jupiter.api.Test
import kotlin.io.path.Path

class PlatformContextReaderTest {
    @Test
    fun read() {
        val platformPath = System.getProperty("platform.context.path")
        val reader = PlatformContextReader()
        reader.read(Path(platformPath, "shcntx_ru.hbk")) {
            println("Types: ${types().count()}")
            println("Enums: ${enums().count()}")
            println("Global methods: ${globalMethods().count()}")
            println("Global properties: ${globalProperties().count()}")
        }
    }
}
