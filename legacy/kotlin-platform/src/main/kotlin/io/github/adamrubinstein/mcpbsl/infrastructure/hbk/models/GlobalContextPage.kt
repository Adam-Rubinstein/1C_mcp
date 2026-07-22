package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models

import io.github.adamrubinstein.mcpbsl.business.entities.MethodDefinition
import io.github.adamrubinstein.mcpbsl.business.entities.PropertyDefinition

class GlobalContextPage(
    val properties: List<PropertyDefinition>,
    val methods: List<MethodDefinition>,
)
