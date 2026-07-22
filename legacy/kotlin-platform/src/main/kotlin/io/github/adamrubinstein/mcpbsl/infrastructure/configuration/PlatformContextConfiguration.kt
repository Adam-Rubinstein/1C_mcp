package io.github.adamrubinstein.mcpbsl.infrastructure.configuration

import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import io.github.adamrubinstein.mcpbsl.business.persistent.PlatformContextRepository
import io.github.adamrubinstein.mcpbsl.business.services.ResponseFormatterService
import io.github.adamrubinstein.mcpbsl.infrastructure.persistent.repositories.PlatformRepository
import io.github.adamrubinstein.mcpbsl.infrastructure.persistent.storage.PlatformContextLoader
import io.github.adamrubinstein.mcpbsl.infrastructure.persistent.storage.PlatformContextStorage
import io.github.adamrubinstein.mcpbsl.infrastructure.search.SearchEngine
import io.github.adamrubinstein.mcpbsl.infrastructure.search.SimpleSearchEngine
import io.github.adamrubinstein.mcpbsl.presentation.formatters.MarkdownFormatterService
import java.nio.file.Path

@Configuration
class PlatformContextConfiguration {
    @Bean
    fun platformContextRepository(
        searchEngine: SearchEngine,
        storage: PlatformContextStorage,
    ): PlatformContextRepository = PlatformRepository(searchEngine, storage)

    @Bean
    fun searchEngine(storage: PlatformContextStorage): SearchEngine = SimpleSearchEngine(storage)

    @Bean
    fun contextStorage(
        loader: PlatformContextLoader,
        @Value("\${platform.context.path}") platformPath: Path,
    ): PlatformContextStorage = PlatformContextStorage(loader, platformPath)

    @Bean
    fun contextLoader(): PlatformContextLoader = PlatformContextLoader()

    @Bean
    fun formatter(): ResponseFormatterService = MarkdownFormatterService()
}
