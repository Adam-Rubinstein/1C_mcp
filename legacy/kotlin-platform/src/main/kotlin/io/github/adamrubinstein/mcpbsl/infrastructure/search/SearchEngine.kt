package io.github.adamrubinstein.mcpbsl.infrastructure.search

import io.github.adamrubinstein.mcpbsl.business.entities.Definition
import io.github.adamrubinstein.mcpbsl.business.entities.MethodDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PlatformTypeDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PropertyDefinition
import io.github.adamrubinstein.mcpbsl.business.valueobjects.SearchQuery
import io.github.adamrubinstein.mcpbsl.infrastructure.persistent.storage.PlatformContextStorage

interface SearchEngine {
    fun initialize(context: PlatformContextStorage)

    fun search(searchQuery: SearchQuery): List<Definition>

    fun findType(name: String): PlatformTypeDefinition?

    fun findProperty(name: String): PropertyDefinition?

    fun findMethod(name: String): MethodDefinition?

    fun findTypeMember(
        type: PlatformTypeDefinition,
        memberName: String,
    ): Definition?
}
