package io.github.adamrubinstein.mcpbsl.infrastructure.persistent.storage

import io.github.adamrubinstein.mcpbsl.business.entities.MethodDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PlatformTypeDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PropertyDefinition
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.MethodInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.ObjectInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.PropertyInfo

fun MethodInfo.toEntity() =
    MethodDefinition(
        name = nameRu,
        description = description,
        returnType = returnValue?.type ?: "",
        signature = emptyList(),
    )

fun PropertyInfo.toEntity() =
    PropertyDefinition(
        name = nameRu,
        description = description,
        propertyType = typeName,
        isReadOnly = readonly,
    )

fun ObjectInfo.toEntity() =
    PlatformTypeDefinition(
        name = nameRu,
        description = description,
        methods = methods?.map(MethodInfo::toEntity) ?: emptyList(),
        properties = properties?.map(PropertyInfo::toEntity) ?: emptyList(),
        constructors = emptyList(),
    )
