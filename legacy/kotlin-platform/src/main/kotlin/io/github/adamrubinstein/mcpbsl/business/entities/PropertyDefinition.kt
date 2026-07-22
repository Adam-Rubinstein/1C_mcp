package io.github.adamrubinstein.mcpbsl.business.entities

/**
 * Доменная сущность, представляющая определение свойства.
 * Содержит информацию о свойстве, его типе и сигнатуре.
 */
data class PropertyDefinition(
    override val name: String,
    override val description: String,
    val propertyType: String,
    val isReadOnly: Boolean = false,
) : Definition
