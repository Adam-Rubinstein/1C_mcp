package io.github.adamrubinstein.mcpbsl.business.entities

/**
 * Value Object: Определение параметра
 */
data class ParameterDefinition(
    val name: String,
    val type: String,
    val description: String,
    val required: Boolean = false,
    val defaultValue: String? = null,
)
