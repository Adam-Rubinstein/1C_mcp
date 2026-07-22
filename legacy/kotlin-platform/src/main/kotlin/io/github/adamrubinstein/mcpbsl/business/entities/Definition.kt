package io.github.adamrubinstein.mcpbsl.business.entities

sealed interface Definition {
    val name: String
    val description: String
}
