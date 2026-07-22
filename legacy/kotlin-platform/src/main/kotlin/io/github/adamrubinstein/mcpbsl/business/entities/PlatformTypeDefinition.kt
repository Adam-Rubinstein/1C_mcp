package io.github.adamrubinstein.mcpbsl.business.entities

/**
 * Доменная сущность, представляющая определение типа платформы.
 * Содержит информацию о типе API, методах и свойствах.
 */
data class PlatformTypeDefinition(
    override val name: String,
    override val description: String,
    val methods: List<MethodDefinition>,
    val properties: List<PropertyDefinition>,
    val constructors: List<Signature>,
) : Definition {
    /**
     * Проверяет, содержит ли тип методы
     */
    fun hasMethods(): Boolean = methods.isNotEmpty()

    /**
     * Проверяет, содержит ли тип свойства
     */
    fun hasProperties(): Boolean = properties.isNotEmpty()
}
