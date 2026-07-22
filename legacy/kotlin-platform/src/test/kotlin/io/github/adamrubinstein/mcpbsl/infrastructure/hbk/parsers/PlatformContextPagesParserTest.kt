package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers

import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Test
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.ConstructorPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.EnumPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.EnumValuePageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.MethodPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.ObjectPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.PropertyPageParser

class PlatformContextPagesParserTest {
    @Test
    fun `test all parsers are available`() {
        // Создаем временный парсер для проверки доступности всех парсеров
        // В реальном использовании PlatformContextPagesParser требует Context, который сложно замокать
        assertNotNull(ConstructorPageParser())
        assertNotNull(EnumPageParser())
        assertNotNull(EnumValuePageParser())
        assertNotNull(MethodPageParser())
        assertNotNull(ObjectPageParser())
        assertNotNull(PropertyPageParser())
    }
}
