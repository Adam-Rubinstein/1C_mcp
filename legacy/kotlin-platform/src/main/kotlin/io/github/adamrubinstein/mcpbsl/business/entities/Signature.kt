package io.github.adamrubinstein.mcpbsl.business.entities

/**
 * Value Object: Сигнатура метода или свойства
 */
data class Signature(
    val name: String,
    val parameters: List<ParameterDefinition>,
    val description: String,
)
