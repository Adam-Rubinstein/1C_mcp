package io.github.adamrubinstein.mcpbsl.infrastructure.persistent.repositories

import io.github.adamrubinstein.mcpbsl.business.entities.Definition
import io.github.adamrubinstein.mcpbsl.business.entities.PlatformTypeDefinition
import io.github.adamrubinstein.mcpbsl.business.persistent.PlatformContextRepository
import io.github.adamrubinstein.mcpbsl.business.valueobjects.ApiType
import io.github.adamrubinstein.mcpbsl.business.valueobjects.SearchQuery
import io.github.adamrubinstein.mcpbsl.infrastructure.persistent.storage.PlatformContextStorage
import io.github.adamrubinstein.mcpbsl.infrastructure.search.SearchEngine

class PlatformRepository(
    val searchEngine: SearchEngine,
    val context: PlatformContextStorage,
) : PlatformContextRepository {
    override fun search(
        query: String,
        limit: Int,
        type: ApiType?,
    ): List<Definition> =
        search(
            SearchQuery(
                query = query,
                apiType = type,
                maxResults = limit,
            ),
        )

    override fun search(searchQuery: SearchQuery) = searchEngine.search(searchQuery)

    override fun findType(name: String) = searchEngine.findType(name)

    override fun findProperty(name: String) = searchEngine.findProperty(name)

    override fun findMethod(name: String) = searchEngine.findMethod(name)

    override fun findTypeMember(
        type: PlatformTypeDefinition,
        memberName: String,
    ) = searchEngine.findTypeMember(type, memberName)
}
