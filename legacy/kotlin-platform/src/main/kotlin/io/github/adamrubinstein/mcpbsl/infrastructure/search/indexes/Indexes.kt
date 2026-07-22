package io.github.adamrubinstein.mcpbsl.infrastructure.search.indexes

import io.github.adamrubinstein.mcpbsl.business.entities.MethodDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PlatformTypeDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PropertyDefinition

class Indexes(
    val properties: Index<PropertyDefinition>,
    val methods: Index<MethodDefinition>,
    val types: Index<PlatformTypeDefinition>,
) {
    fun getProperties(key: String) = properties.get(key)

    fun getMethods(key: String) = methods.get(key)

    fun getTypes(key: String) = types.get(key)
}
