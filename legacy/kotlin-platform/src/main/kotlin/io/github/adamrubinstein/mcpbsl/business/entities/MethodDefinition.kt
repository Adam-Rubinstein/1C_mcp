package io.github.adamrubinstein.mcpbsl.business.entities

/**
 * Доменная сущность, представляющая определение метода.
 * Содержит информацию о методе, его параметрах и сигнатуре.
 */
data class MethodDefinition(
    override val name: String,
    override val description: String,
    val returnType: String,
    val signature: List<Signature>,
) : Definition
