package io.github.adamrubinstein.mcpbsl.business.persistent

import io.github.adamrubinstein.mcpbsl.business.entities.Definition
import io.github.adamrubinstein.mcpbsl.business.entities.MethodDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PlatformTypeDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PropertyDefinition
import io.github.adamrubinstein.mcpbsl.business.valueobjects.ApiType
import io.github.adamrubinstein.mcpbsl.business.valueobjects.SearchQuery

interface PlatformContextRepository {
    fun search(
        query: String,
        limit: Int,
        type: ApiType? = null,
    ): List<Definition>

    fun search(searchQuery: SearchQuery): List<Definition>

    fun findType(name: String): PlatformTypeDefinition?

    fun findProperty(name: String): PropertyDefinition?

    fun findMethod(name: String): MethodDefinition?

    fun findTypeMember(
        type: PlatformTypeDefinition,
        memberName: String,
    ): Definition?
}
